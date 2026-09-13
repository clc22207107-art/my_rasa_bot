"""Audio-only service. It never calls Rasa; the runner owns dialogue routing."""
import argparse
from collections import deque
import re
import threading
import time
import traceback
import wave
import uuid
from pathlib import Path

from .common import APIError, EventClient, load_config, serve


class VoiceServer:
    def __init__(self, config, events, backend=None):
        self.cfg, self.events = config, events
        self.lock = threading.RLock()
        self.state = "IDLE"
        self.context = {}
        self.seen_turns = set()
        self.result = None
        self.error = None
        self.last_playback = 0
        self.worker = None
        self.prompt_worker = None
        self.cancel = threading.Event()
        self.ready = threading.Event()
        self.prompt_started = threading.Event()
        self.prompt_done = threading.Event()
        self.backend = backend or AudioBackend(config, events)
        events.emit("voice.ready")

    def transition(self, state):
        with self.lock:
            old, self.state = self.state, state
        self.events.emit("voice.state.changed", {"from": old, "to": state}, self.context)

    def require(self, *states):
        if self.state not in states:
            raise APIError(f"State {self.state}; expected {states}")

    def fail(self, exc):
        self.cancel.set()
        self.error = str(exc)
        self.transition("ERROR")
        self.events.emit("voice.error", {"error": str(exc), "traceback": traceback.format_exc()},
                         self.context, "ERROR")

    def guarded(self, target):
        try:
            target()
        except Exception as exc:
            self.fail(exc)

    def start(self, target):
        self.worker = threading.Thread(target=self.guarded, args=(target,), daemon=True)
        self.worker.start()

    def dispatch(self, method, path, data):
        if method == "GET" and path == "/health":
            return {"ready": True, "output": str(self.events.path.parent.resolve())}
        if method == "GET" and path == "/status":
            with self.lock:
                return dict(state=self.state, context=self.context, result=self.result,
                            error=self.error, last_playback_monotonic=self.last_playback)
        if method != "POST":
            raise APIError("Unknown endpoint", 404)
        self.events.emit('voice.command.received', {'command': path, 'data': data}, data.get('context'))
        with self.lock:
            if path == "/cancel":
                self.cancel.set()
                return {"ok": True}
            if path == "/flush":
                # Worker shutdown is checked outside the state lock by callers.
                return {"pending_events": self.events.flush(),
                        "workers_active": any(t and t.is_alive() for t in (self.worker, self.prompt_worker))}
            if path == "/prepare":
                self.require("IDLE", "DONE")
                if time.monotonic() - self.last_playback < self.cfg["cooldown_seconds"]:
                    raise APIError("Cooldown has not elapsed")
                context = data["context"]
                key = (context["session_id"], context["turn_id"])
                if key in self.seen_turns:
                    raise APIError("Duplicate turn")
                if not isinstance(data.get("text"), str) or not data["text"].strip():
                    raise APIError("Non-empty text required", 400)
                self.seen_turns.add(key)
                self.context = context
                self.result, self.error = None, None
                self.cancel.clear()
                self.ready.clear()
                self.prompt_started.clear()
                self.prompt_done.clear()
                self.transition("PREPARING")
                text = data["text"]
                self.start(lambda: self.prepare(text))
            else:
                if data.get("context") != self.context:
                    raise APIError("Stale or mismatched turn context")
                if path == "/arm":
                    self.require("PREPARED")
                    self.transition("ARMING")
                    self.start(self.capture)
                elif path == "/prompt":
                    self.require("LISTENING")
                    if self.prompt_started.is_set():
                        raise APIError("Prompt already started")
                    self.prompt_started.set()
                    self.prompt_worker = threading.Thread(target=self.guarded, args=(self.play_prompt,), daemon=True)
                    self.prompt_worker.start()
                elif path == "/speak":
                    self.require("CAPTURED")
                    texts = data.get("texts")
                    if not isinstance(texts, list) or not texts or not all(isinstance(t, str) and t.strip() for t in texts):
                        raise APIError("Non-empty response texts required", 400)
                    self.transition("SPEAKING")
                    self.start(lambda: self.speak(texts))
                else:
                    raise APIError("Unknown endpoint", 404)
        return {"accepted": True}

    def prepare(self, text):
        self.prompt_audio = self.backend.synthesize(text, "user_prompt", self.context)
        self.transition("PREPARED")

    def play_prompt(self):
        self.backend.play(self.prompt_audio, "user_prompt", self.context, self.cancel)
        self.prompt_done.set()

    def capture(self):
        def on_ready():
            self.events.emit("vad.ready", context=self.context)
            self.transition("LISTENING")
            self.ready.set()

        def on_speech():
            self.transition("RECORDING")
            self.events.emit("vad.speech.started", context=self.context)

        pcm, speech_end = self.backend.record(on_ready, on_speech, self.prompt_started,
                                               self.prompt_done, self.cancel, self.context)
        # No further microphone input is accepted until the NEXT /arm command.
        self.transition("TRANSCRIBING")
        transcript, _ = self.backend.transcribe(pcm, self.context)
        if not transcript:
            raise RuntimeError("STT returned empty text")
        with self.lock:
            self.result = dict(transcript=transcript,
                               speech_end_monotonic=speech_end,
                               audio_seconds=len(pcm) / self.cfg["sample_rate"])
        self.transition("CAPTURED")
        self.events.emit("capture.completed", self.result, self.context)

    def speak(self, texts):
        first_playback = None
        for index, text in enumerate(texts):
            audio = self.backend.synthesize(text, "bot_response", self.context, index)
            started, finished = self.backend.play(audio, "bot_response", self.context, self.cancel, index)
            if first_playback is None:
                first_playback = started
        self.last_playback = finished
        self.events.emit("bot.playback.completed", {"responses": len(texts),
                         "completed_monotonic": finished}, self.context)
        with self.lock:
            self.result.update(first_bot_playback_monotonic=first_playback,
                               bot_playback_completed_monotonic=finished)
        self.transition("DONE")


class AudioBackend:
    def __init__(self, cfg, events):
        import numpy as np
        import sounddevice as sd
        import webrtcvad
        from faster_whisper import WhisperModel
        from piper import PiperVoice
        self.np, self.sd, self.vad_module = np, sd, webrtcvad
        self.cfg, self.events = cfg, events
        events.emit("models.loading")
        model = Path(cfg["piper_model"]).expanduser()
        if not model.is_file():
            raise FileNotFoundError(f"Piper model not found: {model}")
        self.voice = PiperVoice.load(str(model), config_path=str(model) + ".json")
        # Local models only: never download model files into an existing cache.
        self.whisper = WhisperModel(cfg["whisper_model"], device="cpu", compute_type="int8",
                                    cpu_threads=cfg["whisper_threads"], num_workers=1,
                                    local_files_only=True)
        sd.check_input_settings(device=cfg["input_device"], channels=1,
                                samplerate=cfg["sample_rate"], dtype="int16")
        sd.check_output_settings(device=cfg["output_device"], channels=1,
                                 samplerate=self.voice.config.sample_rate, dtype="float32")
        events.emit("models.loaded", {"audio_devices": str(sd.query_devices())})

    def synthesize(self, text, role, context, index=0):
        clean = re.sub(r'[\U00010000-\U0010FFFF☀-➿]', '', text)
        clean = re.sub(r'[*_─═]+', ' ', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()
        if not clean:
            raise RuntimeError("No speakable text after TTS cleanup")
        context = dict(context, operation_id=uuid.uuid4().hex)
        self.events.emit("tts.started", dict(role=role, index=index, text=text, spoken_text=clean), context)
        parts = [c.audio_float_array for c in self.voice.synthesize(clean)]
        if not parts:
            raise RuntimeError("Piper produced no audio")
        audio = self.np.concatenate(parts).astype(self.np.float32) * self.cfg["volume"]
        self.events.emit("tts.completed", dict(role=role, index=index,
                         audio_seconds=len(audio)/self.voice.config.sample_rate), context)
        return audio

    def play(self, audio, role, context, cancel, index=0):
        context = dict(context, operation_id=uuid.uuid4().hex)
        if cancel.is_set():
            raise RuntimeError("Playback cancelled")
        with self.sd.OutputStream(device=self.cfg["output_device"], channels=1, dtype="float32",
                                  samplerate=self.voice.config.sample_rate) as stream:
            start = time.monotonic()
            self.events.emit("playback.started", dict(role=role, index=index, started_monotonic=start), context)
            for offset in range(0, len(audio), 1024):
                if cancel.is_set():
                    stream.abort()
                    raise RuntimeError("Playback cancelled")
                if stream.write(audio[offset:offset+1024]):
                    raise RuntimeError("Audio output underflow")
            stream.stop()  # Wait until all pending output buffers have played.
            end = time.monotonic()
        self.events.emit("playback.completed", dict(role=role, index=index,
                         completed_monotonic=end), context)
        return start, end

    def record(self, on_ready, on_speech, prompt_started, prompt_done, cancel, context):
        from .vad_gate import NoiseGate, dbfs
        import json
        cfg, np = self.cfg, self.np
        sr, frame_ms = cfg["sample_rate"], 30
        n = sr * frame_ms // 1000
        vad = self.vad_module.Vad(cfg["vad_aggressiveness"])
        pre_roll = deque(maxlen=10)
        frames, observed, diagnostics = [], [], []
        voiced, silence, started = 0, 0, False
        last_speech, gate, error = None, None, None
        capture_start = time.monotonic()
        try:
            with self.sd.InputStream(device=cfg["input_device"], samplerate=sr, channels=1,
                                     dtype="int16", blocksize=n) as stream:
                self.events.emit("microphone.opened", {"sample_rate": sr, "device": cfg["input_device"]}, context)
                # Drain stale buffers, then estimate ambient noise BEFORE vad.ready.
                for _ in range(8):
                    stream.read(n)
                levels = []
                for _ in range(max(1, int(cfg.get("vad_calibration_seconds", .9)*1000/frame_ms))):
                    if cancel.is_set():
                        raise RuntimeError("Capture cancelled")
                    data, overflow = stream.read(n)
                    if overflow:
                        raise RuntimeError("Audio input overflow during noise calibration")
                    levels.append(dbfs(data))
                gate = NoiseGate(levels, cfg.get("vad_noise_margin_db", 8), cfg.get("vad_min_dbfs", -55))
                self.events.emit("vad.noise.calibrated", dict(noise_dbfs=gate.noise_dbfs,
                                 threshold_dbfs=gate.threshold_dbfs, frames=len(levels)), context)
                on_ready()
                waiting_since = time.monotonic()
                recording_since = None
                while True:
                    if cancel.is_set():
                        raise RuntimeError("Capture cancelled")
                    data, overflow = stream.read(n)
                    now = time.monotonic()
                    observed.append(data.copy())
                    if overflow:
                        raise RuntimeError("Audio input overflow")
                    level = dbfs(data)
                    raw_speech = vad.is_speech(data.tobytes(), sr)
                    speech = gate.is_speech(raw_speech, level)
                    diagnostics.append(dict(seconds=now-capture_start, dbfs=level,
                                            raw_vad=raw_speech, gated_vad=speech,
                                            clipped_samples=int(np.sum(np.abs(data.astype(np.int32)) >= 32760))))
                    if not prompt_started.is_set():
                        if now-waiting_since > cfg["speech_wait_seconds"]:
                            raise TimeoutError("Timed out waiting for /prompt")
                        continue
                    if not started:
                        pre_roll.append(data.copy())
                        voiced = voiced+1 if speech else 0
                        if voiced >= 3:
                            started, recording_since = True, now
                            frames.extend(pre_roll)
                            last_speech = now
                            on_speech()
                        elif now-waiting_since > cfg["speech_wait_seconds"]:
                            raise TimeoutError("VAD detected no speech above the measured noise floor")
                        continue
                    frames.append(data.copy())
                    if speech:
                        voiced += 1
                        silence = 0
                        last_speech = now
                    else:
                        silence += 1
                    if silence*frame_ms >= cfg["vad_silence_ms"] and voiced*frame_ms >= cfg["vad_min_speech_ms"]:
                        if prompt_done.is_set():
                            self.events.emit("vad.endpoint.detected", {"last_speech_monotonic": last_speech}, context)
                            break
                        # Pauses inside the known prompt are not the end of the turn.
                        # Require a fresh silence interval after playback catches up.
                        silence = 0
                    if now-recording_since > cfg["max_record_seconds"]:
                        raise TimeoutError("Speech exceeded max_record_seconds")
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            self.events.emit("microphone.closed", {"error": error}, context)
            # Retain the failed capture as well; never discard evidence on timeout.
            if observed and cfg.get("save_audio", False):
                details = dict(noise_dbfs=gate.noise_dbfs if gate else None,
                               threshold_dbfs=gate.threshold_dbfs if gate else None,
                               error=error, frames=diagnostics)
                folder = self.events.path.parent / "audio"
                folder.mkdir(exist_ok=True)
                stem = context["session_id"] + "_" + context["turn_id"]
                (folder / (stem + ".vad.json")).write_text(json.dumps(details, indent=2))
                self.events.emit("vad.capture.diagnostics", dict(
                    noise_dbfs=details["noise_dbfs"], threshold_dbfs=details["threshold_dbfs"],
                    frames=len(diagnostics), raw_speech_frames=sum(d["raw_vad"] for d in diagnostics),
                    gated_speech_frames=sum(d["gated_vad"] for d in diagnostics),
                    clipped_samples=sum(d["clipped_samples"] for d in diagnostics), error=error), context)
                if cfg["save_audio"]:
                    # On success save the exact STT input. On failure save all observed frames.
                    samples = np.concatenate(observed if error else frames).reshape(-1)
                    path = folder / (stem + (".failed.wav" if error else ".wav"))
                    with wave.open(str(path), "wb") as f:
                        f.setnchannels(1)
                        f.setsampwidth(2)
                        f.setframerate(sr)
                        f.writeframes(samples.astype("<i2").tobytes())
                    self.events.emit("audio.saved", {"path": str(path), "failed": bool(error)}, context)
        pcm = np.concatenate(frames).reshape(-1)
        self.events.emit("vad.speech.ended", {"speech_end_monotonic": last_speech,
                         "audio_seconds": len(pcm)/sr, "voiced_ms": voiced*frame_ms}, context)
        return pcm, last_speech

    def transcribe(self, pcm, context):
        context = dict(context, operation_id=uuid.uuid4().hex)
        self.events.emit("stt.started", {"audio_samples": len(pcm), "sample_rate": self.cfg["sample_rate"]}, context)
        segments, info = self.whisper.transcribe(
            pcm.astype(self.np.float32)/32768.0, language=self.cfg["language"],
            beam_size=1, best_of=1, temperature=0.0, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            no_speech_threshold=0.4, word_timestamps=False,
            condition_on_previous_text=False,
            initial_prompt=(
                "Customer ordering drinks at a vending machine. "
                "Products: Coca-Cola, Pepsi, Sprite, Red Bull, Sting, Monster, "
                "7UP, Fanta, Mirinda, Aquafina, Lavie, Revive, C2, Yakult, "
                "Lipton, Nestea, Birdy, Nescafe, Cocoxim, Twister. "
                "Shopping cart, cart contents, order confirmation, order cancellation. "
                "Payment methods: cash, credit card, bank transfer."
            ))
        raw = " ".join(segment.text.strip() for segment in segments).strip()
        text = raw.strip(".,!? ")
        self.events.emit("stt.completed", {"raw_text": raw, "text": text,
                         "language": info.language}, context)
        return text, None  # Durations are derived later from the two source events.


def main():
    from .support.runtime import arguments, client
    args = arguments(__doc__)
    cfg = load_config(args.config)
    cfg["save_audio"] = False  # Production output is one log per session.
    events = client('voice', args, cfg)
    app = VoiceServer(cfg, events)
    serve(cfg["ports"]["voice"], app.dispatch)


if __name__ == "__main__":
    main()
