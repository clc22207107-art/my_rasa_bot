"""
test_benchmark_stt.py — Benchmark STT (Speech-to-Text) trên 243 audio files
Pipeline: audio file (.m4a) → faster-whisper → so sánh với expected text

Metrics:
  - Word accuracy per sentence
  - Transcription time
  - RAM used (MB), RAM %, CPU %, CPU temperature (°C)

Yêu cầu: KHÔNG cần Rasa chạy

Cách dùng:
    python test_benchmark_stt.py
    python test_benchmark_stt.py --intent greet
    python test_benchmark_stt.py --max 20
    python test_benchmark_stt.py --from 10 --to 50
"""

import os, re, sys, time, subprocess, tempfile, argparse, json, datetime, warnings
warnings.filterwarnings("ignore")

import numpy as np
import psutil
from faster_whisper import WhisperModel

# ── Cấu hình ─────────────────────────────────────────────────────────────────
AUDIO_DIR       = "audiokltn260"
TEST_FILE       = "tests/test_stt.yml"
FILE_OFFSET     = 0
SAMPLE_RATE     = 16000
WHISPER_MODEL   = "small"
WHISPER_DEVICE  = "cpu"
WHISPER_COMPUTE = "int8"
WHISPER_THREADS = 1

# ── ANSI ──────────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"; RED    = "\033[91m"; YELLOW = "\033[93m"
CYAN   = "\033[96m"; BOLD   = "\033[1m";  DIM    = "\033[2m"; RESET = "\033[0m"


# =============================================================================
# System monitoring
# =============================================================================

def get_cpu_temp() -> float:
    try:
        temps = psutil.sensors_temperatures()
        for key in ("cpu_thermal", "coretemp", "k10temp", "acpitz"):
            if key in temps and temps[key]:
                return temps[key][0].current
    except Exception:
        pass
    try:
        return int(open("/sys/class/thermal/thermal_zone0/temp").read().strip()) / 1000
    except Exception:
        return -1.0

def sys_snapshot() -> dict:
    mem = psutil.virtual_memory()
    return {
        "ram_used_mb": mem.used // 1024 // 1024,
        "ram_pct":     round(mem.percent, 1),
        "cpu_pct":     round(psutil.cpu_percent(interval=None), 1),
        "temp_c":      round(get_cpu_temp(), 1),
    }


# =============================================================================
# Parse test file
# =============================================================================

def strip_entities(text: str) -> str:
    return re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text).strip()

def parse_test_file(path: str) -> dict:
    mapping = {}
    current_intent = None
    in_examples = False
    counter = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = re.match(r"^- intent:\s*(\S+)", s)
            if m:
                current_intent = m.group(1)
                in_examples = False
                continue
            if s == "examples: |":
                in_examples = True
                continue
            if in_examples and current_intent and s.startswith("- "):
                counter += 1
                mapping[counter] = (current_intent, strip_entities(s[2:].strip()))
    return mapping


# =============================================================================
# Audio loading
# =============================================================================

def m4a_to_numpy(path: str):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-ar", str(SAMPLE_RATE), "-ac", "1",
             "-f", "s16le", tmp_path],
            capture_output=True, timeout=15
        )
        if r.returncode != 0:
            return None
        data = np.fromfile(tmp_path, dtype=np.int16).astype(np.float32) / 32768.0
        return data if len(data) > 0 else None
    except Exception:
        return None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# =============================================================================
# STT
# =============================================================================

def transcribe(model: WhisperModel, audio: np.ndarray) -> str:
    segments, _ = model.transcribe(
        audio,
        language                   = "en",
        beam_size                  = 1,
        best_of                    = 1,
        temperature                = 0.0,
        vad_filter                 = True,
        vad_parameters             = {"min_silence_duration_ms": 300},
        condition_on_previous_text = False,
        no_speech_threshold        = 0.4,
        word_timestamps            = False,
        initial_prompt             = (
            "Customer ordering drinks at a vending machine. "
            "Products: Coca-Cola, Pepsi, Sprite, Red Bull, Sting, Monster, "
            "7UP, Fanta, Mirinda, Aquafina, Lavie, Revive, C2, Yakult, "
            "Lipton, Nestea, Birdy, Nescafe, Cocoxim, Twister."
        ),
    )
    return " ".join(s.text.strip() for s in segments).strip().strip(".,!? ")


# =============================================================================
# Text comparison
# =============================================================================

_NUM2WORD = {
    "10": "ten", "9": "nine", "8": "eight", "7": "seven", "6": "six",
    "5": "five", "4": "four", "3": "three", "2": "two", "1": "one", "0": "zero",
}

def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    for num, word in sorted(_NUM2WORD.items(), key=lambda x: -len(x[0])):
        text = re.sub(rf'\b{num}\b', word, text)
    return re.sub(r"\s+", " ", text).strip()

def word_accuracy(expected: str, got: str) -> float:
    e = normalize(expected)
    g = normalize(got)
    if e == g:
        return 1.0
    if e.replace(" ", "") == g.replace(" ", ""):
        return 1.0
    e_words = e.split()
    g_flat  = g.replace(" ", "")
    if not e_words:
        return 1.0
    matches = sum(1 for w in e_words if w in g.split() or w.replace(" ", "") in g_flat)
    return matches / len(e_words)


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="STT Benchmark trên 243 audio files")
    parser.add_argument("--intent", type=str, help="Chỉ test câu của 1 intent")
    parser.add_argument("--max",    type=int, help="Giới hạn số câu")
    parser.add_argument("--from",   type=int, dest="from_file", help="File number bắt đầu")
    parser.add_argument("--to",     type=int, dest="to_file",   help="File number kết thúc")
    args = parser.parse_args()

    print(f"\n{'═'*90}")
    print(f"  {BOLD}STT BENCHMARK — faster-whisper/{WHISPER_MODEL} ({WHISPER_COMPUTE}){RESET}")
    print(f"  Audio dir : {AUDIO_DIR}   |   Test file : {TEST_FILE}")
    print(f"{'═'*90}")

    # Load Whisper
    print(f"\nĐang tải model...", end=" ", flush=True)
    t0 = time.time()
    model = WhisperModel(
        WHISPER_MODEL, device=WHISPER_DEVICE,
        compute_type=WHISPER_COMPUTE,
        cpu_threads=WHISPER_THREADS, num_workers=1
    )
    print(f"OK ({time.time()-t0:.1f}s)")

    # Parse test set
    sentence_map = parse_test_file(TEST_FILE)
    print(f"Test set  : {len(sentence_map)} câu\n")

    # Danh sách audio files
    files = sorted(
        [f for f in os.listdir(AUDIO_DIR) if f.endswith(".m4a") and f.split(".")[0].isdigit()],
        key=lambda x: int(x.split(".")[0])
    )
    if args.from_file:
        files = [f for f in files if int(f.split(".")[0]) >= args.from_file]
    if args.to_file:
        files = [f for f in files if int(f.split(".")[0]) <= args.to_file]
    if args.max:
        files = files[:args.max]

    psutil.cpu_percent(interval=None)  # warm up

    # ─ Header ─
    print(f"{'File':<8} {'#':<5} {'Intent':<22} {'Acc':>5}  {'Time':>5}  {'RAM':>8}  {'CPU':>5}  {'Temp':>7}")
    print("─" * 90)

    results       = []
    by_intent     = {}
    total_ok = 0

    for fname in files:
        file_num = int(fname.split(".")[0])
        sent_num = file_num + FILE_OFFSET
        if sent_num not in sentence_map:
            continue
        intent, expected = sentence_map[sent_num]
        if args.intent and intent != args.intent:
            continue

        audio = m4a_to_numpy(os.path.join(AUDIO_DIR, fname))
        if audio is None:
            print(f"{fname:<8} {sent_num:<5} {intent:<22} {RED}[load error]{RESET}")
            continue

        t_start = time.time()
        got = transcribe(model, audio)
        elapsed = time.time() - t_start

        snap = sys_snapshot()
        acc  = word_accuracy(expected, got)
        ok   = acc >= 1.0

        if ok:
            total_ok += 1

        by_intent.setdefault(intent, []).append(ok)

        color = GREEN if ok else (YELLOW if acc >= 0.75 else RED)
        temp_str = f"{snap['temp_c']:.1f}°C" if snap["temp_c"] > 0 else "  N/A"

        print(f"{fname:<8} {sent_num:<5} {intent:<22} {color}{acc*100:>4.0f}%{RESET}  "
              f"{elapsed:>4.1f}s  {snap['ram_used_mb']:>5}MB  {snap['cpu_pct']:>4.0f}%  {temp_str}")
        print(f"         {DIM}Expect: {expected}{RESET}")
        print(f"         {color}Got   : {got}{RESET}\n")

        results.append({
            "file": fname, "sentence": sent_num, "intent": intent,
            "expected": expected, "transcribed": got,
            "word_accuracy": round(acc, 4), "ok": ok,
            "time_s": round(elapsed, 3),
            "ram_used_mb": snap["ram_used_mb"], "ram_pct": snap["ram_pct"],
            "cpu_pct": snap["cpu_pct"], "temp_c": snap["temp_c"],
        })

    if not results:
        print("Không có file nào để test.")
        return

    # ─ Summary ─
    total    = len(results)
    avg_time = sum(r["time_s"] for r in results) / total
    avg_ram  = sum(r["ram_used_mb"] for r in results) / total
    avg_cpu  = sum(r["cpu_pct"] for r in results) / total
    temps    = [r["temp_c"] for r in results if r["temp_c"] > 0]
    max_temp = max(temps, default=-1)
    avg_temp = sum(temps) / len(temps) if temps else -1

    print(f"\n{'═'*90}")
    print(f"  {BOLD}TỔNG KẾT STT — per intent{RESET}")
    print(f"{'─'*90}")
    for intent, oks in sorted(by_intent.items()):
        n_ok  = sum(oks)
        n_tot = len(oks)
        pct   = n_ok / n_tot * 100
        c     = GREEN if pct >= 80 else (YELLOW if pct >= 60 else RED)
        print(f"  {c}{'✓' if pct >= 80 else ('~' if pct >= 60 else '✗')}{RESET}  "
              f"{intent:<30} {c}{n_ok}/{n_tot} ({pct:.0f}%){RESET}")

    overall = total_ok / total * 100
    c_all   = GREEN if overall >= 80 else (YELLOW if overall >= 60 else RED)

    print(f"{'─'*90}")
    print(f"  {BOLD}OVERALL : {c_all}{total_ok}/{total} ({overall:.1f}%){RESET}")
    print(f"{'─'*90}")
    print(f"  Thời gian TB   : {avg_time:.2f}s / câu")
    print(f"  RAM TB         : {avg_ram:.0f} MB")
    print(f"  CPU TB         : {avg_cpu:.1f}%")
    if avg_temp > 0:
        print(f"  Nhiệt độ TB    : {avg_temp:.1f}°C   |   Max : {max_temp:.1f}°C")
    print(f"{'═'*90}\n")

    # ─ JSON export ─
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"benchmark_stt_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": ts,
            "model": WHISPER_MODEL, "compute": WHISPER_COMPUTE,
            "total": total, "correct": total_ok,
            "accuracy": round(total_ok / total, 4),
            "avg_time_s": round(avg_time, 3),
            "avg_ram_mb": round(avg_ram, 1),
            "avg_cpu_pct": round(avg_cpu, 1),
            "avg_temp_c": round(avg_temp, 1) if avg_temp > 0 else None,
            "max_temp_c": round(max_temp, 1) if max_temp > 0 else None,
            "sentences": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"  Kết quả đã lưu : {out_path}\n")


if __name__ == "__main__":
    main()
