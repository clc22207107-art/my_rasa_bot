"""
test_stt.py — Test riêng phần Speech-to-Text
Nhấn Enter → ghi âm → in text + thời gian nhận dạng
Ctrl+C để thoát
"""

import sounddevice as sd
import numpy as np
import time
import warnings
warnings.filterwarnings("ignore")

from faster_whisper import WhisperModel

# ── Cấu hình Whisper ────────────────────────────────────────
WHISPER_MODEL_SIZE  = "small"
WHISPER_DEVICE      = "cpu"
WHISPER_COMPUTE     = "int8"
WHISPER_CPU_THREADS = 3

# ── Cấu hình ghi âm ─────────────────────────────────────────
SAMPLE_RATE     = 16000
FRAME_MS        = 30                        # ms mỗi frame
FRAME_SAMPLES   = int(SAMPLE_RATE * FRAME_MS / 1000)  # 480 samples

CALIB_FRAMES    = 20    # 600ms đo ambient khi mở mic
SPEECH_FACTOR   = 2.0   # ngưỡng bắt đầu  = ambient_rms × 2.0
END_FACTOR      = 1.3   # ngưỡng kết thúc = ambient_rms × 1.3  (hysteresis)
SPEECH_TRIGGER  = 5     # 5×30ms = 150ms RMS cao liên tiếp → bắt đầu ghi
END_FRAMES      = 50    # 50×30ms = 1.5s  RMS thấp liên tiếp → kết thúc
MAX_RECORD_SEC  = 15

# ── Load model ──────────────────────────────────────────────
print(f"\nĐang tải faster-whisper/{WHISPER_MODEL_SIZE} ({WHISPER_COMPUTE})...",
      end=" ", flush=True)
t0 = time.time()
model = WhisperModel(WHISPER_MODEL_SIZE, device=WHISPER_DEVICE,
                     compute_type=WHISPER_COMPUTE,
                     cpu_threads=WHISPER_CPU_THREADS,
                     num_workers=1)
print(f"OK ({time.time()-t0:.1f}s)\n")


def rms(data: np.ndarray) -> float:
    return float(np.sqrt(np.mean(data.flatten().astype(np.float32) ** 2)))


def record() -> np.ndarray | None:
    max_frames  = int(MAX_RECORD_SEC * 1000 / FRAME_MS)
    max_wait    = int(15 * 1000 / FRAME_MS)   # 15s timeout chờ người nói

    all_frames        = []
    speech_count      = 0   # frame RMS cao liên tiếp
    end_count         = 0   # frame RMS thấp liên tiếp (sau khi started)
    silence_start_idx = None  # vị trí bắt đầu khoảng im lặng cuối câu
    started           = False
    wait_frames       = 0

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="int16", blocksize=FRAME_SAMPLES) as s:

        # Đo ambient noise 600ms đầu (không record vào all_frames)
        calib = [rms(s.read(FRAME_SAMPLES)[0]) for _ in range(CALIB_FRAMES)]
        ambient = float(np.mean(calib))
        speech_thr = ambient * SPEECH_FACTOR
        end_thr    = ambient * END_FACTOR
        print(f"  [Chờ tiếng nói... | ambient={ambient:.0f}  nói > {speech_thr:.0f}]",
              flush=True)

        for _ in range(max_frames):
            data, _ = s.read(FRAME_SAMPLES)
            all_frames.append(data.copy())
            level = rms(data)

            if level > speech_thr:
                speech_count += 1
                end_count     = 0
                wait_frames   = 0
                if not started and speech_count >= SPEECH_TRIGGER:
                    started = True
                    print(f"  ● Đang ghi... (level={level:.0f})          ",
                          end="\r", flush=True)
            else:
                speech_count = 0
                if started:
                    if end_count == 0:
                        silence_start_idx = len(all_frames) - 1
                    end_count += 1
                    if end_count >= END_FRAMES:
                        break
                else:
                    wait_frames += 1
                    if wait_frames >= max_wait:
                        break

    if not started:
        print("  Không nghe thấy gì.                        ")
        return None

    # Giữ lại 0.3s sau khi câu kết thúc, bỏ phần silence dài hơn
    keep = silence_start_idx + 10 if silence_start_idx else len(all_frames)
    keep = min(keep, len(all_frames))

    dur = keep * FRAME_MS / 1000
    print(f"  ✓ Ghi xong ({dur:.1f}s) → nhận dạng...        ", flush=True)
    return np.concatenate(all_frames[:keep]).flatten().astype(np.float32) / 32768.0


def stt(audio: np.ndarray):
    t0 = time.time()
    segments, info = model.transcribe(
        audio,
        language                    = "en",
        beam_size                   = 5,
        best_of                     = 5,
        temperature                 = [0.0, 0.2, 0.4],
        vad_filter                  = False,   # tắt — Silero VAD reject audio qua loa
        condition_on_previous_text  = False,
        no_speech_threshold         = 0.5,
        compression_ratio_threshold = 2.4,
        word_timestamps             = False,
        initial_prompt              = None,   # bỏ bias tên đồ uống khi test
    )
    elapsed = time.time() - t0
    text = " ".join(seg.text.strip() for seg in segments).strip().strip(".,!? ")
    return text, elapsed, info.language, info.language_probability


# ── Vòng lặp test ───────────────────────────────────────────
print("Nhấn Enter để ghi âm (Ctrl+C để thoát)\n")
count = 0
total_time = 0.0

try:
    while True:
        input("──────────────────────────\nEnter để bắt đầu ghi âm > ")
        audio = record()
        if audio is None:
            continue

        text, elapsed, lang, lang_prob = stt(audio)
        count += 1
        total_time += elapsed

        print(f"\n  Kết quả  : \"{text}\"")
        print(f"  Thời gian: {elapsed:.2f}s")
        print(f"  Ngôn ngữ : {lang} (conf={lang_prob:.2f})")
        if count > 1:
            print(f"  TB {count} lần: {total_time/count:.2f}s")
        print()

except KeyboardInterrupt:
    print(f"\nĐã test {count} lần, thời gian TB: {total_time/count:.2f}s\n" if count else "\n")
