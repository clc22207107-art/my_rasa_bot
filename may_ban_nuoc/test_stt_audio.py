"""
test_stt_audio.py — Chạy STT trên toàn bộ file audio trong audiopart2/
Map: file N.m4a → câu thứ (N + FILE_OFFSET) trong test_nlu.yml
Ví dụ: 22.m4a → câu thứ 24 (FILE_OFFSET = 2)

Cách dùng:
    python test_stt_audio.py
    python test_stt_audio.py --show-all     # in cả câu đúng
    python test_stt_audio.py --intent greet # chỉ test 1 intent
"""

import os
import re
import sys
import subprocess
import tempfile
import argparse
import time
import warnings
warnings.filterwarnings("ignore")

import numpy as np
from faster_whisper import WhisperModel

# ── Cấu hình ────────────────────────────────────────────────
AUDIO_DIR      = "audiopart2"
TEST_FILE      = "tests/test_nlu.yml"
FILE_OFFSET    = 0          # file N → câu N trong test set
SAMPLE_RATE    = 16000

WHISPER_MODEL  = "small"
WHISPER_DEVICE = "cpu"
WHISPER_COMPUTE = "int8"
WHISPER_THREADS = 3

# ── ANSI ────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


# ============================================================
# 1. Parse test_nlu.yml → list[(sentence_num, intent, clean_text)]
# ============================================================

def strip_entities(text: str) -> str:
    """[coca](drink) → coca"""
    return re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text).strip()

def parse_test_file(path: str) -> dict[int, tuple[str, str]]:
    """Trả về {sentence_num: (intent, clean_text)}"""
    mapping = {}
    current_intent = None
    in_examples = False
    counter = 0

    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            m = re.match(r"^- intent:\s*(\S+)", stripped)
            if m:
                current_intent = m.group(1)
                in_examples = False
                continue

            if stripped == "examples: |":
                in_examples = True
                continue

            if in_examples and current_intent and stripped.startswith("- "):
                counter += 1
                raw = stripped[2:].strip()
                clean = strip_entities(raw)
                mapping[counter] = (current_intent, clean)

    return mapping


# ============================================================
# 2. Chuyển m4a → numpy float32 16kHz via ffmpeg
# ============================================================

def m4a_to_numpy(path: str) -> np.ndarray | None:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", path,
             "-ar", str(SAMPLE_RATE), "-ac", "1",
             "-f", "s16le", tmp_path],
            capture_output=True, timeout=15
        )
        if result.returncode != 0:
            return None
        data = np.fromfile(tmp_path, dtype=np.int16).astype(np.float32) / 32768.0
        return data if len(data) > 0 else None
    except Exception:
        return None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ============================================================
# 3. STT
# ============================================================

def stt(model: WhisperModel, audio: np.ndarray) -> str:
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


# ============================================================
# 4. So sánh kết quả (normalize để tránh diff về hoa/thường, dấu câu)
# ============================================================

def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)   # bỏ dấu câu
    text = re.sub(r"\s+", " ", text).strip()
    return text

def word_accuracy(expected: str, got: str) -> float:
    """Tỷ lệ word match đơn giản (không dùng WER thư viện)"""
    e_words = normalize(expected).split()
    g_words = normalize(got).split()
    if not e_words:
        return 1.0
    matches = sum(1 for w in e_words if w in g_words)
    return matches / len(e_words)


# ============================================================
# 5. Main
# ============================================================

def run(filter_intent=None, show_all=False):
    # Load model
    print(f"\nĐang tải faster-whisper/{WHISPER_MODEL} ({WHISPER_COMPUTE})...",
          end=" ", flush=True)
    t0 = time.time()
    model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE,
                         compute_type=WHISPER_COMPUTE,
                         cpu_threads=WHISPER_THREADS, num_workers=1)
    print(f"OK ({time.time()-t0:.1f}s)\n")

    # Parse test file
    sentence_map = parse_test_file(TEST_FILE)
    print(f"Test set: {len(sentence_map)} câu (offset file→câu: +{FILE_OFFSET})\n")

    # Lấy danh sách file audio
    files = sorted(
        [f for f in os.listdir(AUDIO_DIR) if f.endswith(".m4a")],
        key=lambda x: int(x.split(".")[0])
    )

    # ── Thống kê ──
    results_by_intent: dict[str, list] = {}
    total_run = 0
    total_ok  = 0
    total_time = 0.0

    print(f"{'File':<8} {'Câu#':<6} {'Intent':<22} {'Kết quả'}")
    print("─" * 90)

    for fname in files:
        file_num = int(fname.split(".")[0])
        sent_num = file_num + FILE_OFFSET

        # Bỏ qua nếu không có trong test set
        if sent_num not in sentence_map:
            continue

        intent, expected = sentence_map[sent_num]

        # Lọc theo intent nếu có
        if filter_intent and intent != filter_intent:
            continue

        # Load audio
        audio_path = os.path.join(AUDIO_DIR, fname)
        audio = m4a_to_numpy(audio_path)
        if audio is None:
            print(f"{fname:<8} ({sent_num:<4}) {intent:<22} {RED}[load lỗi]{RESET}")
            continue

        # STT
        t_start = time.time()
        got = stt(model, audio)
        elapsed = time.time() - t_start
        total_time += elapsed
        total_run += 1

        # So sánh
        acc = word_accuracy(expected, got)
        ok  = acc >= 0.7   # >=70% word match = đúng

        if ok:
            total_ok += 1

        # Lưu vào results
        if intent not in results_by_intent:
            results_by_intent[intent] = []
        results_by_intent[intent].append((sent_num, expected, got, ok, elapsed))

        # In dòng kết quả
        if show_all or not ok:
            color  = GREEN if ok else RED
            status = "✓" if ok else "✗"
            print(f"{fname:<8} ({sent_num:<4}) {intent:<22} {color}{status}{RESET} "
                  f"({elapsed:.1f}s)")
            print(f"         Expect: {DIM}{expected}{RESET}")
            print(f"         Got   : {(GREEN if ok else RED)}{got}{RESET}")
            print()
        else:
            # Chỉ in 1 dòng nếu đúng
            print(f"{fname:<8} ({sent_num:<4}) {intent:<22} {GREEN}✓{RESET} "
                  f"{DIM}({elapsed:.1f}s) {got[:50]}{RESET}")

    # ── Tổng kết ──
    print("\n" + "═" * 90)
    print(f"  {BOLD}TỔNG KẾT STT ACCURACY{RESET}")
    print("─" * 90)

    for intent, recs in sorted(results_by_intent.items()):
        n_ok  = sum(1 for *_, ok, _ in recs if ok)
        n_tot = len(recs)
        pct   = n_ok / n_tot * 100 if n_tot else 0
        color = GREEN if pct >= 80 else (YELLOW if pct >= 60 else RED)
        mark  = "✓" if pct >= 80 else ("~" if pct >= 60 else "✗")
        print(f"  {color}{mark}{RESET} {intent:<28} {color}{n_ok}/{n_tot} ({pct:.0f}%){RESET}")

    print("─" * 90)
    overall = total_ok / total_run * 100 if total_run else 0
    avg_t   = total_time / total_run if total_run else 0
    color   = GREEN if overall >= 80 else (YELLOW if overall >= 60 else RED)
    print(f"  Tổng    : {color}{BOLD}{total_ok}/{total_run} ({overall:.1f}%){RESET}")
    print(f"  TB/câu  : {avg_t:.2f}s")
    print("═" * 90 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--intent",   type=str, help="Chỉ test 1 intent")
    parser.add_argument("--show-all", action="store_true", help="In cả câu đúng")
    args = parser.parse_args()
    run(filter_intent=args.intent, show_all=args.show_all)
