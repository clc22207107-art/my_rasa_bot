"""
test_benchmark_nlu.py — Benchmark NLU (Intent classification + Slot filling)
Gửi text trực tiếp vào Rasa, KHÔNG cần audio.

Metrics:
  - Intent accuracy   : predicted_intent == expected_intent
  - Slot filling      : entity types annotated trong test set vs entity types trả về từ Rasa
  - Bot response      : hiển thị câu trả lời thực của bot cho mỗi câu test
  - NLU latency (ms)
  - RAM used (MB), RAM %, CPU %, CPU temperature (°C)

Yêu cầu:
    Terminal 1: ../venv/bin/rasa run actions
    Terminal 2: ../venv/bin/rasa run --enable-api --cors "*"

Cách dùng:
    python test_benchmark_nlu.py
    python test_benchmark_nlu.py --intent order_drink
    python test_benchmark_nlu.py --max 30
    python test_benchmark_nlu.py --no-bot       # bỏ qua bước lấy bot response (nhanh hơn)
"""

import os, re, sys, time, argparse, json, datetime, warnings
warnings.filterwarnings("ignore")

import requests
import psutil

# ── Cấu hình ─────────────────────────────────────────────────────────────────
TEST_FILE    = "tests/test_nlu.yml"
RASA_PARSE   = "http://localhost:5005/model/parse"
RASA_WEBHOOK = "http://localhost:5005/webhooks/rest/webhook"
RASA_STATUS  = "http://localhost:5005/status"

# ── ANSI ──────────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"; RED    = "\033[91m"; YELLOW = "\033[93m"
CYAN   = "\033[96m"; BOLD   = "\033[1m";  DIM    = "\033[2m"; RESET = "\033[0m"


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
# Parse test file — giữ nguyên entity annotation
# =============================================================================

def parse_entities_from_raw(raw: str) -> list:
    """[coca](drink) [500ml](size) → [{"entity": "drink"}, {"entity": "size"}]"""
    return [{"entity": m.group(2), "value": m.group(1)}
            for m in re.finditer(r'\[([^\]]+)\]\(([^\)]+)\)', raw)]

def strip_entities(text: str) -> str:
    return re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text).strip()

def parse_test_file(path: str) -> list:
    """Trả về list of {sentence, intent, text, expected_entities}"""
    results = []
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
                raw = s[2:].strip()
                results.append({
                    "sentence":          counter,
                    "intent":            current_intent,
                    "text":              strip_entities(raw),
                    "expected_entities": parse_entities_from_raw(raw),
                })
    return results


# =============================================================================
# Rasa calls
# =============================================================================

def nlu_parse(text: str) -> dict:
    r = requests.post(RASA_PARSE, json={"text": text}, timeout=10)
    r.raise_for_status()
    return r.json()

# context cần gửi trước cho các intent phụ thuộc hội thoại
_CONTEXT_SETUP = {
    "specify_size":     ["I want sprite"],
    "specify_quantity": ["I want coca"],
    "confirm":          ["I want pepsi"],
    "deny":             ["I want monster"],
    "specify_payment":  ["I want fanta", "yes confirm"],
    "show_cart":        ["I want coca", "give me 2 sprite"],
    "remove_from_cart": ["I want coca", "I want pepsi"],
    "ask_order_status": ["I want coca", "yes", "cash"],
    "request_receipt":  ["I want coca", "yes", "cash"],
    "ask_refund":       ["I want coca", "yes", "cash"],
    "complain":         ["I want coca", "yes", "cash"],
}

def setup_context(intent: str, sender_id: str):
    for msg in _CONTEXT_SETUP.get(intent, []):
        try:
            requests.post(RASA_WEBHOOK, json={"sender": sender_id, "message": msg}, timeout=10)
        except Exception:
            pass

def get_bot_response(text: str, sender_id: str) -> str:
    try:
        r = requests.post(RASA_WEBHOOK,
                          json={"sender": sender_id, "message": text}, timeout=10)
        r.raise_for_status()
        msgs = r.json()
        parts = [m["text"] for m in msgs if "text" in m]
        return " | ".join(parts) if parts else "(no text response)"
    except Exception as e:
        return f"(webhook error: {e})"


# =============================================================================
# Entity comparison
# =============================================================================

def entity_accuracy(expected: list, got: list) -> tuple:
    """
    expected: [{"entity": "drink"}, {"entity": "size"}] từ test set annotation
    got     : list of entities từ Rasa /model/parse
    Trả về (correct, total) — đếm entity TYPES đúng
    """
    if not expected:
        return 1, 1   # không có entity nào expected → pass

    got_types = {e["entity"] for e in got}
    correct = sum(1 for e in expected if e["entity"] in got_types)
    return correct, len(expected)


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="NLU Benchmark — Intent + Slot filling")
    parser.add_argument("--intent", type=str, help="Chỉ test 1 intent")
    parser.add_argument("--max",    type=int, help="Giới hạn số câu")
    parser.add_argument("--no-bot", action="store_true", help="Bỏ qua bot response (nhanh hơn)")
    args = parser.parse_args()

    # Kiểm tra Rasa
    try:
        requests.get(RASA_STATUS, timeout=5).raise_for_status()
    except Exception:
        print(f"{RED}Rasa chưa chạy!{RESET}")
        print(f"  Terminal 1: ../venv/bin/rasa run actions")
        print(f"  Terminal 2: ../venv/bin/rasa run --enable-api --cors \"*\"")
        sys.exit(1)

    # Load test set
    test_cases = parse_test_file(TEST_FILE)
    if args.intent:
        test_cases = [t for t in test_cases if t["intent"] == args.intent]
    if args.max:
        test_cases = test_cases[:args.max]

    print(f"\n{'═'*100}")
    print(f"  {BOLD}NLU BENCHMARK — Intent Classification + Slot Filling + Bot Response{RESET}")
    print(f"  Test file : {TEST_FILE}   |   {len(test_cases)} câu")
    if args.no_bot:
        print(f"  {YELLOW}--no-bot: bỏ qua bot response{RESET}")
    print(f"{'═'*100}")

    psutil.cpu_percent(interval=None)
    run_id = int(time.time())

    results = []
    by_intent_intent = {}
    by_intent_entity = {}
    intent_ok_total = 0
    entity_ok_slots = 0
    entity_total_slots = 0

    for i, tc in enumerate(test_cases):
        intent_expected = tc["intent"]
        text            = tc["text"]
        sent_num        = tc["sentence"]
        expected_ents   = tc["expected_entities"]

        # NLU parse
        t_start = time.time()
        try:
            parsed = nlu_parse(text)
        except requests.exceptions.ConnectionError:
            print(f"\n{RED}Mất kết nối Rasa!{RESET}")
            break
        nlu_ms = (time.time() - t_start) * 1000

        predicted_intent = parsed["intent"]["name"]
        confidence       = parsed["intent"]["confidence"]
        got_entities     = parsed.get("entities", [])

        # Bot response
        bot_reply = ""
        if not args.no_bot:
            sender_id = f"nlu_bench_{run_id}_{sent_num}"
            setup_context(intent_expected, sender_id)
            bot_reply = get_bot_response(text, sender_id)

        snap = sys_snapshot()

        # Intent accuracy
        if intent_expected == "nlu_fallback":
            intent_ok = predicted_intent in ("nlu_fallback", "out_of_scope")
        else:
            intent_ok = predicted_intent == intent_expected

        # Slot filling (entity) accuracy
        slot_correct, slot_total = entity_accuracy(expected_ents, got_entities)

        if intent_ok:
            intent_ok_total += 1
        entity_ok_slots    += slot_correct
        entity_total_slots += slot_total

        by_intent_intent.setdefault(intent_expected, []).append(intent_ok)
        by_intent_entity.setdefault(intent_expected, []).append((slot_correct, slot_total))

        # Print
        ic = GREEN if intent_ok else RED
        temp_str = f"{snap['temp_c']:.1f}°C" if snap["temp_c"] > 0 else "N/A"
        print(f"\n{'─'*100}")
        print(f"[{sent_num:>3}] {CYAN}{intent_expected:<24}{RESET}  "
              f"Intent:{ic}{'✓' if intent_ok else '✗'}{RESET}  "
              f"Slots:{GREEN if slot_correct==slot_total else RED}"
              f"{slot_correct}/{slot_total}{RESET}  "
              f"Conf:{confidence:.2f}  {nlu_ms:.0f}ms  "
              f"RAM={snap['ram_used_mb']}MB  CPU={snap['cpu_pct']:.0f}%  T={temp_str}")
        print(f"  Text   : {DIM}{text}{RESET}")
        print(f"  Intent : {ic}{predicted_intent}{RESET}  "
              f"(expected: {intent_expected})")

        # Entities
        if expected_ents:
            exp_str = ", ".join(f"{e['entity']}={e['value']}" for e in expected_ents)
            got_str = ", ".join(f"{e['entity']}={e.get('value','?')}" for e in got_entities
                                if e['entity'] in {x['entity'] for x in expected_ents})
            print(f"  Slots  : expected [{exp_str}]  →  got [{got_str or 'none'}]")

        if not args.no_bot and bot_reply:
            preview = bot_reply[:120] + "..." if len(bot_reply) > 120 else bot_reply
            print(f"  Bot    : {DIM}{preview}{RESET}")

        results.append({
            "sentence":           sent_num,
            "intent_expected":    intent_expected,
            "intent_predicted":   predicted_intent,
            "confidence":         round(confidence, 4),
            "intent_ok":          intent_ok,
            "expected_entities":  expected_ents,
            "got_entities":       [{"entity": e["entity"], "value": e.get("value", "")}
                                   for e in got_entities],
            "slot_correct":       slot_correct,
            "slot_total":         slot_total,
            "nlu_ms":             round(nlu_ms, 1),
            "bot_reply":          bot_reply,
            "ram_used_mb":        snap["ram_used_mb"],
            "ram_pct":            snap["ram_pct"],
            "cpu_pct":            snap["cpu_pct"],
            "temp_c":             snap["temp_c"],
        })

    if not results:
        print("Không có câu nào để test.")
        return

    # ─ Summary ─
    total      = len(results)
    avg_ms     = sum(r["nlu_ms"] for r in results) / total
    avg_ram    = sum(r["ram_used_mb"] for r in results) / total
    avg_cpu    = sum(r["cpu_pct"] for r in results) / total
    temps      = [r["temp_c"] for r in results if r["temp_c"] > 0]
    max_temp   = max(temps, default=-1)
    avg_temp   = sum(temps) / len(temps) if temps else -1

    intent_acc = intent_ok_total / total
    entity_acc = entity_ok_slots / entity_total_slots if entity_total_slots > 0 else 1.0

    def col(v): return GREEN if v >= 0.9 else (YELLOW if v >= 0.8 else RED)

    print(f"\n\n{'═'*100}")
    print(f"  {BOLD}TỔNG KẾT NLU — per intent{RESET}")
    print(f"{'─'*100}")
    print(f"  {'Intent':<30} {'Cls Acc':>8}  {'Slot Acc':>9}")
    print(f"  {'─'*30} {'─'*8}  {'─'*9}")

    for intent in sorted(by_intent_intent):
        i_oks  = by_intent_intent[intent]
        e_data = by_intent_entity.get(intent, [])
        i_pct  = sum(i_oks) / len(i_oks)
        e_tot  = sum(t for _, t in e_data)
        e_ok   = sum(c for c, _ in e_data)
        e_pct  = e_ok / e_tot if e_tot > 0 else 1.0
        ic     = GREEN if i_pct >= 0.9 else (YELLOW if i_pct >= 0.8 else RED)
        ec     = GREEN if e_pct >= 0.9 else (YELLOW if e_pct >= 0.8 else RED)
        print(f"  {intent:<30} {ic}{i_pct*100:>6.1f}%{RESET}   {ec}{e_pct*100:>7.1f}%{RESET}")

    print(f"{'─'*100}")
    print(f"  {'OVERALL':<30} "
          f"{col(intent_acc)}{BOLD}{intent_ok_total}/{total} ({intent_acc*100:.1f}%){RESET}   "
          f"{col(entity_acc)}{BOLD}{entity_ok_slots}/{entity_total_slots} ({entity_acc*100:.1f}%){RESET}")
    print(f"{'─'*100}")
    print(f"  NLU latency TB : {avg_ms:.1f} ms / câu")
    print(f"  RAM TB         : {avg_ram:.0f} MB")
    print(f"  CPU TB         : {avg_cpu:.1f}%")
    if avg_temp > 0:
        print(f"  Nhiệt độ TB    : {avg_temp:.1f}°C   |   Max : {max_temp:.1f}°C")
    print(f"{'═'*100}\n")

    # ─ JSON export ─
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"benchmark_nlu_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": ts,
            "total": total,
            "intent_accuracy":   round(intent_acc, 4),
            "slot_fill_accuracy": round(entity_acc, 4),
            "avg_nlu_ms":        round(avg_ms, 1),
            "avg_ram_mb":        round(avg_ram, 1),
            "avg_cpu_pct":       round(avg_cpu, 1),
            "avg_temp_c":        round(avg_temp, 1) if avg_temp > 0 else None,
            "max_temp_c":        round(max_temp, 1) if max_temp > 0 else None,
            "sentences": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"  Kết quả đã lưu : {out_path}\n")


if __name__ == "__main__":
    main()
