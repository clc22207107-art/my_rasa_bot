"""
benchmark.py — Solution 1: Rasa (Intent Classification + Slot Filling)
=======================================================================
Đo hiệu năng toàn bộ pipeline cho từng câu test:

  Piper TTS → WAV → faster-whisper STT → Rasa NLU → response

Metrics đo mỗi stage:
  - Latency (ms)
  - CPU usage (%)
  - RAM usage (MB) — chỉ process Python này
  - Nhiệt độ CPU (°C)

Output: benchmark_rasa.csv + tóm tắt in ra màn hình

Yêu cầu trước khi chạy:
    Terminal 1: rasa run actions
    Terminal 2: rasa run --enable-api --cors "*"

Cách dùng:
    python benchmark.py                        # toàn bộ
    python benchmark.py --intent ask_price     # 1 intent
    python benchmark.py --max 5               # tối đa 5 câu/intent
    python benchmark.py --output result.csv   # custom output
"""

import sys
import os
import re
import csv
import time
import threading
import argparse
import tempfile
import warnings
import requests
import numpy as np
import soundfile as sf
import psutil
warnings.filterwarnings("ignore")

from piper import PiperVoice
from faster_whisper import WhisperModel

# ============================================================
# CONFIG
# ============================================================

RASA_URL        = "http://localhost:5005"
TEST_FILE       = "tests/test_nlu.yml"
DEFAULT_OUTPUT  = "benchmark_rasa.csv"

PIPER_MODEL     = os.path.expanduser("~/piper_models/en_US-lessac-medium.onnx")
PIPER_CONFIG    = os.path.expanduser("~/piper_models/en_US-lessac-medium.onnx.json")

WHISPER_SIZE    = "medium"
WHISPER_DEVICE  = "cpu"
WHISPER_COMPUTE = "int8"

SKIP_INTENTS    = {"specify_ice", "specify_sugar"}
STT_THRESHOLD   = 0.6
SAMPLE_INTERVAL = 0.05   # 50ms — tần suất lấy mẫu CPU/RAM/nhiệt độ

# ── ANSI ─────────────────────────────────────────────────────
GREEN = "\033[92m"; RED = "\033[91m"; YELLOW = "\033[93m"
CYAN  = "\033[96m"; BOLD = "\033[1m"; DIM = "\033[2m"; RESET = "\033[0m"

# ============================================================
# HARDWARE METRICS SAMPLER
# ============================================================

THERMAL_PATH = "/sys/class/thermal/thermal_zone0/temp"

def read_temp_c() -> float:
    """Đọc nhiệt độ CPU (°C). Trả về -1 nếu không đọc được."""
    try:
        return int(open(THERMAL_PATH).read().strip()) / 1000.0
    except Exception:
        return -1.0


class MetricsSampler:
    """Lấy mẫu CPU / RAM / nhiệt độ trong một thread riêng."""

    def __init__(self, interval: float = SAMPLE_INTERVAL):
        self._interval = interval
        self._proc     = psutil.Process()
        self._running  = False
        self._thread   = None
        self.cpu_samples  : list[float] = []
        self.ram_samples  : list[float] = []
        self.temp_samples : list[float] = []

    def start(self):
        self.cpu_samples  = []
        self.ram_samples  = []
        self.temp_samples = []
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join()

    def _run(self):
        psutil.cpu_percent()          # prime (first call bao giờ cũng trả 0)
        while self._running:
            self.cpu_samples.append(psutil.cpu_percent())
            self.ram_samples.append(self._proc.memory_info().rss / 1024 / 1024)
            self.temp_samples.append(read_temp_c())
            time.sleep(self._interval)

    # ── aggregate properties ────────────────────────────────
    @property
    def avg_cpu(self)  -> float: return float(np.mean(self.cpu_samples))  if self.cpu_samples  else 0.0
    @property
    def peak_cpu(self) -> float: return float(np.max(self.cpu_samples))   if self.cpu_samples  else 0.0
    @property
    def peak_ram(self) -> float: return float(np.max(self.ram_samples))   if self.ram_samples  else 0.0
    @property
    def avg_temp(self) -> float: return float(np.mean(self.temp_samples)) if self.temp_samples else -1.0
    @property
    def peak_temp(self)-> float: return float(np.max(self.temp_samples))  if self.temp_samples else -1.0


def measure(fn, sampler: MetricsSampler):
    """Chạy fn(), đo thời gian, lấy mẫu metrics đồng thời. Trả về (result, ms)."""
    sampler.start()
    t0 = time.perf_counter()
    result = fn()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    sampler.stop()
    return result, elapsed_ms

# ============================================================
# PARSE TEST FILE
# ============================================================

def strip_entities(text: str) -> str:
    return re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text).strip()


def load_test_cases(path: str, filter_intent=None, max_per=None) -> list:
    data: dict[str, list] = {}
    current = None
    in_ex   = False

    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = re.match(r"^- intent:\s*(\S+)", s)
            if m:
                current = m.group(1); data[current] = []; in_ex = False; continue
            if s == "examples: |":
                in_ex = True; continue
            if in_ex and current and s.startswith("- "):
                original = s[2:].strip()
                clean    = strip_entities(original)
                if len(clean) >= 3:
                    data[current].append({"intent": current, "original": original, "clean": clean})

    cases = []
    for intent, examples in sorted(data.items()):
        if intent in SKIP_INTENTS: continue
        if filter_intent and intent != filter_intent: continue
        batch = examples[:max_per] if max_per else examples
        cases.extend(batch)
    return cases

# ============================================================
# PIPELINE STAGES
# ============================================================

def stage_tts(tts_voice: PiperVoice, text: str, wav_path: str) -> bool:
    parts = [chunk.audio_float_array for chunk in tts_voice.synthesize(text)]
    if not parts: return False
    sf.write(wav_path, np.concatenate(parts), tts_voice.config.sample_rate)
    return True


def stage_stt(stt_model: WhisperModel, wav_path: str) -> str:
    segs, _ = stt_model.transcribe(
        wav_path, language="en",
        beam_size=2, best_of=2, temperature=0.0,
        vad_filter=False,
        no_speech_threshold=0.4, condition_on_previous_text=False,
        initial_prompt="Customer ordering drinks at a vending machine. "
                       "Products: Coca-Cola, Pepsi, Sprite, Red Bull, Sting, Monster, "
                       "7UP, Fanta, Mirinda, Aquafina, La Vie, Revive, C2, Yakult.",
    )
    return " ".join(seg.text.strip() for seg in segs).strip().strip(".,!? ")


def stage_nlu(text: str) -> tuple[str, float]:
    try:
        r = requests.post(f"{RASA_URL}/model/parse", json={"text": text}, timeout=10)
        r.raise_for_status()
        intent = r.json()["intent"]
        return intent["name"], round(intent["confidence"], 3)
    except requests.exceptions.ConnectionError:
        print(f"\n{RED}Rasa mất kết nối!{RESET}")
        sys.exit(1)
    except Exception:
        return "error", 0.0


def stage_tts_response(tts_voice: PiperVoice, response_text: str) -> bool:
    """TTS giai đoạn cuối — đọc response của Rasa (không phát ra loa, chỉ đo thời gian)."""
    clean = re.sub(r'[𐀀-􏿿☀-⛿✀-➿🌀-🧿]', '', response_text)
    clean = re.sub(r'\*+|_+|[─═]+', ' ', clean).strip()
    if not clean:
        return False
    # Chỉ consume generator, không lưu vào RAM
    count = 0
    for chunk in tts_voice.synthesize(clean):
        count += 1
        del chunk  # giải phóng ngay
    return count > 0


def word_overlap(ref: str, hyp: str) -> float:
    norm = lambda t: set(re.sub(r'[^\w\s]', '', t.lower()).split())
    r, h = norm(ref), norm(hyp)
    return len(r & h) / len(r) if r else 0.0

# ============================================================
# MAIN
# ============================================================

def fmt(v, unit="", digits=1): return f"{v:.{digits}f}{unit}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--intent", type=str)
    parser.add_argument("--max",    type=int, help="Tối đa N câu/intent")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    print("=" * 65)
    print("  BENCHMARK — Solution 1: Rasa (Intent Classification + Slot Filling)")
    print("=" * 65)

    # Kiểm tra Rasa
    print("\n  Kiểm tra kết nối Rasa...", end=" ", flush=True)
    try:
        requests.get(f"{RASA_URL}/status", timeout=5).raise_for_status()
        print("OK")
    except Exception:
        print(f"{RED}FAILED{RESET}")
        print("  → Terminal 1: rasa run actions")
        print("  → Terminal 2: rasa run --enable-api --cors \"*\"")
        sys.exit(1)

    # Load models và đo RAM baseline
    ram_before = psutil.Process().memory_info().rss / 1024 / 1024
    print(f"\n  [1/2] Tải TTS (Piper)...", end=" ", flush=True)
    t0 = time.time(); tts = PiperVoice.load(PIPER_MODEL, config_path=PIPER_CONFIG)
    print(f"OK ({time.time()-t0:.1f}s)")

    print(f"  [2/2] Tải STT (faster-whisper/{WHISPER_SIZE})...", end=" ", flush=True)
    t0 = time.time(); stt = WhisperModel(WHISPER_SIZE, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
    print(f"OK ({time.time()-t0:.1f}s)")

    ram_models = psutil.Process().memory_info().rss / 1024 / 1024
    print(f"\n  RAM baseline (models loaded): {ram_models:.0f} MB "
          f"(+{ram_models - ram_before:.0f} MB so với trước khi load)")
    print(f"  Nhiệt độ CPU hiện tại: {read_temp_c():.1f}°C")

    # Load test cases
    cases = load_test_cases(TEST_FILE, filter_intent=args.intent, max_per=args.max)
    total = len(cases)
    if total == 0:
        print(f"\n{RED}Không có câu test nào.{RESET}"); sys.exit(1)

    print(f"\n  {total} câu test | output → {args.output}\n")
    print(f"{'─'*65}")
    print(f"  {'#':>4}  {'Intent':<22}  {'TTS':>6}  {'STT':>6}  {'NLU':>6}  {'TTS_out':>7}  {'Total':>6}  "
          f"{'CPU%':>5}  {'RAM':>6}  {'Temp':>5}  {'OK'}")
    print(f"  {'─'*4}  {'─'*22}  {'─'*6}  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*6}  "
          f"{'─'*5}  {'─'*6}  {'─'*5}  {'─'*2}")

    results  = []
    samplers = {k: MetricsSampler() for k in ("tts", "stt", "nlu", "tts_out")}
    intent_ok_count = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = os.path.join(tmpdir, "audio.wav")

        for i, case in enumerate(cases, 1):
            clean    = case["clean"]
            expected = case["intent"]

            # ── TTS ──────────────────────────────────────────
            ok_tts, t_tts = measure(
                lambda: stage_tts(tts, clean, wav_path),
                samplers["tts"]
            )

            if not ok_tts:
                print(f"  {i:4d}  {RED}TTS FAILED{RESET}")
                continue

            # ── STT ──────────────────────────────────────────
            stt_text, t_stt = measure(
                lambda: stage_stt(stt, wav_path),
                samplers["stt"]
            )
            overlap  = word_overlap(clean, stt_text)
            stt_ok   = overlap >= STT_THRESHOLD

            # ── NLU ──────────────────────────────────────────
            (predicted, conf), t_nlu = measure(
                lambda: stage_nlu(stt_text),
                samplers["nlu"]
            )
            n_ok = predicted == expected
            if n_ok: intent_ok_count += 1

            # ── TTS output ───────────────────────────────────
            try:
                r = requests.post(f"{RASA_URL}/webhooks/rest/webhook",
                                  json={"sender": "benchmark", "message": stt_text},
                                  timeout=10)
                response_texts = " ".join(m.get("text","") for m in r.json() if m.get("text"))
            except Exception:
                response_texts = ""

            _, t_tts_out = measure(
                lambda: stage_tts_response(tts, response_texts),
                samplers["tts_out"]
            )

            t_total  = t_tts + t_stt + t_nlu + t_tts_out
            avg_cpu  = np.mean([samplers["tts"].avg_cpu,
                                samplers["stt"].avg_cpu,
                                samplers["nlu"].avg_cpu,
                                samplers["tts_out"].avg_cpu])
            peak_ram = max(samplers["tts"].peak_ram,
                           samplers["stt"].peak_ram,
                           samplers["nlu"].peak_ram,
                           samplers["tts_out"].peak_ram)
            avg_temp = np.mean([v for v in [samplers["tts"].avg_temp,
                                            samplers["stt"].avg_temp,
                                            samplers["nlu"].avg_temp,
                                            samplers["tts_out"].avg_temp] if v >= 0])

            status = (f"{GREEN}✓{RESET}" if (stt_ok and n_ok)
                      else f"{YELLOW}~{RESET}" if (stt_ok or n_ok)
                      else f"{RED}✗{RESET}")

            print(f"  {i:4d}  {CYAN}{expected:<22}{RESET}"
                  f"  {t_tts:5.0f}ms  {t_stt:5.0f}ms  {t_nlu:4.0f}ms"
                  f"  {t_tts_out:5.0f}ms"
                  f"  {t_total:5.0f}ms"
                  f"  {avg_cpu:4.0f}%"
                  f"  {peak_ram:5.0f}MB"
                  f"  {avg_temp:4.1f}°C"
                  f"  {status}")

            results.append({
                # Metadata
                "intent_expected":   expected,
                "clean":             clean,
                "stt_output":        stt_text,
                "word_overlap":      f"{overlap:.2f}",
                "stt_ok":            "PASS" if stt_ok else "FAIL",
                "predicted_intent":  predicted,
                "confidence":        f"{conf:.3f}",
                "intent_ok":         "PASS" if n_ok else "FAIL",
                "status":            "PASS" if (stt_ok and n_ok) else "FAIL",
                # Latency (ms)
                "t_tts_ms":          f"{t_tts:.1f}",
                "t_stt_ms":          f"{t_stt:.1f}",
                "t_nlu_ms":          f"{t_nlu:.1f}",
                "t_tts_out_ms":      f"{t_tts_out:.1f}",
                "t_total_ms":        f"{t_total:.1f}",
                # CPU (%)
                "cpu_avg_tts":       f"{samplers['tts'].avg_cpu:.1f}",
                "cpu_avg_stt":       f"{samplers['stt'].avg_cpu:.1f}",
                "cpu_avg_nlu":       f"{samplers['nlu'].avg_cpu:.1f}",
                "cpu_peak_total":    f"{max(samplers['tts'].peak_cpu, samplers['stt'].peak_cpu, samplers['nlu'].peak_cpu):.1f}",
                # RAM (MB)
                "ram_peak_mb":       f"{peak_ram:.1f}",
                # Temp (°C)
                "temp_avg_tts":      f"{samplers['tts'].avg_temp:.1f}",
                "temp_avg_stt":      f"{samplers['stt'].avg_temp:.1f}",
                "temp_avg_nlu":      f"{samplers['nlu'].avg_temp:.1f}",
                "temp_peak_total":   f"{max(samplers['tts'].peak_temp, samplers['stt'].peak_temp, samplers['nlu'].peak_temp):.1f}",
            })

    # ── Ghi CSV ──────────────────────────────────────────────
    if results:
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)

    # ── Tóm tắt ──────────────────────────────────────────────
    n = len(results)
    if n == 0:
        print("\nKhông có kết quả."); return

    def col_f(key): return [float(r[key]) for r in results]
    def stat(key):
        v = col_f(key)
        return f"{np.mean(v):7.1f}  {np.min(v):7.1f}  {np.max(v):7.1f}  {np.std(v):6.1f}"

    passes = sum(1 for r in results if r["status"] == "PASS")
    stt_ok = sum(1 for r in results if r["stt_ok"] == "PASS")

    print(f"\n{'═'*65}")
    print(f"  {BOLD}TỔNG KẾT — {n} câu test{RESET}")
    print(f"{'─'*65}")
    print(f"  STT accuracy    : {GREEN if stt_ok/n>=0.9 else YELLOW}{stt_ok}/{n} ({100*stt_ok/n:.1f}%){RESET}")
    print(f"  Intent accuracy : {GREEN if intent_ok_count/n>=0.9 else YELLOW}{intent_ok_count}/{n} ({100*intent_ok_count/n:.1f}%){RESET}")
    print(f"  Full pipeline   : {GREEN if passes/n>=0.9 else YELLOW}{passes}/{n} ({100*passes/n:.1f}%){RESET}")
    print(f"  RAM (models)    : {ram_models:.0f} MB")
    print()
    print(f"  {'Metric':<20}  {'Mean':>7}  {'Min':>7}  {'Max':>7}  {'Std':>6}")
    print(f"  {'─'*20}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*6}")
    for label, key in [
        ("TTS latency (ms)",   "t_tts_ms"),
        ("STT latency (ms)",   "t_stt_ms"),
        ("NLU latency (ms)",   "t_nlu_ms"),
        ("TTS_out latency (ms)","t_tts_out_ms"),
        ("Total latency (ms)", "t_total_ms"),
        ("CPU avg TTS (%)",    "cpu_avg_tts"),
        ("CPU avg STT (%)",    "cpu_avg_stt"),
        ("CPU avg NLU (%)",    "cpu_avg_nlu"),
        ("CPU peak (%)",       "cpu_peak_total"),
        ("RAM peak (MB)",      "ram_peak_mb"),
        ("Temp avg TTS (°C)",  "temp_avg_tts"),
        ("Temp avg STT (°C)",  "temp_avg_stt"),
        ("Temp avg NLU (°C)",  "temp_avg_nlu"),
    ]:
        print(f"  {label:<20}  {stat(key)}")

    print(f"\n  Report: {args.output}")
    print(f"{'═'*65}\n")


if __name__ == "__main__":
    main()
