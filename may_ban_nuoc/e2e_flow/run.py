"""Terminal 5: control scenarios using four independently started services."""
import argparse
import errno
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import sys
import time
import traceback
from urllib.parse import quote

from .common import EventClient, load_config, request, utc_now
from .evaluation import evaluate, speech_scores

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent


def check_port_available(name, port):
    with socket.socket() as sock:
        # Match HTTPServer's reuse policy: closed connections in TIME_WAIT
        # must not prevent an immediate restart. An active listener still fails.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            hint = "Stop the existing service or change the port in e2e_flow/config.json"
            raise RuntimeError(f"Port {name} ({port}) occupied. {hint}") from exc


def validate(cfg, scenarios):
    ports = cfg["ports"]
    if set(ports) != {"voice", "log", "rasa", "action"} or len(set(ports.values())) != 4:
        raise ValueError("Four distinct ports required")
    if not all(isinstance(p, int) and 0 < p < 65536 for p in ports.values()):
        raise ValueError("Invalid port")
    for key in ("cooldown_seconds", "sample_interval_seconds", "startup_timeout_seconds",
                "operation_timeout_seconds", "rasa_timeout_seconds", "speech_wait_seconds", "max_record_seconds"):
        if cfg[key] <= 0:
            raise ValueError(key + " must be positive")
    if cfg["cooldown_seconds"] < 2:
        raise ValueError("cooldown_seconds must be at least 2")
    if cfg["sample_rate"] not in (8000, 16000, 32000, 48000):
        raise ValueError("Unsupported VAD sample rate")
    if not 0 < cfg["volume"] <= 1 or cfg["vad_aggressiveness"] not in range(4):
        raise ValueError("Invalid volume or VAD aggressiveness")
    if cfg["vad_silence_ms"] <= 0 or cfg["vad_min_speech_ms"] <= 0:
        raise ValueError("VAD durations must be positive")
    if cfg.get("vad_calibration_seconds", .9) <= 0 or cfg.get("vad_noise_margin_db", 8) <= 0:
        raise ValueError("VAD calibration duration and noise margin must be positive")
    if not -100 <= cfg.get("vad_min_dbfs", -55) < 0:
        raise ValueError("vad_min_dbfs must be between -100 and 0 (exclusive of 0)")
    ids = set()
    if not scenarios["sessions"]:
        raise ValueError("At least one session required")
    for session in scenarios["sessions"]:
        sid = session["id"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", sid) or sid in ids:
            raise ValueError("Session IDs must be unique safe names")
        ids.add(sid)
        if not session["turns"]:
            raise ValueError("Empty session")
        for turn in session["turns"]:
            if not turn["user_text"].strip() or not turn["expect"].get("intent"):
                raise ValueError("Each turn requires text and expected intent")


class Runner:
    def __init__(self, cfg, scenarios, output):
        self.cfg, self.scenarios, self.output = cfg, scenarios, Path(output)
        self.run_id = self.output.name
        self.urls = {name: f"http://127.0.0.1:{port}" for name, port in cfg["ports"].items()}
        self.connected = False
        self.voice_connected = False
        self.events = EventClient(self.urls["log"], "runner", self.run_id, output, background=True)
        self.report = {"run_id": self.run_id, "started_at": utc_now(), "status": "RUNNING",
                       "sessions": [], "log_complete": False,
                       "scope": "real speaker/microphone; existing Rasa/actions/database"}

    def wait_health(self, name, path):
        print(f"Waiting for {name} to become ready...", flush=True)
        waiting_since = time.monotonic()
        next_update = waiting_since + 10
        deadline = time.monotonic() + self.cfg["startup_timeout_seconds"]
        last = "not ready"
        while time.monotonic() < deadline:
            try:
                result = request(self.urls[name]+path, timeout=2)
                if result.get("ready", True):
                    self.events.emit("process.ready", {"name": name, "health": result})
                    print(f"{name}: ready ({time.monotonic()-waiting_since:.1f}s)", flush=True)
                    return result
            except Exception as exc:
                last = str(exc)
            if time.monotonic() >= next_update:
                print(f"Still waiting for {name} ({time.monotonic()-waiting_since:.0f}s); check its terminal", flush=True)
                next_update = time.monotonic() + 10
            time.sleep(.3)
        raise TimeoutError(f"{name} startup timed out: {last}")

    def voice_command(self, command, context, **data):
        # A transport timeout is ambiguous: NEVER retry a business command.
        self.events.emit('voice.command.sent', {'command': command, **data}, context)
        result = request(self.urls["voice"]+"/"+command, dict(context=context, **data), timeout=10)
        self.events.emit('voice.command.accepted', {'command': command, 'response': result}, context)
        return result

    def wait_voice(self, state, context):
        deadline = time.monotonic()+self.cfg["operation_timeout_seconds"]
        while time.monotonic() < deadline:
            status = request(self.urls["voice"]+"/status", timeout=3)
            if status["context"] != context:
                raise RuntimeError("Voice returned mismatched turn")
            if status["state"] == "ERROR":
                raise RuntimeError("Voice: " + status["error"])
            if status["state"] == state:
                return status
            time.sleep(.05)
        raise TimeoutError("Voice timed out waiting for " + state)

    def startup(self):
        health = request(self.urls['log']+'/health')
        if health['output'] != str(self.output.resolve()):
            raise RuntimeError('All five terminals must use the same --output directory')
        self.connected = True
        request(self.urls['log']+'/register', {'name': 'runner', 'pid': os.getpid()})
        for name in ('action', 'rasa', 'voice'):
            details = self.wait_health(name, '/e2e/health' if name != 'voice' else '/health')
            if details.get('output') != str(self.output.resolve()):
                raise RuntimeError(name + ' uses a different --output directory')
            if name == 'voice':
                self.voice_connected = True
        status = self.wait_health('rasa', '/status')
        if not status.get('model_id') and not status.get('model_file'):
            raise RuntimeError('Rasa has no loaded model')

    def turn(self, spec, context, entry):
        self.events.emit("turn.started", {"user_text": spec["user_text"]}, context)
        self.voice_command("prepare", context, text=spec["user_text"])
        self.wait_voice("PREPARED", context)
        self.voice_command("arm", context)
        self.wait_voice("LISTENING", context)
        self.voice_command("prompt", context)
        captured = self.wait_voice("CAPTURED", context)["result"]
        entry.update(captured)
        entry.update(speech_scores(spec["user_text"], captured["transcript"]))
        body = {"sender": context["session_id"], "message": captured["transcript"], "metadata": context}
        self.events.emit("rasa.request.sent", body, context)
        responses = request(self.urls["rasa"]+"/webhooks/rest/webhook", body, self.cfg["rasa_timeout_seconds"])
        entry["responses"] = responses
        self.events.emit("rasa.response.received", {"responses": responses}, context)
        tracker = request(self.urls["rasa"]+"/conversations/"+quote(context["session_id"], safe="")+"/tracker?include_events=ALL")
        entry["checks"] = evaluate(spec, tracker, responses, context)
        texts = [r["text"] for r in responses if r.get("text", "").strip()]
        if not texts:
            raise RuntimeError("Rasa returned no speakable response")
        self.voice_command("speak", context, texts=texts)
        finished = self.wait_voice("DONE", context)["result"]
        entry["status"] = "PASS" if all(c["passed"] for c in entry["checks"]) else "FAIL"
        self.events.emit("turn.completed", {"status": entry["status"], "checks": entry["checks"]}, context)
        completed = finished["bot_playback_completed_monotonic"]
        self.events.emit("cooldown.started", {"seconds": self.cfg["cooldown_seconds"]}, context)
        while time.monotonic()-completed < self.cfg["cooldown_seconds"]:
            time.sleep(min(.1, max(0, self.cfg["cooldown_seconds"]-(time.monotonic()-completed))))
        self.events.emit("cooldown.completed", {"elapsed_since_playback": time.monotonic()-completed}, context)
        if entry["status"] != "PASS":
            failures = [c for c in entry["checks"] if not c["passed"]]
            detail = "; ".join(f"{c['name']}: expected={c['expected']!r}, actual={c['actual']!r}" for c in failures)
            print("STT:", captured["transcript"], flush=True)
            print("Bot:", " | ".join(texts), flush=True)
            raise AssertionError("Turn assertions failed: " + detail + ". Remaining turns skipped.")

    def run_sessions(self):
        for session in self.scenarios["sessions"]:
            sid = self.run_id + "_" + session["id"]
            result = {"id": session["id"], "sender_id": sid, "status": "RUNNING", "turns": []}
            self.report["sessions"].append(result)
            self.events.emit("session.started", {"description": session.get("description", "")}, {"session_id": sid})
            if self.events.flush():
                raise RuntimeError('Logging server did not acknowledge session start')
            try:
                for index, spec in enumerate(session["turns"], 1):
                    context = {"session_id": sid, "turn_id": f"turn_{index:03d}"}
                    entry = {"turn_id": context["turn_id"], "user_text": spec["user_text"], "status": "RUNNING"}
                    result["turns"].append(entry)
                    print(f"[{session['id']} {index}/{len(session['turns'])}] {spec['user_text']}", flush=True)
                    try:
                        self.turn(spec, context, entry)
                    except BaseException as exc:
                        entry.update(status="FAIL", error=str(exc))
                        raise
                result["status"] = "PASS"
            except AssertionError as exc:
                result.update(status="FAIL", error=str(exc))
                print(f"Session {sid} failed; continuing with the next independent session.", flush=True)
            except BaseException:
                result["status"] = "FAIL"
                raise
            finally:
                self.events.emit("session.completed", {"status": result['status']}, {"session_id": sid})
                self.flush_services()
                request(self.urls['log']+'/session/close', {'session_id': sid})

    def flush_services(self):
        if self.events.flush():
            raise RuntimeError('Controller events are still pending')
        for name in ('rasa', 'action'):
            response = request(self.urls[name]+'/e2e/flush', {}, timeout=5)
            if response['pending_events']:
                raise RuntimeError(name + ' events are still pending')
        if self.voice_connected:
            deadline = time.monotonic()+10
            while True:
                response = request(self.urls['voice']+'/flush', {}, timeout=5)
                if not response.get('workers_active') and not response['pending_events']:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('Voice has unfinished work or undelivered events')
                time.sleep(.1)

    def finish(self):
        self.mark_skipped()
        if not self.connected:
            self.save_summary()
            return
        complete = False
        try:
            if self.voice_connected and self.report['status'] != 'PASS':
                request(self.urls['voice']+'/cancel', {}, timeout=3)
            self.flush_services()
            self.events.emit('test.completed', {'status': self.report['status'], 'error': self.report.get('error')})
            if self.events.flush():
                raise RuntimeError('Final events were not acknowledged')
            complete = request(self.urls['log']+'/finalize', {}, timeout=30)['complete']
        except Exception as exc:
            print('Log incomplete:', exc, file=sys.stderr)
            try:
                request(self.urls['log']+'/finalize', {'error': str(exc)}, timeout=30)
            except Exception:
                pass
        self.report['log_complete'] = complete
        if not complete and self.report['status'] == 'PASS':
            self.report['status'] = 'INVALID'

        self.save_summary()

    def save_summary(self):
        self.report['completed_at'] = utc_now()
        (self.output/'summary.json').write_text(json.dumps(self.report, ensure_ascii=False, indent=2)+'\n')

    def mark_skipped(self):
        for session in self.scenarios["sessions"]:
            found = next((r for r in self.report["sessions"] if r["id"] == session["id"]), None)
            if found is None:
                found = {"id": session["id"], "status": "SKIPPED", "turns": []}
                self.report["sessions"].append(found)
            for index in range(len(found["turns"]), len(session["turns"])):
                found["turns"].append({"turn_id": f"turn_{index+1:03d}", "status": "SKIPPED",
                                       "user_text": session["turns"][index]["user_text"]})


def main():
    test_started_ns, test_started_utc = time.monotonic_ns(), utc_now()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE/"config.json")
    parser.add_argument("--sessions", type=Path, default=HERE/"sessions.json")
    parser.add_argument("--output", type=Path, default=HERE/"runs"/"current")
    parser.add_argument("--check", action="store_true", help="Validate files/dependencies only; no servers or audio")
    args = parser.parse_args()
    cfg, scenarios = load_config(args.config), load_config(args.sessions)
    validate(cfg, scenarios)
    missing = [name for name in ("rasa", "rasa_sdk", "psutil", "sounddevice", "piper", "faster_whisper", "webrtcvad")
               if importlib.util.find_spec(name) is None]
    if missing:
        parser.error("Missing dependencies: " + ", ".join(missing))
    if args.check:
        print(f"Config and {len(scenarios['sessions'])} sessions valid; dependencies present.")
        print("Piper model exists:", Path(cfg["piper_model"]).expanduser().is_file())
        print("Hardware, cached Whisper model and Rasa dialogue model are not exercised by --check.")
        return 0
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.glob('*.log')):
        parser.error('Output already contains a test run; use a fresh --output in all terminals')
    health = request(f"http://127.0.0.1:{cfg['ports']['log']}/health")
    if not health['ready'] or health['output'] != str(output):
        parser.error('Start logging server with the same fresh --output first')
    (output/'config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2)+'\n')
    (output/'sessions.json').write_text(json.dumps(scenarios, ensure_ascii=False, indent=2)+'\n')
    runner = Runner(cfg, scenarios, output)
    runner.events.emit('test.started', {'sample_interval_ms': cfg['sample_interval_seconds']*1000},
                       {'time': test_started_utc, 'monotonic_ns': test_started_ns})
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Received signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    print("Output:", output, flush=True)
    try:
        runner.startup()
        runner.run_sessions()
        runner.report["status"] = "PASS" if all(s['status']=='PASS' for s in runner.report['sessions']) else "FAIL"
    except BaseException as exc:
        runner.report.update(status="FAIL", error=str(exc), traceback=traceback.format_exc())
        print("Run failed:", exc, file=sys.stderr)
    finally:
        runner.finish()
    print("Result:", runner.report["status"], "—", output)
    return 0 if runner.report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
