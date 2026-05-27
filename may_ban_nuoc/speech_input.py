"""
speech_input.py — Automatic Drink Vending Machine
==================================================
STT: faster-whisper model "medium" (offline, local)
TTS: Piper TTS v1.4.2 (offline, local)

Pipeline:
    Mic → faster-whisper → text → Rasa REST API → response → Piper TTS → Loa

Cách chạy (3 terminal):
    Terminal 1:  rasa run actions
    Terminal 2:  rasa run --enable-api --cors "*"
    Terminal 3:  python speech_input.py
"""

import sounddevice as sd
import numpy as np
import requests
import time
import sys
import os
import warnings
import re
warnings.filterwarnings("ignore")

from faster_whisper import WhisperModel
from piper import PiperVoice

# ============================================================
# CẤU HÌNH
# ============================================================

RASA_URL  = "http://localhost:5005/webhooks/rest/webhook"
SENDER_ID = "voice_user"

# ── STT: faster-whisper ──────────────────────────────────────
# Laptop : "medium" (~1.5GB, chính xác nhất trên CPU)
# Pi 5   : đổi thành "small" hoặc "base" cho nhẹ hơn
WHISPER_MODEL_SIZE = "medium"
WHISPER_DEVICE     = "cpu"
WHISPER_COMPUTE    = "int8"

# ── TTS: Piper ───────────────────────────────────────────────
PIPER_MODEL_PATH  = os.path.expanduser("~/piper_models/en_US-lessac-medium.onnx")
PIPER_CONFIG_PATH = os.path.expanduser("~/piper_models/en_US-lessac-medium.onnx.json")

# ── Ghi âm ───────────────────────────────────────────────────
SAMPLE_RATE       = 16000
SILENCE_THRESHOLD = 0.012
SILENCE_DURATION  = 1.5
MAX_RECORD_SEC    = 10

# ── Chế độ xác nhận ─────────────────────────────────────────
# True  = hỏi xác nhận trước khi gửi (dùng khi test)
# False = tự động gửi luôn (dùng khi deploy)
CONFIRM_BEFORE_SEND = False

# ============================================================
# KHỞI TẠO MODEL
# ============================================================

print("=" * 55)
print("  MÁY BÁN NƯỚC — ĐIỀU KHIỂN BẰNG GIỌNG NÓI")
print("=" * 55)

print(f"\n  [1/2] Đang tải STT (faster-whisper/{WHISPER_MODEL_SIZE})...",
      end=" ", flush=True)
t0 = time.time()
stt_model = WhisperModel(
    WHISPER_MODEL_SIZE,
    device=WHISPER_DEVICE,
    compute_type=WHISPER_COMPUTE,
)
print(f"OK ({time.time()-t0:.1f}s)")

print(f"  [2/2] Đang tải TTS (Piper lessac-medium)...",
      end=" ", flush=True)
t0 = time.time()
if not os.path.exists(PIPER_MODEL_PATH):
    print(f"\n  Không tìm thấy Piper model: {PIPER_MODEL_PATH}")
    print("  Download bằng lệnh:")
    print("    mkdir -p ~/piper_models && cd ~/piper_models")
    print("    wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx")
    print("    wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json")
    sys.exit(1)
tts_voice = PiperVoice.load(PIPER_MODEL_PATH, config_path=PIPER_CONFIG_PATH)
print(f"OK ({time.time()-t0:.1f}s)")

print("\n  Tất cả model da san sang!")
print(f"  Rasa : {RASA_URL}")
print(f"  Mode : {'Xac nhan truoc khi gui' if CONFIRM_BEFORE_SEND else 'Tu dong gui'}\n")

# ============================================================
# GHI AM
# ============================================================

def record_audio():
    """
    Ghi am tu mic den khi phat hien im lang sau tieng noi.
    Returns numpy float32 array 1D hoac None.
    """
    chunk_dur   = 0.08
    chunk_size  = int(SAMPLE_RATE * chunk_dur)
    need_silent = int(SILENCE_DURATION / chunk_dur)
    max_chunks  = int(MAX_RECORD_SEC / chunk_dur)

    chunks       = []
    silent_count = 0
    speech_found = False

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=chunk_size,
    ) as stream:
        while len(chunks) < max_chunks:
            data, _ = stream.read(chunk_size)
            chunks.append(data.copy())
            vol = float(np.abs(data).mean())

            if vol > SILENCE_THRESHOLD:
                speech_found = True
                silent_count = 0
            elif speech_found:
                silent_count += 1
                if silent_count >= need_silent:
                    break

            bars  = min(int(vol * 800), 25)
            label = "Dang nghe..." if speech_found else "Cho tieng noi..."
            print(f"\r  [{'|' * bars:<25}] {label}   ", end="", flush=True)

    print("\r" + " " * 60 + "\r", end="")

    if not speech_found:
        return None

    return np.concatenate(chunks, axis=0).flatten()


# ============================================================
# SPEECH TO TEXT — faster-whisper
# ============================================================

def stt(audio):
    """Nhan dang giong noi tieng Anh bang faster-whisper."""
    print("  Dang nhan dang...", end=" ", flush=True)
    t0 = time.time()

    segments, info = stt_model.transcribe(
        audio,
        language                   = "en",
        beam_size                  = 5,
        best_of                    = 5,
        temperature                = [0.0, 0.2, 0.4],
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

    text = " ".join(seg.text.strip() for seg in segments).strip()
    text = text.strip(".,!? ")

    elapsed = time.time() - t0
    print(f"({elapsed:.1f}s)")

    return text if len(text) >= 2 else None


# ============================================================
# TEXT TO SPEECH — Piper TTS v1.4.2
# ============================================================

def clean_text_for_tts(text: str) -> str:
    """Lam sach text truoc khi doc — bo emoji, markdown."""
    # Bo emoji (dung \U 8-digit de tranh parse nham range ASCII)
    text = re.sub(
        r'[\U00010000-\U0010FFFF\u2600-\u26FF\u2700-\u27BF'
        r'\U0001F300-\U0001F9FF\u2300-\u23FF\u2B50]',
        '', text, flags=re.UNICODE
    )
    # Bo markdown bold/italic
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'_+', '', text)
    # Bo duong ke ngang
    text = re.sub(r'[─═]+', '.', text)
    # Normalize khoang trang
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def tts(text: str):
    """Doc text thanh giong noi bang Piper va phat ra loa qua sounddevice."""
    if not text or not text.strip():
        return

    clean = clean_text_for_tts(text)
    if not clean:
        return

    try:
        # synthesize() tra ve cac AudioChunk voi audio_float_array la float32 [-1, 1]
        audio_parts = [chunk.audio_float_array for chunk in tts_voice.synthesize(clean)]
        if not audio_parts:
            return

        audio = np.concatenate(audio_parts)
        sd.play(audio, samplerate=tts_voice.config.sample_rate)
        sd.wait()

    except Exception as e:
        print(f"  TTS error: {e}")


# ============================================================
# RASA
# ============================================================

def chat(message: str) -> list:
    """Gui message den Rasa REST API."""
    try:
        r = requests.post(
            RASA_URL,
            json    = {"sender": SENDER_ID, "message": message},
            timeout = 10,
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        print("\n  Khong ket noi duoc Rasa!")
        print("  -> Terminal 1: rasa run actions")
        print("  -> Terminal 2: rasa run --enable-api --cors \"*\"")
        return []
    except Exception as e:
        print(f"\n  Loi Rasa: {e}")
        return []


def handle_responses(responses: list):
    """In response ra man hinh va doc to bang TTS."""
    if not responses:
        print("  Bot: (khong co phan hoi)")
        return

    print()
    for resp in responses:
        text = resp.get("text", "")
        if text:
            print(f"  Bot: {text}\n")
            tts(text)


# ============================================================
# VONG LAP CHINH
# ============================================================

def main():
    # Kiem tra ket noi Rasa
    print("  Kiem tra ket noi Rasa...", end=" ", flush=True)
    responses = chat("hello")
    if responses:
        print("OK\n")
        handle_responses(responses)
    else:
        print("Chua ket noi\n")
        print("  -> Chay Rasa truoc:")
        print("    Terminal 1: rasa run actions")
        print("    Terminal 2: rasa run --enable-api --cors \"*\"\n")

    print("-" * 55)
    print("  Huong dan:")
    print("  - Noi vao mic -> bot tu nhan dang va tra loi")
    print("  - Go thu cong neu mic khong nhan")
    print("  - Ctrl+C hoac noi 'goodbye' de thoat")
    print("-" * 55 + "\n")

    while True:
        print("Bat dau noi...")

        # 1. Ghi am
        audio = record_audio()

        if audio is None:
            print("  Khong nghe thay. Go thu cong (Enter de thu lai):")
            manual = input("  > ").strip()
            if not manual:
                continue
            text = manual
        else:
            # 2. STT
            text = stt(audio)
            if not text:
                print("  Khong nhan ra. Go thu cong (Enter de thu lai):")
                manual = input("  > ").strip()
                if not manual:
                    continue
                text = manual

        # 3. Hien thi text nhan dang
        print(f"\n  Ban: \"{text}\"")

        # 4. Xac nhan (tat bang CONFIRM_BEFORE_SEND = False)
        if CONFIRM_BEFORE_SEND:
            choice = input("  [Enter]=gui  [s]=sua  [q]=thoat: ").strip().lower()
            if choice == "q":
                tts("Goodbye! Thank you for using our service!")
                print("\n  Tam biet!\n")
                break
            elif choice == "s":
                text = input("  Nhap lai: ").strip()
                if not text:
                    continue

        # 5. Gui den Rasa
        print("  Dang xu ly...")
        responses = chat(text)
        handle_responses(responses)

        # 6. Thoat neu goodbye
        if text.lower() in ["goodbye", "bye", "exit", "quit"]:
            break

        print()


# ============================================================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Dung boi Ctrl+C\n")
        sys.exit(0)