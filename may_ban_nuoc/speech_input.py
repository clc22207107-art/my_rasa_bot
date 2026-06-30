"""
speech_input.py — Automatic Drink Vending Machine
==================================================
STT  : faster-whisper "base" int8 greedy (offline, ~2-3s trên Pi 5)
TTS  : Piper TTS v1.4.2 (offline, local)
Wake : openWakeWord — nhiều wake word song song, 100% offline
VAD  : webrtcvad (phát hiện kết thúc câu, offline)

Pipeline:
    [idle]   Mic → openWakeWord → nghe thấy BẤT KỲ wake word nào
    [beep]   Phát tiếng bíp xác nhận
    [record] Mic → webrtcvad → im lặng ~390ms → dừng ghi
    [stt]    Audio → faster-whisper → text
    [bot]    text → Rasa REST → response → Piper TTS → loa
    → quay lại [idle]

Train wake word tùy chỉnh (Google Colab, miễn phí):
    Xem hướng dẫn trong WAKE_WORD_MODELS bên dưới.

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
WHISPER_MODEL_SIZE = "base"   # medium=15s → base=~2-3s trên Pi 5
WHISPER_DEVICE     = "cpu"
WHISPER_COMPUTE    = "int8"   # bắt buộc trên Pi (giảm RAM + tăng tốc)
WHISPER_CPU_THREADS = 3       # giới hạn thread để tránh scheduler overhead

# ── TTS: Piper ───────────────────────────────────────────────
PIPER_MODEL_PATH  = os.path.expanduser("~/piper_models/en_US-lessac-medium.onnx")
PIPER_CONFIG_PATH = os.path.expanduser("~/piper_models/en_US-lessac-medium.onnx.json")

# ── Wake word: openWakeWord (100% offline, nhiều phrase cùng lúc) ────────────
#
# Mỗi file .onnx = 1 wake word phrase.
# Hệ thống kích hoạt khi BẤT KỲ model nào vượt ngưỡng WAKE_THRESHOLD.
#
# Cách train model tùy chỉnh trên Google Colab (miễn phí, ~10 phút):
#   1. Mở Google Colab → New notebook
#   2. Chạy:
#        !pip install openwakeword TTS
#        !python -m openwakeword.train --training_phrase "hey vendor" \
#            --output_dir "/content/wake_models" --n_epochs 50
#        !python -m openwakeword.train --training_phrase "ok vendy" \
#            --output_dir "/content/wake_models" --n_epochs 50
#        !python -m openwakeword.train --training_phrase "hi machine" \
#            --output_dir "/content/wake_models" --n_epochs 50
#   3. Download các file .onnx → chép vào thư mục wake_models/
#
# Trong lúc chờ train xong, dùng model built-in để test:
WAKE_WORD_MODELS = [
    # --- Custom models (sau khi train trên Colab) ---
    # "wake_models/hey_vendor.onnx",
    # "wake_models/ok_vendy.onnx",
    # "wake_models/hi_machine.onnx",

    # --- Built-in models (dùng tạm để test ngay) ---
    "hey_jarvis",   # nói "hey jarvis"
    "alexa",        # nói "alexa"
]

WAKE_THRESHOLD = 0.3   # hạ xuống để nhận giọng non-native (0.3–0.4 phù hợp)

# ── VAD end-of-speech: webrtcvad ─────────────────────────────
VAD_AGGRESSIVENESS = 3   # 3 = lọc nhiễu nền tốt nhất, phân biệt rõ nói/không nói
VAD_FRAME_MS       = 30  # chỉ dùng 10 / 20 / 30
VAD_VOICED_TRIGGER = 3   # số frame có tiếng nói để bắt đầu ghi (3×30ms = 90ms)
VAD_SILENCE_END    = 8   # số frame im lặng để kết thúc (8×30ms = 240ms)
MAX_RECORD_SEC     = 10

SAMPLE_RATE = 16000

# ── Chế độ xác nhận ─────────────────────────────────────────
CONFIRM_BEFORE_SEND = False


# ============================================================
# KHỞI TẠO MODEL
# ============================================================

print("=" * 55)
print("  MÁY BÁN NƯỚC — ĐIỀU KHIỂN BẰNG GIỌNG NÓI")
print("=" * 55)

print(f"\n  [1/3] Đang tải STT (faster-whisper/{WHISPER_MODEL_SIZE})...",
      end=" ", flush=True)
t0 = time.time()
stt_model = WhisperModel(WHISPER_MODEL_SIZE, device=WHISPER_DEVICE,
                         compute_type=WHISPER_COMPUTE,
                         cpu_threads=WHISPER_CPU_THREADS,
                         num_workers=1)
print(f"OK ({time.time()-t0:.1f}s)")

print("  [2/3] Đang tải TTS (Piper lessac-medium)...", end=" ", flush=True)
t0 = time.time()
if not os.path.exists(PIPER_MODEL_PATH):
    print(f"\n  Không tìm thấy: {PIPER_MODEL_PATH}")
    print("  Download:\n    mkdir -p ~/piper_models && cd ~/piper_models")
    print("    wget .../en_US-lessac-medium.onnx")
    sys.exit(1)
tts_voice = PiperVoice.load(PIPER_MODEL_PATH, config_path=PIPER_CONFIG_PATH)
print(f"OK ({time.time()-t0:.1f}s)")

print("  [3/3] Đang tải Wake word models...", end=" ", flush=True)
t0 = time.time()
try:
    from openwakeword.model import Model as OWWModel
    oww_model = OWWModel(wakeword_models=WAKE_WORD_MODELS,
                         inference_framework="onnx")
    # Tên hiển thị từ key của model
    wake_labels = [k.replace("_", " ") for k in oww_model.models.keys()]
    WAKE_ENABLED = True
    print(f"OK ({time.time()-t0:.1f}s)")
    print(f"  Wake words: {wake_labels}")
except Exception as e:
    print(f"SKIP\n  Lỗi: {e}")
    WAKE_ENABLED = False
    oww_model = None
    wake_labels = []

try:
    import webrtcvad as _wv
    VAD_ENABLED = True
except ImportError:
    VAD_ENABLED = False

print(f"\n  Rasa      : {RASA_URL}")
if WAKE_ENABLED:
    print(f"  Wake words: {' | '.join(wake_labels)}")
else:
    print("  Wake word : TẮT (luôn lắng nghe)")
print(f"  VAD       : {'webrtcvad (chính xác)' if VAD_ENABLED else 'volume threshold'}\n")


# ============================================================
# TIẾNG BÍP XÁC NHẬN
# ============================================================

def play_beep(freq: int = 880, duration: float = 0.12):
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    sd.play((0.25 * np.sin(2 * np.pi * freq * t)).astype(np.float32),
            samplerate=SAMPLE_RATE)
    sd.wait()


# ============================================================
# PHASE 1 — WAKE WORD (Porcupine)
# ============================================================

def wait_for_wake_word():
    """
    Đọc mic liên tục theo chunk 80ms và chạy tất cả wake word models.
    Kích hoạt khi BẤT KỲ model nào vượt WAKE_THRESHOLD.
    CPU thấp khi idle (~2-5% trên Pi 5).
    """
    chunk = 1280  # 80ms tại 16kHz — cố định theo openWakeWord
    labels_str = " | ".join(f'"{w}"' for w in wake_labels)
    print(f"  Chờ wake word — nói {labels_str}...", flush=True)

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="int16", blocksize=chunk) as stream:
        while True:
            pcm, _ = stream.read(chunk)
            scores = oww_model.predict(pcm.flatten())
            best_key   = max(scores, key=scores.get)
            best_score = scores[best_key]

            # Hiển thị thanh mức độ nhận diện của model tốt nhất
            bars = min(int(best_score * 30), 30)
            print(f"\r  [{('█' * bars):<30}] {best_key}: {best_score:.2f} ",
                  end="", flush=True)

            if best_score >= WAKE_THRESHOLD:
                print(f"\r  ✓ Wake word nhận ra: \"{best_key.replace('_', ' ')}\" "
                      f"(score={best_score:.2f}){' ' * 20}")
                return


# ============================================================
# PHASE 2A — GHI ÂM VỚI VAD (webrtcvad)
# ============================================================

def record_with_vad() -> np.ndarray | None:
    """
    Ghi âm sau wake word.
    Dùng webrtcvad để phát hiện chính xác khi người dùng nói xong.
    Dừng sau ~390ms im lặng liên tiếp.
    """
    import webrtcvad
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    frame_samples = int(SAMPLE_RATE * VAD_FRAME_MS / 1000)   # 480 samples
    max_frames    = int(MAX_RECORD_SEC * 1000 / VAD_FRAME_MS)

    all_frames    = []
    voiced_count  = 0
    silence_count = 0
    speech_started = False

    wait_frames = 0
    max_wait = int(SESSION_IDLE_TIMEOUT * 1000 / VAD_FRAME_MS)
    print("  Đang chờ...", flush=True)

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="int16", blocksize=frame_samples) as stream:
        for _ in range(max_frames):
            data, _ = stream.read(frame_samples)
            all_frames.append(data.copy())
            is_speech = vad.is_speech(data.tobytes(), SAMPLE_RATE)

            if is_speech:
                voiced_count  += 1
                silence_count  = 0
                wait_frames    = 0
                if not speech_started and voiced_count >= VAD_VOICED_TRIGGER:
                    speech_started = True
                    print("  [Đang ghi...]", flush=True)
            elif speech_started:
                silence_count += 1
                if silence_count >= VAD_SILENCE_END:
                    break  # kết thúc câu
            else:
                voiced_count = 0
                wait_frames += 1
                if not speech_started and wait_frames >= max_wait:
                    break  # timeout — không ai nói trong 12s

    if not speech_started:
        return None

    return np.concatenate(all_frames).flatten().astype(np.float32) / 32768.0


# ============================================================
# PHASE 2B — GHI ÂM VOLUME THRESHOLD (fallback)
# ============================================================

def record_with_threshold() -> np.ndarray | None:
    SILENCE_THRESHOLD = 0.012
    SILENCE_DURATION  = 1.5
    chunk_size  = int(SAMPLE_RATE * 0.08)
    need_silent = int(SILENCE_DURATION / 0.08)
    chunks, silent_count, speech_found = [], 0, False

    print("  Đang lắng nghe...", flush=True)
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="float32", blocksize=chunk_size) as stream:
        while len(chunks) < int(MAX_RECORD_SEC / 0.08):
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
    return np.concatenate(chunks).flatten() if speech_found else None


def record_audio() -> np.ndarray | None:
    return record_with_vad() if VAD_ENABLED else record_with_threshold()


# ============================================================
# SPEECH TO TEXT — faster-whisper
# ============================================================

def stt(audio: np.ndarray) -> str | None:
    print("  Nhận dạng...", end=" ", flush=True)
    t0 = time.time()
    segments, _ = stt_model.transcribe(
        audio,
        language                   = "en",
        beam_size                  = 1,      # greedy decode, nhanh gấp ~3-5x so với beam_size=5
        best_of                    = 1,      # không sample nhiều lần
        temperature                = 0.0,   # 1 lần duy nhất, không fallback
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
    text = " ".join(s.text.strip() for s in segments).strip().strip(".,!? ")
    print(f"({time.time()-t0:.1f}s)")
    return text if len(text) >= 2 else None


# ============================================================
# TEXT TO SPEECH — Piper
# ============================================================

def clean_for_tts(text: str) -> str:
    text = re.sub(r'[\U00010000-\U0010FFFF☀-➿\U0001F300-\U0001F9FF]',
                  '', text, flags=re.UNICODE)
    text = re.sub(r'\*+|_+', '', text)
    text = re.sub(r'[─═]+', '.', text)
    return re.sub(r'\s+', ' ', text).strip()


def tts(text: str):
    clean = clean_for_tts(text)
    if not clean:
        return
    try:
        parts = [c.audio_float_array for c in tts_voice.synthesize(clean)]
        if parts:
            sd.play(np.concatenate(parts), samplerate=tts_voice.config.sample_rate)
            sd.wait()
            time.sleep(0.4)  # chờ âm thanh tan trong phòng
    except Exception as e:
        print(f"  TTS error: {e}")


# ============================================================
# RASA
# ============================================================

def chat(message: str) -> list:
    try:
        r = requests.post(RASA_URL,
                          json={"sender": SENDER_ID, "message": message},
                          timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        print("  Không kết nối Rasa! Chạy Terminal 1+2 trước.")
        return []
    except Exception as e:
        print(f"  Lỗi Rasa: {e}")
        return []


def handle_responses(responses: list):
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

SESSION_IDLE_TIMEOUT = 17  # giây không nói → đóng phiên tự động

def run_session():
    """
    Chạy 1 phiên hội thoại sau khi wake word được kích hoạt.
    Người dùng hỏi liên tiếp không cần nói lại wake word.
    Phiên kết thúc khi:
      - Người dùng nói "goodbye / bye"
      - Không có tiếng nói trong SESSION_IDLE_TIMEOUT giây (người rời đi)
    Trả về True nếu kết thúc bình thường, False nếu timeout (tự rời đi).
    """
    print(f"\n{'─'*45}")
    print("  Phiên mới bắt đầu. Hỏi ngay đi!")
    print(f"  (Tự đóng sau {SESSION_IDLE_TIMEOUT}s nếu không nói gì)")
    print(f"{'─'*45}\n")

    while True:
        audio = record_audio()

        if audio is None:
            # Hết SESSION_IDLE_TIMEOUT giây không nói — người đã rời đi
            print("  Không có hoạt động — đóng phiên.\n")
            return False

        text = stt(audio)
        if not text:
            # Nhận dạng thất bại — tiếp tục chờ trong phiên, không đóng
            print("  Không nghe rõ, thử lại...\n")
            continue

        print(f"\n  Bạn: \"{text}\"")
        print("  Xử lý...")
        handle_responses(chat(text))

        # Kết thúc phiên chủ động khi người dùng tạm biệt
        if any(w in text.lower() for w in ["goodbye", "bye", "exit", "quit", "done"]):
            print("  Phiên kết thúc. Hẹn gặp lại!\n")
            return True

        print()


def main():
    print("  Kiểm tra Rasa...", end=" ", flush=True)
    responses = chat("hello")
    if responses:
        print("OK\n")
        handle_responses(responses)
    else:
        print("Chưa kết nối\n")

    print("-" * 55)
    if WAKE_ENABLED:
        labels = " / ".join(f'"{w}"' for w in wake_labels)
        print(f"  Nói {labels} để bắt đầu phiên mới")
    else:
        print("  Tự động lắng nghe (không có wake word)")
    print("  Ctrl+C để thoát")
    print("-" * 55 + "\n")

    while True:
        # ── Chờ wake word để mở phiên ──────────────────────
        if WAKE_ENABLED:
            wait_for_wake_word()
            play_beep()

        # ── Chạy phiên hội thoại ────────────────────────────
        run_session()

        # Chờ âm thanh TTS tan hết trong phòng trước khi nghe lại
        # (tránh TTS trigger nhầm wake word)
        time.sleep(1.5)
        if WAKE_ENABLED:
            oww_model.reset()  # xóa buffer nội bộ của model


# ============================================================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Dừng bởi Ctrl+C\n")
        sys.exit(0)
