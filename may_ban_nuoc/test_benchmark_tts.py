"""
test_benchmark_tts.py — Benchmark TTS trên response thực tế của bot
=====================================================================
Pipeline:
  243 câu từ test_stt.yml
    → Rasa REST webhook (bot xử lý, trả response text)
    → Piper TTS (tổng hợp giọng nói)
    → Đo RTF, synth time, audio duration, RAM, CPU, temp

Yêu cầu: Rasa server đang chạy
    Terminal 1: rasa run --enable-api --cors "*" --port 5005
    Terminal 2: rasa run actions --port 5055

Cách dùng:
    python test_benchmark_tts.py
    python test_benchmark_tts.py --intent greet
    python test_benchmark_tts.py --max 50
    python test_benchmark_tts.py --save-audio
"""

import os, re, sys, time, argparse, json, datetime, warnings
warnings.filterwarnings("ignore")

import numpy as np
import psutil
import requests
import yaml

# ── Cấu hình ─────────────────────────────────────────────────────────────────
PIPER_MODEL_PATH  = os.path.expanduser("~/piper_models/en_US-lessac-medium.onnx")
PIPER_CONFIG_PATH = os.path.expanduser("~/piper_models/en_US-lessac-medium.onnx.json")
RASA_URL          = "http://localhost:5005"
TEST_FILE         = "tests/test_stt.yml"
SENDER_ID         = "tts_benchmark"

# Các intent cần ngữ cảnh trước — gửi setup messages với cùng sender_id trước câu test
CONTEXT_SETUP = {
    "specify_size":     ["I want coca cola"],
    "specify_quantity": ["I want coca cola"],
    "remove_from_cart": ["I want coca cola"],
    "change_order":     ["I want coca cola"],
    "confirm_order":    ["I want coca cola"],
    "cancel_order":     ["I want coca cola"],
    "specify_payment":  ["I want coca cola", "confirm"],
}

# ── ANSI ──────────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"; RED   = "\033[91m"; YELLOW = "\033[93m"
CYAN   = "\033[96m"; BOLD  = "\033[1m";  DIM    = "\033[2m"; RESET = "\033[0m"


# =============================================================================
# Load test sentences từ test_stt.yml
# =============================================================================

_ENTITY_ANNOT_RE = re.compile(r'\[([^\]]+)\]\([^)]+\)')

def load_stt_sentences(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    sentences = []
    for item in data.get("nlu", []):
        intent = item.get("intent", "unknown")
        examples_raw = item.get("examples", "")
        for line in examples_raw.strip().splitlines():
            text = line.strip().lstrip("- ").strip()
            if text:
                # Strip entity annotations: [lipton](drink) → lipton
                text = _ENTITY_ANNOT_RE.sub(r'\1', text)
                sentences.append({"intent": intent, "text": text})
    return sentences


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
# Rasa — gửi câu, lấy response text
# =============================================================================

def get_bot_response(text: str, sender_id: str) -> str:
    try:
        r = requests.post(
            f"{RASA_URL}/webhooks/rest/webhook",
            json={"sender": sender_id, "message": text},
            timeout=15,
        )
        r.raise_for_status()
        return " ".join(m.get("text", "") for m in r.json() if m.get("text")).strip()
    except requests.exceptions.ConnectionError:
        print(f"\n{RED}Không kết nối được Rasa tại {RASA_URL}{RESET}")
        print("  → Terminal 1: rasa run --enable-api --cors \"*\" --port 5005")
        print("  → Terminal 2: rasa run actions --port 5055")
        sys.exit(1)
    except Exception as e:
        return ""


# =============================================================================
# TTS helpers
# =============================================================================

def clean_for_tts(text: str) -> str:
    text = re.sub(r'[\U00010000-\U0010FFFF☀-➿\U0001F300-\U0001F9FF]', '', text, flags=re.UNICODE)
    text = re.sub(r'\*+|_+', '', text)
    text = re.sub(r'[─═]+', '.', text)
    return re.sub(r'\s+', ' ', text).strip()

def synthesize(voice, text: str) -> tuple:
    clean = clean_for_tts(text)
    if not clean:
        return None, 0, 0.0, 0.0
    t_start = time.time()
    chunks = list(voice.synthesize(clean))
    synth_time = time.time() - t_start
    if not chunks:
        return None, 0, synth_time, 0.0
    sr = voice.config.sample_rate
    audio = np.concatenate([c.audio_float_array for c in chunks])
    return audio, sr, synth_time, len(audio) / sr


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="TTS Benchmark — bot responses từ 243 câu STT")
    parser.add_argument("--intent",     type=str, help="Chỉ test 1 intent")
    parser.add_argument("--max",        type=int, help="Giới hạn số câu")
    parser.add_argument("--save-audio", action="store_true",
                        help="Lưu file .wav vào tts_audio_output/")
    args = parser.parse_args()

    # ── Kiểm tra Piper model ──────────────────────────────────────────────────
    if not os.path.exists(PIPER_MODEL_PATH):
        print(f"{RED}Không tìm thấy Piper model: {PIPER_MODEL_PATH}{RESET}")
        sys.exit(1)

    # ── Kiểm tra Rasa ─────────────────────────────────────────────────────────
    print(f"Đang kiểm tra kết nối Rasa...", end=" ", flush=True)
    try:
        requests.get(f"{RASA_URL}/status", timeout=5).raise_for_status()
        print(f"{GREEN}OK{RESET}")
    except Exception:
        print(f"{RED}FAIL{RESET}")
        print("  → Terminal 1: rasa run --enable-api --cors \"*\" --port 5005")
        print("  → Terminal 2: rasa run actions --port 5055")
        sys.exit(1)

    # ── Load sentences ────────────────────────────────────────────────────────
    sentences = load_stt_sentences(TEST_FILE)
    if args.intent:
        sentences = [s for s in sentences if s["intent"] == args.intent]
    if args.max:
        sentences = sentences[:args.max]

    # ── Load Piper ────────────────────────────────────────────────────────────
    from piper import PiperVoice
    print(f"Đang tải Piper model...", end=" ", flush=True)
    t0 = time.time()
    voice = PiperVoice.load(PIPER_MODEL_PATH, config_path=PIPER_CONFIG_PATH)
    print(f"{GREEN}OK{RESET} ({time.time()-t0:.1f}s)  sample_rate={voice.config.sample_rate}Hz")

    if args.save_audio:
        os.makedirs("tts_audio_output", exist_ok=True)

    print(f"\n{'═'*90}")
    print(f"  {BOLD}TTS BENCHMARK — bot responses ({len(sentences)} câu từ {TEST_FILE}){RESET}")
    print(f"  Piper model : {PIPER_MODEL_PATH}")
    print(f"{'═'*90}")
    print(f"{'#':<5} {'Intent':<22} {'Chars':>5}  {'RTF':>5}  {'Đọc':>6}  "
          f"{'RAM':>8}  {'CPU':>5}  {'Temp':>7}")
    print("─" * 90)

    psutil.cpu_percent(interval=None)

    results = []
    by_intent = {}
    skipped = 0

    for i, item in enumerate(sentences, 1):
        intent   = item["intent"]
        user_msg = item["text"]

        # 1. Setup context nếu intent cần ngữ cảnh trước
        sid = f"tts_bench_{i}"
        for setup_msg in CONTEXT_SETUP.get(intent, []):
            get_bot_response(setup_msg, sender_id=sid)

        # 2. Lấy bot response với context đã được thiết lập
        bot_text = get_bot_response(user_msg, sender_id=sid)

        if not bot_text:
            skipped += 1
            print(f"{i:<5} {intent:<22} {'—':>5}  {YELLOW}[no response]{RESET}")
            print(f"      {DIM}Input: {user_msg}{RESET}\n")
            continue

        # 2. TTS
        audio, sr, synth_time, audio_dur = synthesize(voice, bot_text)
        snap = sys_snapshot()

        if audio is None:
            skipped += 1
            print(f"{i:<5} {intent:<22} {'—':>5}  {RED}[tts failed]{RESET}")
            print(f"      {DIM}Input: {user_msg}{RESET}\n")
            continue

        rtf       = synth_time / audio_dur if audio_dur > 0 else 0
        rtf_color = GREEN if rtf < 0.5 else (YELLOW if rtf < 1.0 else RED)
        temp_str  = f"{snap['temp_c']:.1f}°C" if snap["temp_c"] > 0 else "  N/A"

        print(f"{i:<5} {intent:<22} {len(bot_text):>5}c  "
              f"{rtf_color}{rtf:>4.2f}x{RESET}  {audio_dur:>5.2f}s  "
              f"{snap['ram_used_mb']:>5}MB  {snap['cpu_pct']:>4.0f}%  {temp_str}")
        print(f"      {DIM}Input : {user_msg}{RESET}")
        print(f"      {DIM}→     : {bot_text[:90]}{'...' if len(bot_text)>90 else ''}{RESET}\n")

        # Save audio
        wav_path = None
        if args.save_audio:
            import wave
            wav_path = f"tts_audio_output/{i:03d}_{intent}.wav"
            pcm = (audio * 32767).astype(np.int16)
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2)
                wf.setframerate(sr); wf.writeframes(pcm.tobytes())

        by_intent.setdefault(intent, []).append(
            {"synth": synth_time, "dur": audio_dur, "rtf": rtf}
        )
        results.append({
            "id": i, "intent": intent,
            "user_input": user_msg,
            "bot_response": bot_text,
            "response_chars": len(bot_text),
            "synth_time_s":   round(synth_time, 4),
            "audio_duration_s": round(audio_dur, 4),
            "rtf":            round(rtf, 4),
            "realtime_capable": rtf < 1.0,
            "ram_used_mb": snap["ram_used_mb"],
            "ram_pct":     snap["ram_pct"],
            "cpu_pct":     snap["cpu_pct"],
            "temp_c":      snap["temp_c"],
            "saved_wav":   wav_path,
        })

    # ── Summary ───────────────────────────────────────────────────────────────
    if not results:
        print("Không có kết quả nào.")
        return

    total     = len(results)
    avg_synth = sum(r["synth_time_s"] for r in results) / total
    avg_dur   = sum(r["audio_duration_s"] for r in results) / total
    avg_rtf   = sum(r["rtf"] for r in results) / total
    min_rtf   = min(r["rtf"] for r in results)
    max_rtf   = max(r["rtf"] for r in results)
    rt_cap    = sum(1 for r in results if r["realtime_capable"])
    avg_ram   = sum(r["ram_used_mb"] for r in results) / total
    avg_cpu   = sum(r["cpu_pct"] for r in results) / total
    temps     = [r["temp_c"] for r in results if r["temp_c"] > 0]
    max_temp  = max(temps, default=-1)
    avg_temp  = sum(temps) / len(temps) if temps else -1

    print(f"\n{'═'*90}")
    print(f"  {BOLD}TỔNG KẾT TTS — per intent{RESET}")
    print(f"{'─'*90}")
    for intent, data in sorted(by_intent.items()):
        a_rtf   = sum(d["rtf"] for d in data) / len(data)
        a_synth = sum(d["synth"] for d in data) / len(data)
        n       = len(data)
        c = GREEN if a_rtf < 0.5 else (YELLOW if a_rtf < 1.0 else RED)
        mark = "✓" if a_rtf < 0.5 else ("~" if a_rtf < 1.0 else "✗")
        a_dur = sum(d["dur"] for d in data) / n
        print(f"  {c}{mark}{RESET}  {intent:<30} {c}{a_rtf:.3f}x RTF{RESET}  "
              f"{a_dur:.2f}s đọc/câu  ({n} câu)")

    rtf_color = GREEN if avg_rtf < 0.5 else (YELLOW if avg_rtf < 1.0 else RED)
    print(f"{'─'*90}")
    print(f"  {BOLD}OVERALL : {rtf_color}{avg_rtf:.3f}x RTF{RESET}  "
          f"(min {min_rtf:.3f}  max {max_rtf:.3f})  bỏ qua {skipped} câu")
    print(f"{'─'*90}")
    print(f"  Real-time capable : {GREEN if rt_cap==total else YELLOW}{rt_cap}/{total}{RESET} câu RTF < 1.0")
    print(f"  Thời gian đọc TB  : {avg_dur:.3f}s / câu")
    print(f"  Synth time TB     : {avg_synth:.3f}s / câu")
    print(f"  RAM TB            : {avg_ram:.0f} MB")
    print(f"  CPU TB            : {avg_cpu:.1f}%")
    if avg_temp > 0:
        print(f"  Nhiệt độ TB       : {avg_temp:.1f}°C   |   Max : {max_temp:.1f}°C")
    if args.save_audio:
        print(f"  Audio saved       : tts_audio_output/ ({total} file .wav)")
    print(f"{'═'*90}\n")

    # ── JSON export ───────────────────────────────────────────────────────────
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"benchmark_tts_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp":        ts,
            "piper_model":      "en_US-lessac-medium",
            "source":           TEST_FILE,
            "total":            total,
            "skipped":          skipped,
            "avg_rtf":          round(avg_rtf, 4),
            "min_rtf":          round(min_rtf, 4),
            "max_rtf":          round(max_rtf, 4),
            "realtime_capable": rt_cap,
            "avg_synth_time_s": round(avg_synth, 4),
            "avg_audio_dur_s":  round(avg_dur, 4),
            "avg_ram_mb":       round(avg_ram, 1),
            "avg_cpu_pct":      round(avg_cpu, 1),
            "avg_temp_c":       round(avg_temp, 1) if avg_temp > 0 else None,
            "max_temp_c":       round(max_temp, 1) if max_temp > 0 else None,
            "sentences":        results,
        }, f, ensure_ascii=False, indent=2)
    print(f"  Kết quả đã lưu : {out_path}\n")


if __name__ == "__main__":
    main()
