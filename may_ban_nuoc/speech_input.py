"""
speech_input.py — Máy bán nước tự động (STT offline bằng Whisper)
================================================
Yêu cầu:
    pip install openai-whisper sounddevice numpy
    sudo apt install ffmpeg portaudio19-dev -y

Cách chạy (3 terminal):
    Terminal 1:  rasa run actions
    Terminal 2:  rasa run --enable-api --cors "*"
    Terminal 3:  python speech_input.py
"""

import sounddevice as sd
import numpy as np
import whisper
import requests
import time
import sys
import warnings
warnings.filterwarnings("ignore")

# ============================================================
# CẤU HÌNH — chỉnh tại đây nếu cần
# ============================================================

RASA_URL   = "http://localhost:5005/webhooks/rest/webhook"
SENDER_ID  = "voice_user"

# Ghi âm
SAMPLE_RATE       = 16000   # Hz (Whisper yêu cầu 16kHz)
SILENCE_THRESHOLD = 0.012   # Ngưỡng im lặng (0.0–1.0), tăng nếu môi trường ồn
SILENCE_DURATION  = 1.5     # Giây im lặng cuối câu → tự dừng ghi
MAX_RECORD_SEC    = 8       # Giới hạn thời gian ghi tối đa

# Whisper model:
#   "tiny"   → ~1s, kém nhất   (~75MB)
#   "base"   → ~1.5s, ổn       (~145MB)  ← khuyến nghị cho máy không GPU
#   "small"  → ~2.5s, tốt      (~465MB)
#   "medium" → ~5s,  rất tốt   (~1.5GB)
WHISPER_MODEL = "medium"

# ============================================================
# LOAD MODEL (chỉ 1 lần khi khởi động)
# ============================================================

print("=" * 52)
print("  MÁY BÁN NƯỚC — ĐIỀU KHIỂN BẰNG GIỌNG NÓI")
print("=" * 52)
print(f"  Model  : Whisper '{WHISPER_MODEL}' (offline, tiếng Việt)")
print(f"  Rasa   : {RASA_URL}")
print()
print("  Đang tải model Whisper...", end=" ", flush=True)

model = whisper.load_model(WHISPER_MODEL)
print("Xong!\n")

# ============================================================
# GHI ÂM — tự động phát hiện khi nói & im lặng
# ============================================================

def record_audio():
    """
    Ghi âm từ mic cho đến khi phát hiện im lặng sau khi đã có tiếng nói.
    Trả về numpy float32 array 1D, hoặc None nếu không có âm thanh.
    """
    chunk_dur   = 0.08                               # 80ms mỗi chunk
    chunk_size  = int(SAMPLE_RATE * chunk_dur)
    need_silent = int(SILENCE_DURATION / chunk_dur)  # số chunk im lặng cần thiết
    max_chunks  = int(MAX_RECORD_SEC / chunk_dur)

    chunks        = []
    silent_count  = 0
    speech_found  = False

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                         dtype="float32", blocksize=chunk_size) as stream:
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

            # Volume bar trực quan
            bars  = min(int(vol * 800), 25)
            label = "Đang nghe" if speech_found else "Chờ tiếng nói..."
            print(f"\r  [{'|' * bars:<25}] {label}   ", end="", flush=True)

    print("\r" + " " * 55 + "\r", end="")  # xóa dòng volume bar

    if not speech_found:
        return None

    return np.concatenate(chunks, axis=0).flatten()


# ============================================================
# SPEECH TO TEXT
# ============================================================

def stt(audio):
    """Chạy Whisper trên audio, trả về text tiếng Việt hoặc None."""
    print("  Đang nhận dạng...", end=" ", flush=True)
    t0 = time.time()

    result = model.transcribe(
        audio,
        #language                   = "vi",   # Tiếng Việt
        fp16                       = False,   # Bắt buộc False nếu không có GPU
        temperature                = 0,       # Greedy decoding — ổn định hơn
        no_speech_threshold        = 0.5,     # Bỏ qua nếu không có tiếng nói rõ
        condition_on_previous_text = False,
    )

    text    = result["text"].strip().strip(".,!? ")
    elapsed = time.time() - t0
    print(f"({elapsed:.1f}s)")

    return text if len(text) >= 2 else None


# ============================================================
# GỬI ĐẾN RASA & IN RESPONSE
# ============================================================

def chat(message):
    """Gửi message đến Rasa REST API, trả về list response."""
    try:
        r = requests.post(
            RASA_URL,
            json    = {"sender": SENDER_ID, "message": message},
            timeout = 8,
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        print("\n  Không kết nối được Rasa!")
        print("  -> Chạy: rasa run --enable-api --cors \"*\"")
        return []
    except Exception as e:
        print(f"\n  Lỗi: {e}")
        return []


def print_responses(responses):
    """In phản hồi của bot ra màn hình."""
    if not responses:
        print("  Bot: (không có phản hồi)")
        return
    print()
    for resp in responses:
        text = resp.get("text", "")
        if text:
            print(f"Bot: {text}")
    print()


# ============================================================
# VÒNG LẶP CHÍNH
# ============================================================

def main():
    # Kiểm tra kết nối Rasa lúc khởi động
    print("Kiểm tra kết nối Rasa...", end=" ", flush=True)
    responses = chat("xin chào")
    if responses:
        print("OK")
        print_responses(responses)
    else:
        print("Chưa kết nối (chạy Rasa trước rồi thử lại)")
        print()

    print("Hướng dẫn:")
    print("  • Nói vào mic, dừng lại cuối câu -> bot tự nhận")
    print("  • Nếu mic không nhận, có thể gõ thủ công")
    print("  • Nhấn Ctrl+C hoặc nói 'tạm biệt' để thoát\n")

    while True:
        print("-" * 40)
        print("Bắt đầu nói...")

        # 1. Ghi âm
        audio = record_audio()

        if audio is None:
            print("  Không nghe thấy. Gõ thủ công (hoặc Enter để thử lại):")
            manual = input("  > ").strip()
            if not manual:
                continue
            text = manual
        else:
            # 2. STT
            text = stt(audio)
            if not text:
                print("  Không nhận ra được. Thử lại hoặc gõ thủ công:")
                manual = input("  > ").strip()
                if not manual:
                    continue
                text = manual

        # 3. Hiển thị kết quả nhận dạng
        print(f"\n  Bạn nói: \"{text}\"")

        # 4. Xác nhận trước khi gửi
        choice = input("  [Enter]=gửi  [s]=sửa  [q]=thoát: ").strip().lower()
        if choice == "q":
            print("\nTạm biệt!")
            break
        elif choice == "s":
            text = input("  Nhập lại: ").strip()
            if not text:
                continue

        # 5. Gửi đến Rasa và in response
        responses = chat(text)
        print_responses(responses)

        # Thoát khi user nói goodbye
        if text.lower() in ["tạm biệt", "bye", "goodbye", "thoát"]:
            break

    print("Đã thoát.\n")


# ============================================================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nDừng bởi Ctrl+C")
        sys.exit(0)
