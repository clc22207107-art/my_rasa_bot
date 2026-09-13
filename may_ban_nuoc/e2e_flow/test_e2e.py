"""Optional code checks; not part of the five-terminal application.

Run from the project directory: python -m unittest e2e_flow.test_e2e -v
Uses fake audio and isolated temporary files. No models or business database.
"""

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from e2e_flow.common import APIError
from e2e_flow.evaluation import evaluate, speech_scores
from e2e_flow.run import Runner, validate
from e2e_flow.voice_server import VoiceServer
from e2e_flow.common import EventClient
from e2e_flow.log_server import LogServer
import errno
import socket
from e2e_flow.run import check_port_available
import asyncio
from types import SimpleNamespace
from e2e_flow.support.report import export_delays, calculate_events
from e2e_flow.support.runtime import instrument_http, instrument_method, CONTEXT
import threading
import types
import numpy as np
from e2e_flow.vad_gate import NoiseGate, dbfs
from e2e_flow.voice_server import AudioBackend


# Dialogue flow

HERE = Path(__file__).resolve().parent


class Events:
    def __init__(self):
        self.records = []

    def emit(self, name, data=None, context=None, level="INFO"):
        self.records.append((name, data, context))

    def flush(self):
        return 0


class Backend:
    def __init__(self):
        self.spoken = []
        self.recordings = 0

    def synthesize(self, text, role, context, index=0):
        return text

    def play(self, audio, role, context, cancel, index=0):
        self.spoken.append((role, audio))
        return time.monotonic(), time.monotonic()

    def record(self, ready, speech, prompt_started, prompt_done, cancel, context):
        self.recordings += 1
        ready()
        if not prompt_started.wait(1):
            raise TimeoutError("No prompt")
        speech()
        if not prompt_done.wait(1):
            raise TimeoutError("Prompt not completed")
        return [0]*1600, time.monotonic()

    def transcribe(self, pcm, context):
        return "recognized microphone text", 1.0


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((HERE/"config.json").read_text())
        self.backend, self.events = Backend(), Events()
        self.voice = VoiceServer(self.cfg, self.events, self.backend)
        self.context = {"session_id": "s1", "turn_id": "t1"}

    def wait(self, state):
        deadline = time.monotonic()+2
        while time.monotonic() < deadline:
            status = self.voice.dispatch("GET", "/status", {})
            if status["state"] == state:
                return status
            if status["state"] == "ERROR":
                self.fail(status["error"])
            time.sleep(.005)
        self.fail("State did not become " + state)

    def command(self, name, **data):
        return self.voice.dispatch("POST", "/"+name, dict(context=self.context, **data))

    def capture(self):
        self.command("prepare", text="reference sentence")
        self.wait("PREPARED")
        self.command("arm")
        self.wait("LISTENING")
        self.command("prompt")
        return self.wait("CAPTURED")

    def test_sequence_transcript_and_multiple_responses(self):
        captured = self.capture()
        self.assertEqual(captured["result"]["transcript"], "recognized microphone text")
        with self.assertRaises(APIError):
            self.command("arm")
        self.command("speak", texts=["reply one", "reply two"])
        self.wait("DONE")
        self.assertEqual(self.backend.spoken, [("user_prompt", "reference sentence"),
                                             ("bot_response", "reply one"), ("bot_response", "reply two")])
        self.assertEqual(self.backend.recordings, 1)
        self.assertEqual(sum(name == "bot.playback.completed" for name, _, _ in self.events.records), 1)
        with self.assertRaises(APIError):
            self.voice.dispatch("POST", "/prepare", {"context": {"session_id": "s1", "turn_id": "t2"}, "text": "next"})
        self.voice.last_playback -= 3
        with self.assertRaises(APIError):
            self.command("prepare", text="duplicate")

    def test_invalid_order_and_stale_commands(self):
        with self.assertRaises(APIError):
            self.command("prompt")
        self.capture()
        with self.assertRaises(APIError):
            self.voice.dispatch("POST", "/speak", {"context": {"session_id": "other", "turn_id": "t1"}, "texts": ["bad"]})
        with self.assertRaises(APIError):
            self.command("speak", texts=[])

    def test_empty_stt_is_error_never_reference_fallback(self):
        self.backend.transcribe = lambda pcm, context: ("", 1)
        self.command("prepare", text="reference sentence")
        self.wait("PREPARED")
        self.command("arm")
        self.wait("LISTENING")
        self.command("prompt")
        deadline = time.monotonic()+2
        while time.monotonic() < deadline and self.voice.state != "ERROR":
            time.sleep(.005)
        self.assertEqual(self.voice.state, "ERROR")
        self.assertIsNone(self.voice.result)

    def test_runner_routes_stt_not_reference_and_waits_for_playback(self):
        tracker = {"sender_id": "s1", "latest_message": {"metadata": self.context, "intent": {"name": "greet"}},
                   "events": [{"event": "user", "metadata": self.context}, {"event": "action", "name": "utter_greet"}],
                   "slots": {"cart": None}}
        sent = []
        def transport(url, data=None, timeout=10):
            if "/webhooks/rest/webhook" in url:
                self.assertEqual(self.voice.state, "CAPTURED")
                sent.append(data)
                return [{"text": "Hello back"}, {"text": "Welcome"}]
            if "/tracker?" in url:
                return tracker
            path = "/" + url.split("/", 3)[3]
            return self.voice.dispatch("GET" if data is None else "POST", path, data or {})
        with tempfile.TemporaryDirectory() as output:
            runner = Runner(self.cfg, {"sessions": []}, output)
            runner.events = self.events
            real_play = self.backend.play
            def past_play(*args, **kwargs):
                a, b = real_play(*args, **kwargs)
                return a-3, b-3
            self.backend.play = past_play
            entry = {}
            with patch("e2e_flow.run.request", side_effect=transport):
                runner.turn({"user_text": "reference sentence", "expect": {"intent": "greet", "actions": ["utter_greet"]}}, self.context, entry)
            self.assertEqual(sent[0]["message"], "recognized microphone text")
            self.assertEqual(sent[0]["sender"], "s1")
            self.assertEqual(entry["status"], "PASS")
            names = [n for n, _, _ in self.events.records]
            self.assertLess(names.index("bot.playback.completed"), names.index("cooldown.started"))

    def test_second_session_runs_after_first_assertion_failure(self):
        sessions = {'sessions': [dict(id=sid, turns=[{'user_text': 'Hello'}]) for sid in ('one', 'two')]}
        with tempfile.TemporaryDirectory() as output:
            runner = Runner(self.cfg, sessions, output)
            runner.events = self.events
            def turn(spec, context, entry):
                if context['session_id'].endswith('_one'):
                    raise AssertionError('wrong intent')
                entry['status'] = 'PASS'
            with patch.object(runner, 'turn', side_effect=turn), patch.object(runner, 'flush_services'), patch('e2e_flow.run.request'):
                runner.run_sessions()
            self.assertEqual([s['status'] for s in runner.report['sessions']], ['FAIL', 'PASS'])

    def test_evaluation_ignores_prior_actions_and_detects_wrong_cart(self):
        tracker = {"sender_id": "s1", "latest_message": {"metadata": self.context, "intent": {"name": "order_drink"}},
                   "events": [{"event": "action", "name": "action_add_to_cart"},
                              {"event": "user", "metadata": self.context}, {"event": "action", "name": "utter_fallback"}],
                   "slots": {"cart": json.dumps([{"key": "pepsi", "qty": 1}])}}
        checks = evaluate({"expect": {"intent": "order_drink", "actions": ["action_add_to_cart"], "cart": {"coca": 2}}},
                          tracker, [{"text": "fallback"}], self.context)
        self.assertFalse(next(c for c in checks if c["name"] == "actions.in_order")["passed"])
        self.assertFalse(next(c for c in checks if c["name"] == "cart")["passed"])

    def test_config_and_scores(self):
        scenarios = json.loads((HERE/"sessions.json").read_text())
        validate(self.cfg, scenarios)
        self.cfg["cooldown_seconds"] = 1
        with self.assertRaises(ValueError):
            validate(self.cfg, scenarios)
        self.assertEqual(speech_scores("Hello, world!", "hello world")["wer"], 0)
        self.assertEqual(speech_scores("one two", "one")["wer"], .5)

    def test_cart_items_rejects_correct_quantity_but_wrong_volume(self):
        tracker = {"sender_id": "s1", "latest_message": {"metadata": self.context, "intent": {"name": "order_drink"}},
                   "events": [{"event": "user", "metadata": self.context}],
                   "slots": {"cart": json.dumps([{"key": "coca", "qty": 2, "volume": "330ml"}])}}
        spec = {"expect": {"intent": "order_drink", "cart": {"coca": 2},
                           "cart_items": [{"product": "coca", "quantity": 2, "volume": "500ml"}]}}
        checks = evaluate(spec, tracker, [{"text": "Added"}], self.context)
        self.assertTrue(next(c for c in checks if c["name"] == "cart")["passed"])
        self.assertFalse(next(c for c in checks if c["name"] == "cart_items")["passed"])
        tracker["slots"]["cart"] = json.dumps([{"key": "coca", "qty": 2, "volume": "500ml"}])
        self.assertTrue(all(c["passed"] for c in evaluate(spec, tracker, [{"text": "Added"}], self.context)))

    def test_startup_failure_cleanup_never_contacts_unowned_servers(self):
        with tempfile.TemporaryDirectory() as output:
            runner = Runner(self.cfg, {"sessions": []}, output)
            runner.report["status"] = "FAIL"
            with patch("e2e_flow.run.request") as transport:
                runner.finish()
            transport.assert_not_called()
            self.assertTrue((Path(output)/"summary.json").is_file())


# Event delivery and resource sampling

class LoggingTests(unittest.TestCase):
    def test_lost_ack_retry_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as output:
            server = LogServer(output, 10)
            client = EventClient("http://unused", "test", "run", output)
            def lost_ack(url, event, timeout=10):
                server.dispatch("POST", "/events", event)
                raise TimeoutError("ack lost")
            try:
                with patch("e2e_flow.common.request", side_effect=lost_ack):
                    client.emit("example", {"value": 7})
                self.assertEqual(len(client.pending), 1)
                with patch("e2e_flow.common.request", side_effect=lambda url, event, timeout=10: server.dispatch("POST", "/events", event)):
                    self.assertEqual(client.flush(), 0)
                events = (Path(output)/"events.jsonl").read_text().splitlines()
                self.assertEqual(sum(json.loads(e)['event_name']=='example' for e in events), 1)
            finally:
                server.stop_event.set()
                server.thread.join(1)

    def test_sampler_runs_during_wait(self):
        with tempfile.TemporaryDirectory() as output:
            server = LogServer(output, .03)
            try:
                deadline = time.monotonic()+3
                while time.monotonic() < deadline:
                    with server.lock:
                        rows = list(server.all_events)
                    if len(rows) >= 2:
                        break
                    time.sleep(.03)
                self.assertGreaterEqual(len(rows), 2)
                self.assertIn('rss_mb', rows[0]['event_data']['processes'][0])
            finally:
                server.stop_event.set()
                server.thread.join(1)


# Local service ports

class PortTests(unittest.TestCase):
    def test_active_listener_is_rejected(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            with self.assertRaises(RuntimeError) as error:
                check_port_available('log', port)
            self.assertNotIn('--external-rasa-actions', str(error.exception))
            with self.assertRaises(RuntimeError) as error:
                check_port_available('rasa', port)
            self.assertIn('change the port', str(error.exception))

    def test_restart_after_server_closes_connection(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            with socket.create_connection(('127.0.0.1', port), timeout=2) as client:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(2)
                    connection.shutdown(socket.SHUT_WR)
                    self.assertEqual(client.recv(1), b'')
                    client.shutdown(socket.SHUT_WR)
                    self.assertEqual(connection.recv(1), b'')
        # Reproduce the original probe's false positive, then verify the fix.
        with socket.socket() as old_probe:
            with self.assertRaises(OSError) as error:
                old_probe.bind(('127.0.0.1', port))
            self.assertEqual(error.exception.errno, errno.EADDRINUSE)
        check_port_available('log', port)


# Source timestamps

class FakeApp:
    def __init__(self):
        self.middleware_functions = {}
    def get(self, path):
        return lambda fn: fn
    post = get
    def middleware(self, kind):
        def register(fn):
            self.middleware_functions[kind] = fn
            return fn
        return register


class TimestampTests(unittest.TestCase):
    def test_rasa_delay_uses_server_boundaries_and_repeated_operations(self):
        with tempfile.TemporaryDirectory() as folder:
            client = EventClient('unused', 'rasa', 'test', folder)
            rows = []
            with patch('e2e_flow.common.request'):
                for name, ns, op in [('rasa.request.sent', 1, 'http'),
                                     ('rasa.received', 100000000, 'a'),
                                     ('rasa.processing.completed', 450000000, 'a'),
                                     ('rasa.received', 500000000, 'b'),
                                     ('rasa.processing.completed', 600000000, 'b'),
                                     ('rasa.response.received', 900000000, 'http'),
                                     ('stt.started', 910000000, 'incomplete')]:
                    rows.append(client.emit(name, {}, dict(monotonic_ns=ns, operation_id=op,
                                                           session_id='s', turn_id='t')))
            result = export_delays(folder, list(reversed(rows)))
            self.assertEqual([d['delay_ms'] for d in result['delays']], [350, 100])
            self.assertEqual(result['delays'][0]['start_event_id'], rows[1]['event_id'])
            self.assertEqual(result['delays'][0]['end_event_id'], rows[2]['event_id'])
            self.assertIn('missing completion', result['missing_pairs'][0])

    def test_pairs_do_not_cross_sessions(self):
        events = [dict(event_name=name, monotonic_ns=ns, source='voice',
                       session_id=sid, turn_id='t', operation_id='op')
                  for name, ns, sid in [('stt.started', 1, 'a'), ('stt.completed', 2, 'b')]]
        pairs, missing = calculate_events(events)
        self.assertEqual(pairs, [])
        self.assertEqual(len(missing), 2)

    def test_http_hooks_record_actual_body_and_action_result(self):
        async def exercise(folder):
            events = EventClient('unused', 'rasa', 'run', folder)
            app = instrument_http(FakeApp(), events, 'rasa')
            req = SimpleNamespace(path='/webhooks/rest/webhook', method='POST', headers={},
                                  json={'message': 'Hello', 'metadata': {'session_id': 's', 'turn_id': 't'}},
                                  ctx=SimpleNamespace())
            with patch('e2e_flow.common.request', side_effect=TimeoutError):
                await app.middleware_functions['request'](req)
                self.assertEqual(CONTEXT.get()['turn_id'], 't')
                await asyncio.sleep(.002)
                await app.middleware_functions['response'](req, SimpleNamespace(status=200, body=b'[{"text":"Hi"}]'))
            self.assertEqual(CONTEXT.get(), {})
            rows = list(events.pending)
            self.assertEqual([e['event_name'] for e in rows], ['rasa.received', 'rasa.processing.completed'])
            self.assertEqual(rows[0]['operation_id'], rows[1]['operation_id'])
            self.assertLess(rows[0]['monotonic_ns'], rows[1]['monotonic_ns'])
            self.assertEqual(rows[1]['event_data']['response'], [{'text': 'Hi'}])
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(exercise(folder))

    def test_execution_error_has_no_successful_completion(self):
        class Executor:
            async def run(self, data):
                raise ValueError('failure')
        async def exercise(folder):
            events = EventClient('unused', 'action', 'run', folder)
            instrument_method(Executor, 'run', events, 'action.execution', lambda a, k: a[1])
            with patch('e2e_flow.common.request', side_effect=TimeoutError):
                with self.assertRaises(ValueError):
                    await Executor().run({'next_action': 'example'})
            rows = list(events.pending)
            self.assertEqual([r['event_name'] for r in rows], ['action.execution.started', 'action.execution.failed'])
            self.assertEqual(rows[0]['operation_id'], rows[1]['operation_id'])
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(exercise(folder))

    def test_finalize_sorts_late_events_and_aligns_resource_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            server = LogServer(folder, .02)
            client = EventClient('unused', 'runner', Path(folder).name, folder)
            try:
                with patch('e2e_flow.common.request', side_effect=lambda url, data, timeout=1: server.dispatch('POST', '/events', data)):
                    origin = time.monotonic_ns()
                    client.emit('test.started', context={'monotonic_ns': origin})
                    client.emit('session.started', context={'session_id': 's'})
                    time.sleep(.06)
                    client.emit('late.event', context={'monotonic_ns': origin-1000})
                    client.emit('session.completed', context={'session_id': 's'})
                    client.emit('test.completed')
                result = server.dispatch('POST', '/finalize', {})
                self.assertTrue(result['complete'])
                rows = [json.loads(line) for line in (Path(folder)/'events.jsonl').read_text().splitlines()]
                late = next(r for r in rows if r['event_name']=='late.event')
                self.assertEqual(late['elapsed_ms'], -.001)
                self.assertTrue(any(r['event_name']=='system.sample' for r in rows))
                self.assertEqual([r['monotonic_ns'] for r in rows], sorted(r['monotonic_ns'] for r in rows))
            finally:
                server.stop_event.set()
                server.thread.join(1)


# Voice activity detection

class NoiseGateTests(unittest.TestCase):
    def test_false_positive_vad_does_not_keep_background_alive(self):
        gate = NoiseGate([-34]*30, margin_db=8)
        self.assertFalse(gate.is_speech(True, -34))
        self.assertTrue(gate.is_speech(True, -17))
        self.assertFalse(gate.is_speech(False, -17))
        self.assertEqual(gate.threshold_dbfs, -26)

    def test_silent_calibration_uses_absolute_floor(self):
        gate = NoiseGate([dbfs(np.zeros(480, dtype='int16'))]*30)
        self.assertEqual(gate.threshold_dbfs, -55)
        self.assertFalse(gate.is_speech(True, -100))

    def record(self, output, never_silent=False, delayed_prompt=False):
        cfg = json.loads(Path(__file__).resolve().parent.joinpath('config.json').read_text())
        cfg['save_audio'] = True
        cfg['max_record_seconds'] = .5 if never_silent else 20
        clock = [0.0]
        prompt_done = threading.Event()
        if not delayed_prompt:
            prompt_done.set()
        class Stream:
            def __init__(self, **kwargs):
                self.i = 0
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, n):
                clock[0] += .03
                self.i += 1
                if delayed_prompt and self.i == 85:
                    prompt_done.set()
                # 8 flush + 30 noise calibration; voice follows, then steady noise.
                loud = self.i > 38 and (never_silent or self.i <= 48)
                return np.full((n,1), 5000 if loud else 650, dtype='int16'), False
        class AlwaysSpeech:
            def __init__(self, *args):
                pass
            def is_speech(self, *args):
                return True
        backend = AudioBackend.__new__(AudioBackend)
        backend.cfg, backend.np = cfg, np
        backend.sd = types.SimpleNamespace(InputStream=Stream)
        backend.vad_module = types.SimpleNamespace(Vad=AlwaysSpeech)
        backend.events = types.SimpleNamespace(path=Path(output)/'voice.journal.jsonl', emit=lambda *a, **k: None)
        prompt = threading.Event()
        prompt.set()
        with patch('e2e_flow.voice_server.time.monotonic', side_effect=lambda: clock[0]):
            return backend.record(lambda: None, lambda: None, prompt, prompt_done, threading.Event(),
                                  dict(session_id='s', turn_id='t'))

    def test_real_capture_loop_ends_with_noise_despite_raw_vad_always_true(self):
        with tempfile.TemporaryDirectory() as output:
            pcm, _ = self.record(output)
            self.assertGreater(len(pcm), 0)
            self.assertLess(len(pcm), 16000*2)
            detail = json.loads((Path(output)/'audio/s_t.vad.json').read_text())
            self.assertIsNone(detail['error'])
            self.assertTrue(all(r['raw_vad'] for r in detail['frames']))
            self.assertFalse(detail['frames'][-1]['gated_vad'])
            self.assertTrue((Path(output)/'audio/s_t.wav').exists())

    def test_pause_during_prompt_does_not_end_capture(self):
        with tempfile.TemporaryDirectory() as output:
            pcm, _ = self.record(output, delayed_prompt=True)
            self.assertGreater(len(pcm), 16000)

    def test_timeout_still_fails_and_saves_audio_evidence(self):
        with tempfile.TemporaryDirectory() as output:
            with self.assertRaisesRegex(TimeoutError, 'Speech exceeded'):
                self.record(output, never_silent=True)
            self.assertTrue((Path(output)/'audio/s_t.failed.wav').exists())
            detail = json.loads((Path(output)/'audio/s_t.vad.json').read_text())
            self.assertIn('Speech exceeded', detail['error'])


if __name__ == "__main__":
    unittest.main()
