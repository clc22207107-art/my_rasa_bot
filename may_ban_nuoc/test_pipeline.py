"""
test_pipeline.py — Full Audio Pipeline Test
============================================
Pipeline: Piper TTS → WAV file → faster-whisper STT → Rasa NLU → CSV report

Đọc câu test từ tests/test_nlu.yml, mỗi câu chạy qua:
  1. Piper TTS  → sinh file âm thanh .wav
  2. faster-whisper  → nhận dạng giọng nói → text
  3. Rasa /model/parse  → dự đoán intent
  4. So sánh: STT vs gốc | intent dự đoán vs expected
  5. Ghi kết quả vào CSV

Yêu cầu trước khi chạy:
    Terminal 1: rasa run actions
    Terminal 2: rasa run --enable-api --cors "*"

Cách dùng:
    python test_pipeline.py                          # test toàn bộ
    python test_pipeline.py --intent ask_price       # chỉ 1 intent
    python test_pipeline.py --max 3                  # tối đa 3 câu / intent
    python test_pipeline.py --output result.csv      # custom output
    python test_pipeline.py --show-all               # in cả câu đúng
"""

import sys
import os
import re
import csv
import time
import argparse
import tempfile
import warnings
import requests
import numpy as np
import soundfile as sf
warnings.filterwarnings("ignore")

from piper import PiperVoice
from faster_whisper import WhisperModel

# ============================================================
# CONFIG — đồng bộ với speech_input.py
# ============================================================

RASA_PARSE_URL  = "http://localhost:5005/model/parse"
TEST_FILE       = "tests/test_nlu.yml"
DEFAULT_CSV     = "test_pipeline_report.csv"

PIPER_MODEL     = os.path.expanduser("~/piper_models/en_US-lessac-medium.onnx")
PIPER_CONFIG    = os.path.expanduser("~/piper_models/en_US-lessac-medium.onnx.json")

WHISPER_SIZE    = "medium"
WHISPER_DEVICE  = "cpu"
WHISPER_COMPUTE = "int8"

# Ngưỡng word-overlap để coi STT là OK
STT_THRESHOLD   = 0.6

# Intent chỉ có entity cụt, bỏ qua khỏi audio test
SKIP_INTENTS    = {"specify_ice", "specify_sugar"}

# ── ANSI colors ──────────────────────────────────────────────
GREEN  = "\033[92m"; RED    = "\033[91m"; YELLOW = "\033[93m"
CYAN   = "\033[96m"; BOLD   = "\033[1m";  DIM    = "\033[2m"; RESET  = "\033[0m"

# ============================================================
# LOAD MODELS
# ============================================================

def load_models():
    print(f"\n  [1/2] Đang tải TTS (Piper lessac-medium)...", end=" ", flush=True)
    t0 = time.time()
    tts = PiperVoice.load(PIPER_MODEL, config_path=PIPER_CONFIG)
    print(f"OK ({time.time()-t0:.1f}s)")

    print(f"  [2/2] Đang tải STT (faster-whisper/{WHISPER_SIZE})...", end=" ", flush=True)
    t0 = time.time()
    stt = WhisperModel(WHISPER_SIZE, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
    print(f"OK ({time.time()-t0:.1f}s)")

    return tts, stt

# ============================================================
# PARSE TEST FILE
# ============================================================

def strip_entities(text: str) -> str:
    """[coca](drink) → coca"""
    return re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text).strip()

def load_test_cases(path: str, filter_intent=None, max_per_intent=None) -> list:
    """Đọc test_nlu.yml → list of {intent, original, clean}"""
    data: dict[str, list] = {}
    current_intent = None
    in_examples = False

    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = re.match(r"^- intent:\s*(\S+)", stripped)
            if m:
                current_intent = m.group(1)
                data[current_intent] = []
                in_examples = False
                continue
            if stripped == "examples: |":
                in_examples = True
                continue
            if in_examples and current_intent and stripped.startswith("- "):
                original = stripped[2:].strip()
                clean = strip_entities(original)
                if len(clean) >= 3:
                    data[current_intent].append({"intent": current_intent,
                                                  "original": original,
                                                  "clean": clean})

    cases = []
    for intent, examples in sorted(data.items()):
        if intent in SKIP_INTENTS:
            continue
        if filter_intent and intent != filter_intent:
            continue
        batch = examples[:max_per_intent] if max_per_intent else examples
        cases.extend(batch)

    return cases

# ============================================================
# TTS — text → WAV file
# ============================================================

def tts_to_wav(tts_voice: PiperVoice, text: str, wav_path: str) -> bool:
    """Sinh audio từ text, lưu vào wav_path. Trả về True nếu OK."""
    try:
        parts = [chunk.audio_float_array for chunk in tts_voice.synthesize(text)]
        if not parts:
            return False
        audio = np.concatenate(parts)
        sf.write(wav_path, audio, tts_voice.config.sample_rate)
        return True
    except Exception as e:
        print(f"    TTS error: {e}")
        return False

# ============================================================
# STT — WAV file → text
# ============================================================

def stt_from_wav(stt_model: WhisperModel, wav_path: str) -> str:
    """Nhận dạng giọng nói từ file WAV. Trả về text."""
    segments, _ = stt_model.transcribe(
        wav_path,
        language                   = "en",
        beam_size                  = 5,
        best_of                    = 5,
        temperature                = [0.0, 0.2, 0.4],
        vad_filter                 = True,
        vad_parameters             = {"min_silence_duration_ms": 300},
        no_speech_threshold        = 0.4,
        condition_on_previous_text = False,
        initial_prompt             = (
            "Customer ordering drinks at a vending machine. "
            "Products: Coca-Cola, Pepsi, Sprite, Red Bull, Sting, Monster, "
            "7UP, Fanta, Mirinda, Aquafina, La Vie, Revive, C2, Yakult."
        ),
    )
    text = " ".join(seg.text.strip() for seg in segments).strip().strip(".,!? ")
    return text

# ============================================================
# RASA NLU
# ============================================================

def rasa_parse(text: str) -> tuple[str, float]:
    """Gửi text → Rasa NLU, trả về (intent, confidence)."""
    try:
        r = requests.post(RASA_PARSE_URL, json={"text": text}, timeout=8)
        r.raise_for_status()
        intent = r.json()["intent"]
        return intent["name"], round(intent["confidence"], 3)
    except requests.exceptions.ConnectionError:
        print(f"\n{RED}Không kết nối được Rasa!{RESET}")
        print("  → Terminal 1: rasa run actions")
        print("  → Terminal 2: rasa run --enable-api --cors \"*\"")
        sys.exit(1)
    except Exception:
        return "error", 0.0

# ============================================================
# COMPARISON
# ============================================================

def word_overlap(ref: str, hyp: str) -> float:
    """Tỉ lệ từ trong ref xuất hiện trong hyp (0.0 – 1.0)."""
    def norm(t):
        return set(re.sub(r'[^\w\s]', '', t.lower()).split())
    ref_words = norm(ref)
    hyp_words = norm(hyp)
    if not ref_words:
        return 0.0
    return len(ref_words & hyp_words) / len(ref_words)

# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Full pipeline test: TTS → STT → Rasa NLU")
    parser.add_argument("--intent",    type=str,  help="Chỉ test 1 intent")
    parser.add_argument("--max",       type=int,  help="Tối đa N câu mỗi intent")
    parser.add_argument("--output",    type=str,  default=DEFAULT_CSV, help="File CSV output")
    parser.add_argument("--show-all",  action="store_true", help="In cả câu đúng")
    args = parser.parse_args()

    print("=" * 60)
    print("  PIPELINE TEST: TTS → STT → RASA NLU")
    print("=" * 60)

    # Kiểm tra Rasa
    print("\n  Kiểm tra kết nối Rasa...", end=" ", flush=True)
    try:
        requests.get("http://localhost:5005/status", timeout=5).raise_for_status()
        print("OK")
    except Exception:
        print(f"{RED}FAILED{RESET}")
        print("  → Chạy: rasa run --enable-api --cors \"*\"")
        sys.exit(1)

    tts, stt = load_models()

    cases = load_test_cases(TEST_FILE, filter_intent=args.intent, max_per_intent=args.max)
    total = len(cases)
    if total == 0:
        print(f"\n{RED}Không tìm thấy câu test nào.{RESET}")
        sys.exit(1)

    print(f"\n  {total} câu test | output → {args.output}\n")
    print(f"{'─'*60}")

    results    = []
    stt_pass   = 0
    intent_pass = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = os.path.join(tmpdir, "tts_out.wav")

        for i, case in enumerate(cases, 1):
            clean    = case["clean"]
            expected = case["intent"]

            print(f"[{i:3d}/{total}] {CYAN}{expected:<28}{RESET} \"{clean[:50]}\"")

            # ── Bước 1: TTS ──────────────────────────────────
            t0 = time.time()
            tts_ok = tts_to_wav(tts, clean, wav_path)
            tts_ms = int((time.time() - t0) * 1000)

            if not tts_ok:
                print(f"         {RED}✗ TTS thất bại{RESET}")
                results.append({**case, "stt_output": "", "overlap": 0,
                                 "stt_ok": "FAIL", "predicted_intent": "error",
                                 "confidence": 0, "intent_ok": "FAIL", "status": "FAIL"})
                continue

            # ── Bước 2: STT ──────────────────────────────────
            t0 = time.time()
            stt_text = stt_from_wav(stt, wav_path)
            stt_ms = int((time.time() - t0) * 1000)
            overlap  = word_overlap(clean, stt_text)
            stt_ok   = overlap >= STT_THRESHOLD

            stt_icon = f"{GREEN}✓{RESET}" if stt_ok else f"{RED}✗{RESET}"
            print(f"         STT  {stt_icon} \"{stt_text}\"  [{overlap:.0%} overlap | {stt_ms}ms]")

            # ── Bước 3: Rasa NLU ─────────────────────────────
            predicted, conf = rasa_parse(stt_text)
            intent_ok = predicted == expected

            nlu_icon = f"{GREEN}✓{RESET}" if intent_ok else f"{RED}✗{RESET}"
            nlu_info = f"{predicted} ({conf:.2f})"
            if not intent_ok:
                nlu_info += f"  {DIM}expected: {expected}{RESET}"
            print(f"         NLU  {nlu_icon} {nlu_info}")

            if stt_ok:   stt_pass   += 1
            if intent_ok: intent_pass += 1

            results.append({
                "intent_expected":  expected,
                "original":         case["original"],
                "clean":            clean,
                "stt_output":       stt_text,
                "overlap":          f"{overlap:.2f}",
                "stt_ok":           "PASS" if stt_ok else "FAIL",
                "predicted_intent": predicted,
                "confidence":       f"{conf:.3f}",
                "intent_ok":        "PASS" if intent_ok else "FAIL",
                "status":           "PASS" if (stt_ok and intent_ok) else "FAIL",
            })

    # ── Ghi CSV ──────────────────────────────────────────────
    fieldnames = ["intent_expected", "original", "clean",
                  "stt_output", "overlap", "stt_ok",
                  "predicted_intent", "confidence", "intent_ok", "status"]
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    # ── Tóm tắt ──────────────────────────────────────────────
    full_pass = sum(1 for r in results if r["status"] == "PASS")

    def pct(n): return f"{100*n/total:.1f}%" if total else "—"
    def col(n, t): return GREEN if n/t >= 0.9 else (YELLOW if n/t >= 0.8 else RED)

    print(f"\n{'═'*60}")
    print(f"  {BOLD}KẾT QUẢ TỔNG ({total} câu){RESET}")
    print(f"{'─'*60}")
    print(f"  STT accuracy  : {col(stt_pass,total)}{stt_pass}/{total} ({pct(stt_pass)}){RESET}")
    print(f"  NLU accuracy  : {col(intent_pass,total)}{intent_pass}/{total} ({pct(intent_pass)}){RESET}")
    print(f"  Full pipeline : {col(full_pass,total)}{full_pass}/{total} ({pct(full_pass)}){RESET}")

    # In câu sai nếu có
    failed = [r for r in results if r["status"] == "FAIL"]
    if failed and not args.show_all:
        print(f"\n  {BOLD}Câu FAIL ({len(failed)}):{RESET}")
        for r in failed:
            print(f"  {RED}✗{RESET} [{r['intent_expected']}] \"{r['clean']}\"")
            if r["stt_ok"] == "FAIL":
                print(f"    STT: \"{r['stt_output']}\" (overlap={r['overlap']})")
            if r["intent_ok"] == "FAIL":
                print(f"    NLU: predicted={r['predicted_intent']} (conf={r['confidence']})")

    print(f"\n  Report: {args.output}")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()
