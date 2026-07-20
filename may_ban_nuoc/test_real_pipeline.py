"""
test_real_pipeline.py — Full pipeline test với audio THẬT
Pipeline: audio file → STT → Rasa NLU → so sánh intent
Đồng thời theo dõi: RAM, CPU temp, CPU%, thời gian từng bước

Yêu cầu:
    Terminal 1: rasa run --enable-api --cors "*"

Cách dùng:
    python test_real_pipeline.py
    python test_real_pipeline.py --intent ask_price
    python test_real_pipeline.py --max 5
"""

import os, re, sys, time, subprocess, tempfile, argparse, warnings, json, datetime
warnings.filterwarnings("ignore")

import numpy as np
import requests
import psutil
from faster_whisper import WhisperModel

# ── Cấu hình ────────────────────────────────────────────────
AUDIO_DIR       = "audiokltn260"
TEST_FILE       = "tests/test_nlu.yml"
FILE_OFFSET     = 0
SAMPLE_RATE     = 16000
RASA_URL        = "http://localhost:5005/model/parse"

WHISPER_MODEL   = "small"
WHISPER_DEVICE  = "cpu"
WHISPER_COMPUTE = "int8"
WHISPER_THREADS = 1

# ── ANSI ────────────────────────────────────────────────────
GREEN  = "\033[92m"; RED   = "\033[91m"; YELLOW = "\033[93m"
CYAN   = "\033[96m"; BOLD  = "\033[1m";  DIM    = "\033[2m"; RESET = "\033[0m"


# ============================================================
# System monitoring
# ============================================================

def get_cpu_temp() -> float:
    """Đọc nhiệt độ CPU (°C). Hỗ trợ Pi và Linux thường."""
    try:
        temps = psutil.sensors_temperatures()
        for key in ("cpu_thermal", "coretemp", "k10temp", "acpitz"):
            if key in temps and temps[key]:
                return temps[key][0].current
    except Exception:
        pass
    try:
        raw = open("/sys/class/thermal/thermal_zone0/temp").read().strip()
        return int(raw) / 1000
    except Exception:
        pass
    return -1.0

def sys_snapshot() -> dict:
    mem = psutil.virtual_memory()
    return {
        "ram_used_mb": mem.used // 1024 // 1024,
        "ram_pct":     mem.percent,
        "cpu_pct":     psutil.cpu_percent(interval=None),
        "temp_c":      get_cpu_temp(),
    }


# ============================================================
# Parse test file
# ============================================================

def strip_entities(text):
    return re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text).strip()

def parse_test_file(path):
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


# ============================================================
# Audio load
# ============================================================

def m4a_to_numpy(path):
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


# ============================================================
# STT
# ============================================================

from word2number import w2n

def normalize_stt(text: str) -> str:
    """Clean punctuation artifacts from STT output before sending to NLU."""
    text = text.strip(".,!? ")
    text = re.sub(r',\s*', ' ', text)
    text = re.sub(r'(\d+)-([A-Za-z])', r'\1\2', text)
    text = re.sub(r'\s+-\s+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    words = text.split()
    converted = []
    for w in words:
        try:
            converted.append(str(w2n.word_to_num(w.lower())))
        except ValueError:
            converted.append(w)
    return " ".join(converted)

def stt(model, audio):
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
    raw = " ".join(s.text.strip() for s in segments).strip()
    return normalize_stt(raw)


# ============================================================
# So sánh STT (giống test_stt_audio.py)
# ============================================================

from num2words import num2words

def normalize(text):
    text = text.lower()
    text = re.sub(r'\b(\d+)\b', lambda m: num2words(int(m.group())), text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def word_accuracy(expected, got):
    e = normalize(expected)
    g = normalize(got)
    if e == g: return 1.0
    if e.replace(" ","") == g.replace(" ",""): return 1.0
    e_words = e.split()
    g_flat  = g.replace(" ","")
    if not e_words: return 1.0
    matches = sum(1 for w in e_words if w in g.split() or w.replace(" ","") in g_flat)
    return matches / len(e_words)


# ============================================================
# NLU + Bot response
# ============================================================

RASA_WEBHOOK = "http://localhost:5005/webhooks/rest/webhook"

def nlu_parse(text):
    try:
        r = requests.post(RASA_URL, json={"text": text}, timeout=8)
        r.raise_for_status()
        intent = r.json()["intent"]
        return intent["name"], round(intent["confidence"], 3)
    except requests.exceptions.ConnectionError:
        print(f"\n{RED}Không kết nối được Rasa! Chạy: rasa run --enable-api --cors \"*\"{RESET}")
        sys.exit(1)
    except Exception:
        return "error", 0.0

# Context setup: gửi trước các intent phụ thuộc hội thoại
# Key = intent cần test, Value = danh sách message context gửi trước
_CONTEXT_SETUP = {
    "specify_size":     ["I want sprite"],
    "specify_quantity": ["I want coca"],
    "confirm":          ["I want pepsi"],
    "deny":             ["I want monster"],
    "specify_payment":  ["I want fanta", "yes confirm"],
    "show_cart":        ["I want coca", "give me 2 sprite"],
}

def _send_webhook(sender_id, text):
    """Gửi 1 message qua webhook, bỏ qua response."""
    try:
        requests.post(RASA_WEBHOOK,
                      json={"sender": sender_id, "message": text},
                      timeout=10)
    except Exception:
        pass

def setup_context(intent, sender_id):
    """Gửi context messages trước khi test intent phụ thuộc hội thoại."""
    msgs = _CONTEXT_SETUP.get(intent, [])
    for msg in msgs:
        _send_webhook(sender_id, msg)

def get_bot_response(text, sender_id):
    """Gửi message qua webhook → lấy câu trả lời thực của bot."""
    try:
        r = requests.post(RASA_WEBHOOK,
                          json={"sender": sender_id, "message": text},
                          timeout=10)
        r.raise_for_status()
        msgs = r.json()
        if not msgs:
            return "(no response)"
        parts = [m["text"] for m in msgs if "text" in m]
        return " | ".join(parts) if parts else "(no text response)"
    except Exception:
        return "(webhook error)"


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--intent",   type=str)
    parser.add_argument("--max",      type=int)
    parser.add_argument("--sentence", type=int)
    args = parser.parse_args()

    # Kiểm tra Rasa
    try:
        requests.get("http://localhost:5005/status", timeout=5).raise_for_status()
    except Exception:
        print(f"{RED}Rasa chưa chạy! → rasa run --enable-api --cors \"*\"{RESET}")
        sys.exit(1)

    # Load Whisper
    print(f"\nĐang tải faster-whisper/{WHISPER_MODEL}...", end=" ", flush=True)
    t0 = time.time()
    model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE,
                         compute_type=WHISPER_COMPUTE,
                         cpu_threads=WHISPER_THREADS, num_workers=1)
    print(f"OK ({time.time()-t0:.1f}s)")

    sentence_map = parse_test_file(TEST_FILE)
    files = sorted(
        [f for f in os.listdir(AUDIO_DIR)
         if f.endswith(".m4a") and f.split(".")[0].isdigit()],
        key=lambda x: int(x.split(".")[0])
    )
    if args.sentence:
        files = [f for f in files if int(f.split(".")[0]) + FILE_OFFSET == args.sentence]
    elif args.max:
        files = files[:args.max]

    # Warm up CPU% reading
    psutil.cpu_percent(interval=None)

    print(f"\n{'═'*90}")
    print(f"  {BOLD}PIPELINE TEST: Real Audio → STT → NLU → Bot Response{RESET}")
    print(f"{'═'*90}")

    total = ok_stt = ok_nlu = ok_both = 0
    times_stt = []; times_nlu = []
    sys_samples = []
    run_id = int(time.time())   # sender prefix unique mỗi lần chạy
    results_log = []            # lưu kết quả từng câu để export

    for fname in files:
        file_num = int(fname.split(".")[0])
        sent_num = file_num + FILE_OFFSET
        if sent_num not in sentence_map:
            continue
        intent_expected, expected_text = sentence_map[sent_num]
        if args.intent and intent_expected != args.intent:
            continue

        audio = m4a_to_numpy(os.path.join(AUDIO_DIR, fname))
        if audio is None:
            continue

        # STT
        t1 = time.time()
        got_text = stt(model, audio)
        t_stt = time.time() - t1

        # NLU
        t2 = time.time()
        predicted_intent, conf = nlu_parse(got_text)
        t_nlu = time.time() - t2

        # Bot response — sender riêng mỗi câu, setup context nếu cần
        sender_id = f"test_{run_id}_{file_num}"
        setup_context(intent_expected, sender_id)
        bot_reply = get_bot_response(got_text, sender_id)

        snap = sys_snapshot()

        stt_ok    = word_accuracy(expected_text, got_text) >= 1.0
        # out_of_scope: chấp nhận cả nlu_fallback vì cả hai đều → từ chối đúng
        if intent_expected == "out_of_scope":
            intent_ok = predicted_intent in ("out_of_scope", "nlu_fallback")
        else:
            intent_ok = predicted_intent == intent_expected
        # Full pipeline: NLU đúng là đủ (STT sai nhưng NLU đúng vẫn tính pass)
        pipeline_ok = intent_ok

        total += 1
        if stt_ok:      ok_stt  += 1
        if intent_ok:   ok_nlu  += 1
        if pipeline_ok: ok_both += 1
        times_stt.append(t_stt)
        times_nlu.append(t_nlu)
        sys_samples.append(snap)
        results_log.append({
            "file": fname, "sentence": sent_num,
            "expected_intent": intent_expected, "expected_text": expected_text,
            "stt_text": got_text, "predicted_intent": predicted_intent,
            "confidence": conf, "stt_ok": stt_ok, "intent_ok": intent_ok,
            "stt_time": round(t_stt, 3), "nlu_time": round(t_nlu, 3),
            "bot_reply": bot_reply,
        })

        stt_mark  = f"{GREEN}✓{RESET}" if stt_ok    else f"{RED}✗{RESET}"
        nlu_mark  = f"{GREEN}✓{RESET}" if intent_ok else f"{RED}✗{RESET}"
        pipe_mark = f"{GREEN}✓{RESET}" if pipeline_ok else f"{RED}✗{RESET}"
        temp_str  = f"{snap['temp_c']:.1f}°C" if snap['temp_c'] > 0 else " N/A"

        print(f"\n{'─'*90}")
        print(f"[{fname}] {CYAN}{intent_expected}{RESET}  "
              f"STT:{stt_mark} NLU:{nlu_mark} PIPE:{pipe_mark}  "
              f"stt={t_stt:.1f}s nlu={t_nlu:.2f}s  "
              f"RAM={snap['ram_used_mb']}MB  {temp_str}  CPU={snap['cpu_pct']:.0f}%")
        print(f"  Expect : {DIM}{expected_text}{RESET}")
        stt_col = GREEN if stt_ok else RED
        print(f"  STT    : {stt_col}{got_text}{RESET}")
        nlu_col = GREEN if intent_ok else RED
        print(f"  NLU    : {nlu_col}{predicted_intent} ({conf:.2f}){RESET}")
        # Cắt bot reply dài để không spam terminal
        reply_preview = bot_reply[:500] + "..." if len(bot_reply) > 500 else bot_reply
        print(f"  Bot    : {DIM}{reply_preview}{RESET}")

    if total == 0:
        print("Không có file audio nào để test.")
        return

    # ── Tóm tắt ──────────────────────────────────────────────
    avg_stt  = sum(times_stt) / len(times_stt)
    avg_nlu  = sum(times_nlu) / len(times_nlu)
    avg_ram  = sum(s["ram_used_mb"] for s in sys_samples) / len(sys_samples)
    max_temp = max((s["temp_c"] for s in sys_samples if s["temp_c"] > 0), default=-1)
    avg_temp = sum(s["temp_c"] for s in sys_samples if s["temp_c"] > 0)
    n_temp   = sum(1 for s in sys_samples if s["temp_c"] > 0)

    def pct(n): return f"{100*n/total:.1f}%"
    def col(n): return GREEN if n/total >= 0.9 else (YELLOW if n/total >= 0.8 else RED)

    print(f"\n{'═'*80}")
    print(f"  {BOLD}KẾT QUẢ ({total} câu){RESET}")
    print(f"{'─'*80}")
    print(f"  STT accuracy   : {col(ok_stt)}{ok_stt}/{total} ({pct(ok_stt)}){RESET}  ← transcript khớp expected")
    print(f"  NLU accuracy   : {col(ok_nlu)}{ok_nlu}/{total} ({pct(ok_nlu)}){RESET}  ← intent đúng từ STT output")
    print(f"  Full pipeline  : {col(ok_both)}{ok_both}/{total} ({pct(ok_both)}){RESET}  ← NLU đúng (kể cả khi STT sai)")
    print(f"{'─'*80}")
    print(f"  TB thời gian STT : {avg_stt:.2f}s/câu")
    print(f"  TB thời gian NLU : {avg_nlu:.3f}s/câu")
    print(f"  Tổng TB / câu    : {avg_stt+avg_nlu:.2f}s")
    print(f"{'─'*80}")
    print(f"  RAM trung bình : {avg_ram:.0f} MB")
    if max_temp > 0:
        print(f"  Nhiệt độ TB    : {avg_temp/n_temp:.1f}°C")
        print(f"  Nhiệt độ max   : {max_temp:.1f}°C")
    print(f"{'═'*80}\n")

    # ── Lưu kết quả ra file ──────────────────────────────────
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"test_results_{ts}.json"
    summary = {
        "timestamp": ts, "total": total,
        "stt_accuracy": round(ok_stt / total, 4) if total else 0,
        "nlu_accuracy": round(ok_nlu / total, 4) if total else 0,
        "pipeline_accuracy": round(ok_both / total, 4) if total else 0,
        "avg_stt_time": round(avg_stt, 3),
        "avg_nlu_time": round(avg_nlu, 3),
        "sentences": results_log,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"  Kết quả đã lưu : {out_path}\n")


if __name__ == "__main__":
    main()
