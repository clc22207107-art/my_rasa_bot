"""
speech_input.py — Automatic Drink Vending Machine
==================================================
STT: faster-whisper model "medium" (offline, local)
TTS: Piper TTS (offline, local)

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
import wave
import tempfile
import warnings
import subprocess
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
WHISPER_COMPUTE    = "int8"   # int8 nhanh hơn float32 trên CPU

# ── TTS: Piper ───────────────────────────────────────────────
PIPER_MODEL_PATH  = os.path.expanduser("~/piper_models/en_US-lessac-medium.onnx")
PIPER_CONFIG_PATH = os.path.expanduser("~/piper_models/en_US-lessac-medium.onnx.json")

# ── Ghi âm ───────────────────────────────────────────────────
SAMPLE_RATE       = 16000  # Hz — faster-whisper yêu cầu 16kHz
SILENCE_THRESHOLD = 0.012  # Ngưỡng im lặng, tăng nếu môi trường ồn
SILENCE_DURATION  = 1.5    # Giây im lặng → tự dừng ghi
MAX_RECORD_SEC    = 10     # Giới hạn ghi tối đa

# ── Chế độ xác nhận ─────────────────────────────────────────
# True  = hỏi xác nhận trước khi gửi (an toàn hơn, dùng khi test)
# False = tự động gửi luôn sau khi nhận dạng (dùng khi deploy)
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
    print(f"\n  ❌ Không tìm thấy Piper model: {PIPER_MODEL_PATH}")
    print("  Chạy lệnh sau để download:")
    print("    mkdir -p ~/piper_models && cd ~/piper_models")
    print("    wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx")
    print("    wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json")
    sys.exit(1)
tts_voice = PiperVoice.load(PIPER_MODEL_PATH, config_path=PIPER_CONFIG_PATH)
print(f"OK ({time.time()-t0:.1f}s)")

print("\n  ✅ Tất cả model đã sẵn sàng!")
print(f"  Rasa : {RASA_URL}")
print(f"  Mode : {'Xác nhận trước khi gửi' if CONFIRM_BEFORE_SEND else 'Tự động gửi'}\n")

# ============================================================
# GHI ÂM
# ============================================================

def record_audio():
    """
    Ghi âm từ mic đến khi phát hiện im lặng sau tiếng nói.
    Returns numpy float32 array 1D hoặc None.
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
            label = "🎙️  Đang nghe..." if speech_found else "⏳ Chờ tiếng nói..."
            print(f"\r  [{'█' * bars:<25}] {label}   ", end="", flush=True)

    print("\r" + " " * 60 + "\r", end="")

    if not speech_found:
        return None

    return np.concatenate(chunks, axis=0).flatten()


# ============================================================
# SPEECH TO TEXT — faster-whisper
# ============================================================

def stt(audio):
    """
    Nhận dạng giọng nói tiếng Anh bằng faster-whisper.
    Returns text string hoặc None.
    """
    print("  🔍 Đang nhận dạng...", end=" ", flush=True)
    t0 = time.time()

    segments, info = stt_model.transcribe(
        audio,
        language                   = "en",
        beam_size                  = 5,
        best_of                    = 5,       # chọn kết quả tốt nhất trong 5 lần
        temperature                = [0.0, 0.2, 0.4],  # thử nhiều temperature
        vad_filter                 = True,
        vad_parameters             = {"min_silence_duration_ms": 300},
        condition_on_previous_text = False,
        no_speech_threshold        = 0.4,     # nhạy hơn với tiếng nói nhỏ
        word_timestamps            = False,
        initial_prompt             = (        # gợi ý context cho model
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
# TEXT TO SPEECH — Piper TTS
# ============================================================

def clean_text_for_tts(text: str) -> str:
    """Làm sạch text trước khi đọc — bỏ emoji, markdown, ký tự đặc biệt."""
    # Bỏ emoji
    text = re.sub(
        r'[\U00010000-\U0010ffff\u2600-\u26FF\u2700-\u27BF'
        r'\u1F300-\u1F9FF\u2300-\u23FF\u2B50⭐⚡🥤🐂👾🍵🍋🍊💧⚗️🌿🍑🥥🍶🫘🍫🥛☕🌵🌾]',
        '', text, flags=re.UNICODE
    )
    # Bỏ markdown
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'_+', '', text)
    # Bỏ dấu phân cách
    text = re.sub(r'[─═]+', '.', text)
    # Bỏ số thứ tự kiểu 1️⃣ 2️⃣
    text = re.sub(r'\d️⃣', '', text)
    # Normalize khoảng trắng
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def tts(text: str):
    """
    Đọc text thành giọng nói bằng Piper và phát ra loa.
    Hoàn toàn offline.
    """
    if not text or not text.strip():
        return

    clean = clean_text_for_tts(text)
    if not clean:
        return

    # Tạo WAV tạm
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        with wave.open(tmp_path, "wb") as wav_file:
            tts_voice.synthesize(clean, wav_file)

        # Phát bằng sox play
        subprocess.run(
            ["play", "-q", tmp_path],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        # Fallback: aplay
        try:
            subprocess.run(
                ["aplay", "-q", tmp_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            print("  ⚠️  Không phát được audio")
    except Exception as e:
        print(f"  ⚠️  TTS error: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ============================================================
# RASA
# ============================================================

def chat(message: str) -> list:
    """Gửi message đến Rasa REST API."""
    try:
        r = requests.post(
            RASA_URL,
            json    = {"sender": SENDER_ID, "message": message},
            timeout = 10,
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        print("\n  ❌ Không kết nối được Rasa!")
        print("  → Terminal 1: rasa run actions")
        print("  → Terminal 2: rasa run --enable-api --cors \"*\"")
        return []
    except Exception as e:
        print(f"\n  ❌ Lỗi Rasa: {e}")
        return []


def handle_responses(responses: list):
    """In response ra màn hình và đọc to bằng TTS."""
    if not responses:
        print("  Bot: (không có phản hồi)")
        return

    print()
    for resp in responses:
        text = resp.get("text", "")
        if text:
            print(f"  Bot: {text}\n")
            tts(text)


# ============================================================
# VÒNG LẶP CHÍNH
# ============================================================

def main():
    # Kiểm tra kết nối Rasa
    print("  Kiểm tra kết nối Rasa...", end=" ", flush=True)
    responses = chat("hello")
    if responses:
        print("✅ OK\n")
        handle_responses(responses)
    else:
        print("❌ Chưa kết nối\n")
        print("  → Chạy Rasa trước:")
        print("    Terminal 1: rasa run actions")
        print("    Terminal 2: rasa run --enable-api --cors \"*\"\n")

    print("─" * 55)
    print("  Hướng dẫn:")
    print("  • Nói vào mic → bot tự nhận dạng và trả lời")
    print("  • Gõ thủ công nếu mic không nhận")
    print("  • Ctrl+C hoặc nói 'goodbye' để thoát")
    print("─" * 55 + "\n")

    while True:
        print("🎙️  Bắt đầu nói...")

        # 1. Ghi âm
        audio = record_audio()

        if audio is None:
            print("  Không nghe thấy. Gõ thủ công (Enter để thử lại):")
            manual = input("  > ").strip()
            if not manual:
                continue
            text = manual
        else:
            # 2. STT
            text = stt(audio)
            if not text:
                print("  ❌ Không nhận ra. Gõ thủ công (Enter để thử lại):")
                manual = input("  > ").strip()
                if not manual:
                    continue
                text = manual

        # 3. Hiển thị text nhận dạng
        print(f"\n  Bạn: \"{text}\"")

        # 4. Xác nhận (tắt bằng CONFIRM_BEFORE_SEND = False)
        if CONFIRM_BEFORE_SEND:
            choice = input("  [Enter]=gửi  [s]=sửa  [q]=thoát: ").strip().lower()
            if choice == "q":
                tts("Goodbye! Thank you for using our service!")
                print("\n  Tạm biệt!\n")
                break
            elif choice == "s":
                text = input("  Nhập lại: ").strip()
                if not text:
                    continue

        # 5. Gửi đến Rasa
        print("  ⏳ Đang xử lý...")
        responses = chat(text)
        handle_responses(responses)

        # 6. Thoát nếu goodbye
        if text.lower() in ["goodbye", "bye", "exit", "quit"]:
            break

        print()


# ============================================================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Dừng bởi Ctrl+C\n")
        sys.exit(0)