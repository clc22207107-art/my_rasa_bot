"""
test_benchmark_tts.py — Benchmark TTS (Text-to-Speech) dùng Piper
Đo thời gian tổng hợp âm thanh, RTF, chất lượng audio.

Metrics:
  - Synthesis time (giây)
  - Audio duration (giây)
  - RTF = synthesis_time / audio_duration (RTF < 1 = real-time capable)
  - RAM used (MB), RAM %, CPU %, CPU temperature (°C)

Yêu cầu: KHÔNG cần Rasa, chỉ cần Piper model

Cách dùng:
    python test_benchmark_tts.py
    python test_benchmark_tts.py --save-audio    # lưu .wav để nghe kiểm tra
    python test_benchmark_tts.py --category menu # chỉ test 1 nhóm câu
"""

import os, re, sys, time, argparse, json, datetime, warnings
warnings.filterwarnings("ignore")

import numpy as np
import psutil

# ── Cấu hình Piper ────────────────────────────────────────────────────────────
PIPER_MODEL_PATH  = os.path.expanduser("~/piper_models/en_US-lessac-medium.onnx")
PIPER_CONFIG_PATH = os.path.expanduser("~/piper_models/en_US-lessac-medium.onnx.json")

# ── ANSI ──────────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"; RED    = "\033[91m"; YELLOW = "\033[93m"
CYAN   = "\033[96m"; BOLD   = "\033[1m";  DIM    = "\033[2m"; RESET = "\033[0m"

# =============================================================================
# Tập câu test TTS — đại diện cho các loại response thực tế của bot
# =============================================================================

TTS_TEST_CASES = [
    # Greeting / basic
    {"id": 1,  "category": "greeting",
     "text": "Hello! Welcome to the vending machine. Say menu to see drinks, or tell me what you want!"},
    {"id": 2,  "category": "greeting",
     "text": "Goodbye! Thank you for using our service. See you again!"},

    # Menu / product info
    {"id": 3,  "category": "menu",
     "text": "Menu: C2, Lipton Peach Tea, Zero Degree Green Tea, Coca-Cola, Pepsi, Sprite, Fanta, Red Bull, Sting, Monster, Aquafina, Lavie, Yakult, Revive."},
    {"id": 4,  "category": "product",
     "text": "Coca-Cola is available in 330ml for 12,000 VND, 500ml for 15,000 VND, and 1.5 liters for 28,000 VND."},
    {"id": 5,  "category": "product",
     "text": "Sting Energy Drink contains: water, sugar, citric acid, taurine, caffeine, B vitamins, natural flavors."},
    {"id": 6,  "category": "product",
     "text": "Red Bull has 80mg of caffeine per 250ml can, comparable to a cup of coffee."},
    {"id": 7,  "category": "product",
     "text": "Healthy and low-sugar options include Zero Degree Green Tea, C2, Lipton, Dr Thanh, and Oolong Tea."},

    # Order flow
    {"id": 8,  "category": "order",
     "text": "Added to cart: 2 times Coca-Cola 330ml at 12,000 VND each. Total so far: 24,000 VND."},
    {"id": 9,  "category": "order",
     "text": "Your cart: 2 Coca-Cola 330ml, 1 Pepsi 500ml. Total: 39,000 VND. Say confirm to place your order."},
    {"id": 10, "category": "order",
     "text": "Order confirmed! Please proceed with payment. You can pay by cash, card, MoMo, ZaloPay, or QR code."},
    {"id": 11, "category": "order",
     "text": "Payment successful! Your drink is being dispensed. Thank you for your purchase!"},
    {"id": 12, "category": "order",
     "text": "Order cancelled. Would you like to order something else?"},

    # Cart management
    {"id": 13, "category": "cart",
     "text": "Removed Coca-Cola from your cart. Remaining: 1 Pepsi 500ml. Total: 15,000 VND."},
    {"id": 14, "category": "cart",
     "text": "Your cart has been cleared. What would you like to order?"},

    # Payment
    {"id": 15, "category": "payment",
     "text": "We accept: Cash, Visa and Mastercard, ATM card, MoMo, ZaloPay, VNPay, and QR Code. Which method would you prefer?"},
    {"id": 16, "category": "payment",
     "text": "Please scan the QR code on the screen to complete your payment of 27,000 Vietnamese dong."},

    # Comparison
    {"id": 17, "category": "compare",
     "text": "Coca-Cola versus Pepsi: Coca-Cola starts from 12,000 dong and Pepsi from 10,000 dong, so Pepsi is cheaper. Both contain caffeine and sugar. Pepsi is more popular with over 1,000 units sold."},

    # Diet / health
    {"id": 18, "category": "health",
     "text": "For sugar-free and low-calorie options, I recommend Zero Degree Green Tea, C2 Green Tea, or Lipton Peach Tea. All are lightly sweetened or unsweetened."},

    # Support / complaints
    {"id": 19, "category": "support",
     "text": "Sorry to hear that! Please press the maintenance button on the machine panel or contact our support team. We apologize for the inconvenience."},
    {"id": 20, "category": "support",
     "text": "For refunds, please press the refund button or contact support at the number displayed on the machine. Refunds are processed within 24 hours."},
    {"id": 21, "category": "support",
     "text": "Your order is being processed. The drink should dispense within a few seconds. If nothing comes out, please press the help button."},

    # Misc
    {"id": 22, "category": "misc",
     "text": "A digital receipt has been sent via QR code. For a printed receipt, please press the receipt button on the machine panel."},
    {"id": 23, "category": "misc",
     "text": "Please enter your promo code at the payment screen. Discounts are applied automatically at checkout."},
    {"id": 24, "category": "misc",
     "text": "Sorry, I don't have access to your order history yet. Please tell me what you would like to order today."},

    # Stress test — câu dài
    {"id": 25, "category": "stress",
     "text": ("Our top selling drinks this week are Monster Energy, Red Bull, Sting, Coca-Cola, and Pepsi. "
              "Monster Energy has the highest caffeine content at 160mg per can and costs 25,000 VND. "
              "Red Bull is 80mg caffeine at 20,000 VND. Sting is 75mg at 8,000 VND. "
              "Would you like to order any of these?")},
]


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
# TTS helpers
# =============================================================================

def clean_for_tts(text: str) -> str:
    text = re.sub(r'[\U00010000-\U0010FFFF☀-➿\U0001F300-\U0001F9FF]', '', text, flags=re.UNICODE)
    text = re.sub(r'\*+|_+', '', text)
    text = re.sub(r'[─═]+', '.', text)
    return re.sub(r'\s+', ' ', text).strip()

def synthesize(voice, text: str) -> tuple:
    """
    Tổng hợp giọng nói.
    Trả về (audio_array, sample_rate, synth_time_s, audio_duration_s)
    """
    clean = clean_for_tts(text)
    t_start = time.time()
    chunks = list(voice.synthesize(clean))
    synth_time = time.time() - t_start

    if not chunks:
        return None, 0, synth_time, 0

    sr = voice.config.sample_rate
    audio = np.concatenate([c.audio_float_array for c in chunks])
    audio_duration = len(audio) / sr
    return audio, sr, synth_time, audio_duration


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="TTS Benchmark — Piper TTS")
    parser.add_argument("--save-audio", action="store_true",
                        help="Lưu file .wav cho mỗi câu vào thư mục tts_audio_output/")
    parser.add_argument("--category", type=str, help="Chỉ test 1 nhóm (greeting/menu/order/...)")
    parser.add_argument("--play",     action="store_true",
                        help="Phát audio qua loa (cần sounddevice)")
    args = parser.parse_args()

    # Kiểm tra model
    if not os.path.exists(PIPER_MODEL_PATH):
        print(f"{RED}Không tìm thấy Piper model: {PIPER_MODEL_PATH}{RESET}")
        print(f"  Download: mkdir -p ~/piper_models && cd ~/piper_models")
        print(f"  wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx")
        print(f"  wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json")
        sys.exit(1)

    from piper import PiperVoice
    if args.play:
        import sounddevice as sd

    print(f"\n{'═'*90}")
    print(f"  {BOLD}TTS BENCHMARK — Piper en_US-lessac-medium{RESET}")
    print(f"  Model : {PIPER_MODEL_PATH}")
    print(f"{'═'*90}")

    # Load voice
    print(f"\nĐang tải Piper model...", end=" ", flush=True)
    t0 = time.time()
    voice = PiperVoice.load(PIPER_MODEL_PATH, config_path=PIPER_CONFIG_PATH)
    print(f"OK ({time.time()-t0:.1f}s)")
    print(f"  Sample rate: {voice.config.sample_rate} Hz\n")

    # Filter test cases
    test_cases = TTS_TEST_CASES
    if args.category:
        test_cases = [t for t in test_cases if t["category"] == args.category]

    # Output dir nếu --save-audio
    if args.save_audio:
        os.makedirs("tts_audio_output", exist_ok=True)

    psutil.cpu_percent(interval=None)

    results = []
    by_category = {}

    print(f"{'ID':>3}  {'Category':<12}  {'Chars':>5}  {'Synth':>6}  {'Audio':>6}  "
          f"{'RTF':>5}  {'RAM':>8}  {'CPU':>5}  {'Temp':>7}")
    print("─" * 90)

    for tc in test_cases:
        tc_id    = tc["id"]
        category = tc["category"]
        text     = tc["text"]

        snap_before = sys_snapshot()
        audio, sr, synth_time, audio_dur = synthesize(voice, text)
        snap_after = sys_snapshot()

        if audio is None:
            print(f"{tc_id:>3}  {category:<12}  {RED}[synthesis failed]{RESET}")
            continue

        rtf = synth_time / audio_dur if audio_dur > 0 else 0

        # Dùng snapshot sau synthesis để capture peak usage
        snap = snap_after
        temp_str = f"{snap['temp_c']:.1f}°C" if snap["temp_c"] > 0 else "  N/A"

        rtf_color = GREEN if rtf < 0.5 else (YELLOW if rtf < 1.0 else RED)

        print(f"{tc_id:>3}  {category:<12}  {len(text):>5}c  "
              f"{synth_time:>5.2f}s  {audio_dur:>5.2f}s  "
              f"{rtf_color}{rtf:>4.2f}x{RESET}  "
              f"{snap['ram_used_mb']:>5}MB  {snap['cpu_pct']:>4.0f}%  {temp_str}")
        print(f"     {DIM}{text[:100]}{'...' if len(text)>100 else ''}{RESET}\n")

        # Lưu audio nếu cần
        wav_path = None
        if args.save_audio:
            import wave, struct
            wav_path = f"tts_audio_output/{tc_id:03d}_{category}.wav"
            pcm = (audio * 32767).astype(np.int16)
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(pcm.tobytes())

        if args.play and audio is not None:
            import sounddevice as sd
            sd.play(audio, samplerate=sr)
            sd.wait()

        by_category.setdefault(category, []).append({
            "synth": synth_time, "dur": audio_dur, "rtf": rtf
        })

        results.append({
            "id": tc_id, "category": category,
            "text_length": len(text),
            "synth_time_s": round(synth_time, 4),
            "audio_duration_s": round(audio_dur, 4),
            "rtf": round(rtf, 4),
            "realtime_capable": rtf < 1.0,
            "ram_used_mb": snap["ram_used_mb"], "ram_pct": snap["ram_pct"],
            "cpu_pct": snap["cpu_pct"], "temp_c": snap["temp_c"],
            "saved_wav": wav_path,
        })

    if not results:
        print("Không có câu nào để test.")
        return

    # ─ Summary ─
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
    print(f"  {BOLD}TỔNG KẾT TTS — per category{RESET}")
    print(f"{'─'*90}")
    print(f"  {'Category':<14}  {'Câu':>4}  {'RTF TB':>7}  {'Synth TB':>9}  {'Dur TB':>8}")
    print(f"  {'─'*14}  {'─'*4}  {'─'*7}  {'─'*9}  {'─'*8}")

    for cat, data in sorted(by_category.items()):
        a_rtf   = sum(d["rtf"] for d in data) / len(data)
        a_synth = sum(d["synth"] for d in data) / len(data)
        a_dur   = sum(d["dur"] for d in data) / len(data)
        c = GREEN if a_rtf < 0.5 else (YELLOW if a_rtf < 1.0 else RED)
        print(f"  {cat:<14}  {len(data):>4}  {c}{a_rtf:>6.3f}x{RESET}  {a_synth:>7.3f}s    {a_dur:>6.3f}s")

    rtf_color = GREEN if avg_rtf < 0.5 else (YELLOW if avg_rtf < 1.0 else RED)
    print(f"{'─'*90}")
    print(f"  {BOLD}OVERALL ({total} câu){RESET}")
    print(f"  RTF TB         : {rtf_color}{avg_rtf:.3f}x{RESET}  (min {min_rtf:.3f}  max {max_rtf:.3f})")
    print(f"  Real-time      : {GREEN if rt_cap==total else YELLOW}{rt_cap}/{total}{RESET} câu RTF < 1.0")
    print(f"  Synth time TB  : {avg_synth:.3f}s / câu")
    print(f"  Audio dur TB   : {avg_dur:.3f}s / câu")
    print(f"  RAM TB         : {avg_ram:.0f} MB")
    print(f"  CPU TB         : {avg_cpu:.1f}%")
    if avg_temp > 0:
        print(f"  Nhiệt độ TB    : {avg_temp:.1f}°C   |   Max : {max_temp:.1f}°C")
    if args.save_audio:
        print(f"  Audio saved    : tts_audio_output/ ({total} file .wav)")
    print(f"{'═'*90}\n")

    # ─ JSON export ─
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"benchmark_tts_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": ts,
            "model": "en_US-lessac-medium",
            "total": total,
            "avg_rtf":        round(avg_rtf, 4),
            "min_rtf":        round(min_rtf, 4),
            "max_rtf":        round(max_rtf, 4),
            "realtime_capable": rt_cap,
            "avg_synth_time_s": round(avg_synth, 4),
            "avg_audio_dur_s":  round(avg_dur, 4),
            "avg_ram_mb":     round(avg_ram, 1),
            "avg_cpu_pct":    round(avg_cpu, 1),
            "avg_temp_c":     round(avg_temp, 1) if avg_temp > 0 else None,
            "max_temp_c":     round(max_temp, 1) if max_temp > 0 else None,
            "sentences": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"  Kết quả đã lưu : {out_path}\n")


if __name__ == "__main__":
    main()
