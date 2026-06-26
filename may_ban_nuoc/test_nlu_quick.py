"""
test_nlu_quick.py — Chạy toàn bộ test_nlu.yml qua Rasa NLU và in kết quả
Yêu cầu: rasa run --enable-api --cors "*" đang chạy ở Terminal 2

Cách dùng:
    python test_nlu_quick.py
    python test_nlu_quick.py --intent ask_price        # chỉ test 1 intent
    python test_nlu_quick.py --show-wrong              # chỉ in câu sai
    python test_nlu_quick.py --show-all                # in cả đúng lẫn sai
"""

import sys
import re
import argparse
import requests
from collections import defaultdict

RASA_URL    = "http://localhost:5005/model/parse"
TEST_FILE   = "tests/test_nlu.yml"
CONFIDENCE_WARN = 0.70   # highlight nếu confidence thấp dù đúng intent

# ── ANSI colors ──────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


def parse_test_file(path: str) -> dict[str, list[str]]:
    """Parse test_nlu.yml → {intent: [example, ...]}"""
    data: dict[str, list[str]] = {}
    current_intent = None
    in_examples = False

    with open(path, encoding="utf-8") as f:
        for line in f:
            # Bỏ dòng comment và dòng trắng
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # Detect intent header
            m = re.match(r"^- intent:\s*(\S+)", stripped)
            if m:
                current_intent = m.group(1)
                data[current_intent] = []
                in_examples = False
                continue

            if stripped == "examples: |":
                in_examples = True
                continue

            # Collect examples (dòng bắt đầu bằng "- ")
            if in_examples and current_intent and stripped.startswith("- "):
                text = stripped[2:].strip()
                # Bỏ entity annotations: [coca](drink) → coca
                text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
                data[current_intent].append(text)

    return data


def predict(text: str) -> tuple[str, float]:
    """Gửi text tới Rasa NLU, trả về (intent, confidence)."""
    try:
        r = requests.post(RASA_URL, json={"text": text}, timeout=5)
        r.raise_for_status()
        intent = r.json()["intent"]
        return intent["name"], round(intent["confidence"], 3)
    except requests.exceptions.ConnectionError:
        print(f"{RED}Không kết nối được Rasa! Chạy: rasa run --enable-api --cors \"*\"{RESET}")
        sys.exit(1)
    except Exception as e:
        return "ERROR", 0.0


def run_tests(filter_intent=None, show_mode="wrong"):
    test_data = parse_test_file(TEST_FILE)

    if filter_intent:
        if filter_intent not in test_data:
            print(f"{RED}Intent '{filter_intent}' không tồn tại trong test file.{RESET}")
            print(f"Các intent có sẵn: {', '.join(sorted(test_data.keys()))}")
            sys.exit(1)
        test_data = {filter_intent: test_data[filter_intent]}

    # ── Chạy từng câu ────────────────────────────────────────
    results: dict[str, dict] = {}   # intent → {correct, wrong: [(text, pred, conf)]}
    total_correct = 0
    total_wrong   = 0

    intents_sorted = sorted(test_data.keys())
    total_examples = sum(len(v) for v in test_data.values())

    print(f"\n{BOLD}{'═'*60}{RESET}")
    print(f"{BOLD}  NLU QUICK TEST — {TEST_FILE}{RESET}")
    print(f"  {len(intents_sorted)} intents | {total_examples} câu test")
    print(f"{'═'*60}{RESET}\n")

    for intent in intents_sorted:
        examples = test_data[intent]
        correct_list = []
        wrong_list   = []

        for text in examples:
            pred_intent, conf = predict(text)
            if pred_intent == intent:
                correct_list.append((text, pred_intent, conf))
                total_correct += 1
            else:
                wrong_list.append((text, pred_intent, conf))
                total_wrong += 1

        results[intent] = {"correct": correct_list, "wrong": wrong_list}
        n_total   = len(examples)
        n_correct = len(correct_list)
        pct       = n_correct / n_total * 100 if n_total else 0

        # Dòng tóm tắt per-intent
        color  = GREEN if pct == 100 else (YELLOW if pct >= 80 else RED)
        status = "✓" if pct == 100 else ("~" if pct >= 80 else "✗")
        print(f"  {color}{status}{RESET} {BOLD}{intent:<28}{RESET} "
              f"{color}{n_correct}/{n_total} ({pct:5.1f}%){RESET}")

        # In chi tiết câu sai / câu đúng tùy show_mode
        if show_mode == "all":
            for text, pred, conf in correct_list:
                low = f" {YELLOW}[low conf]{RESET}" if conf < CONFIDENCE_WARN else ""
                print(f"      {DIM}✓ \"{text}\" → {conf:.2f}{RESET}{low}")
        if show_mode in ("wrong", "all") and wrong_list:
            for text, pred, conf in wrong_list:
                print(f"      {RED}✗ \"{text}\"{RESET}")
                print(f"        {DIM}→ predicted: {RED}{pred}{RESET} {DIM}({conf:.2f}){RESET}")

    # ── Tổng kết ─────────────────────────────────────────────
    total = total_correct + total_wrong
    acc   = total_correct / total * 100 if total else 0
    color = GREEN if acc >= 90 else (YELLOW if acc >= 80 else RED)

    print(f"\n{'═'*60}")
    print(f"  {BOLD}KẾT QUẢ TỔNG{RESET}")
    print(f"{'─'*60}")
    print(f"  Đúng  : {GREEN}{total_correct}{RESET} / {total}")
    print(f"  Sai   : {RED}{total_wrong}{RESET} / {total}")
    print(f"  Accuracy: {color}{BOLD}{acc:.1f}%{RESET}")

    if total_wrong > 0:
        print(f"\n  {BOLD}Câu sai theo intent:{RESET}")
        for intent in intents_sorted:
            wrongs = results[intent]["wrong"]
            if wrongs:
                print(f"  {RED}{intent}{RESET} ({len(wrongs)} sai):")
                for text, pred, conf in wrongs:
                    print(f"    \"{text}\"")
                    print(f"    {DIM}→ {RED}{pred}{RESET} {DIM}({conf:.2f}){RESET}")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--intent",     type=str, help="Chỉ test 1 intent cụ thể")
    parser.add_argument("--show-wrong", action="store_true", help="Chỉ in câu sai (mặc định)")
    parser.add_argument("--show-all",   action="store_true", help="In cả đúng lẫn sai")
    args = parser.parse_args()

    mode = "all" if args.show_all else "wrong"
    run_tests(filter_intent=args.intent, show_mode=mode)
