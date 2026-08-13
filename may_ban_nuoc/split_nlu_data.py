"""
split_nlu_data.py — Chia nlu_full.yml thành train + test theo tỉ lệ
=====================================================================
Quy trình:
  1. Gom thủ công: data/nlu.yml + tests/test_nlu.yml → data/nlu_full.yml
  2. Chạy script này để split:
       python split_nlu_data.py            # mặc định 75-25
       python split_nlu_data.py --ratio 0.7

Output ghi thẳng vào:
  data/nlu.yml          (train + synonym/lookup từ bản cũ)
  tests/test_nlu.yml    (test  + nlu_fallback từ bản cũ)
"""

import re, argparse, os
from collections import defaultdict
from math import ceil

FULL_IN   = "data/nlu_full.yml"
TRAIN_OUT = "data/nlu.yml"
TEST_OUT  = "tests/test_nlu.yml"
FALLBACK_INTENT = "nlu_fallback"


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_nlu_file(path: str) -> dict[str, list[str]]:
    """Trả về {intent: [example, ...]} — bỏ qua synonym/lookup/nlu_fallback."""
    examples = defaultdict(list)
    current  = None
    block_type = None  # 'intent' | 'other'
    in_ex    = False
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            m = re.match(r'^- intent:\s*(\S+)', s)
            if m:
                name = m.group(1)
                current    = None if name == FALLBACK_INTENT else name
                block_type = 'intent'
                in_ex      = False
                continue
            if re.match(r'^- (synonym|lookup):', s):
                block_type = 'other'
                current    = None
                in_ex      = False
                continue
            if s == "examples: |":
                in_ex = True
                continue
            if in_ex and block_type == 'intent' and current and s.startswith("- "):
                examples[current].append(s[2:].strip())
    return dict(examples)


def read_fallback(path: str) -> list[str]:
    """Đọc nlu_fallback examples từ file test."""
    examples = []
    current  = None
    in_ex    = False
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            m = re.match(r'^- intent:\s*(\S+)', s)
            if m:
                current = m.group(1)
                in_ex   = False
                continue
            if s == "examples: |":
                in_ex = True
                continue
            if in_ex and current == FALLBACK_INTENT and s.startswith("- "):
                examples.append(s[2:].strip())
    return examples


def read_synonym_lookup(path: str) -> list[str]:
    """Đọc synonym/lookup blocks (top-level, 0-indent); dừng khi gặp - intent:."""
    lines    = []
    in_block = False
    with open(path, encoding="utf-8") as f:
        for raw in f:
            if re.match(r'^- (synonym|lookup):', raw):
                in_block = True
            elif re.match(r'^- intent:', raw):
                in_block = False  # intent mới → dừng capture
            if in_block:
                lines.append(raw)
    return lines


# ── Writer ────────────────────────────────────────────────────────────────────

def write_train(path: str, data: dict[str, list[str]], syn_lk: list[str]):
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write('version: "3.1"\n\nnlu:\n')
        for intent, exs in sorted(data.items()):
            if not exs:
                continue
            f.write(f"\n- intent: {intent}\n  examples: |\n")
            for ex in exs:
                f.write(f"    - {ex}\n")
        if syn_lk:
            f.write("\n")
            f.writelines(syn_lk)
    n = sum(len(v) for v in data.values())
    print(f"  → TRAIN: {path}  ({n} câu, {len(data)} intent)")


def write_test(path: str, data: dict[str, list[str]], fallback: list[str]):
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write('version: "3.1"\n\nnlu:\n')
        for intent, exs in sorted(data.items()):
            if not exs:
                continue
            f.write(f"\n- intent: {intent}\n  examples: |\n")
            for ex in exs:
                f.write(f"    - {ex}\n")
        if fallback:
            f.write(f"\n- intent: {FALLBACK_INTENT}\n  examples: |\n")
            for ex in fallback:
                f.write(f"    - {ex}\n")
    n = sum(len(v) for v in data.values()) + len(fallback)
    print(f"  → TEST : {path}  ({n} câu, {len(data)+1 if fallback else len(data)} intent)")


# ── Split ─────────────────────────────────────────────────────────────────────

def sequential_split(pool: dict[str, list[str]], train_ratio: float):
    train, test = {}, {}
    for intent, exs in sorted(pool.items()):
        n       = len(exs)
        n_train = ceil(train_ratio * n)
        n_train = max(2, min(n_train, n - 1))
        train[intent] = exs[:n_train]
        test[intent]  = exs[n_train:]
    return train, test


# ── Report ────────────────────────────────────────────────────────────────────

def report(pool, train, test, fallback):
    GREEN  = "\033[92m"; YELLOW = "\033[93m"; RED = "\033[91m"
    BOLD   = "\033[1m";  RESET  = "\033[0m"

    print(f"\n{'═'*72}")
    print(f"  {BOLD}SPLIT REPORT — per intent{RESET}")
    print(f"{'─'*72}")
    print(f"  {'Intent':<32} {'Total':>6} {'Train':>6} {'Test':>5} {'%Train':>8}")
    print(f"  {'─'*32} {'─'*6} {'─'*6} {'─'*5} {'─'*8}")

    tot_total = tot_train = tot_test = 0
    for intent in sorted(pool):
        n    = len(pool[intent])
        n_tr = len(train.get(intent, []))
        n_te = len(test.get(intent, []))
        pct  = n_tr / n * 100
        c    = GREEN if 73 <= pct <= 78 else (YELLOW if 68 <= pct <= 83 else RED)
        print(f"  {intent:<32} {n:>6} {c}{n_tr:>6}{RESET} {n_te:>5} {c}{pct:>7.0f}%{RESET}")
        tot_total += n; tot_train += n_tr; tot_test += n_te

    n_fb = len(fallback)
    if n_fb:
        print(f"  {FALLBACK_INTENT:<32} {n_fb:>6} {'—':>6} {n_fb:>5} {'test-only':>8}")
        tot_test += n_fb

    print(f"  {'─'*32} {'─'*6} {'─'*6} {'─'*5} {'─'*8}")
    pct_ov = tot_train / tot_total * 100
    c = GREEN if 73 <= pct_ov <= 78 else YELLOW
    print(f"  {BOLD}{'TOTAL':<32}{RESET} {tot_total:>6} {c}{BOLD}{tot_train:>6}{RESET}"
          f" {tot_test:>5} {c}{BOLD}{pct_ov:>7.0f}%{RESET}")
    print(f"{'═'*72}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ratio", type=float, default=0.75,
                    help="Tỉ lệ train (mặc định 0.75 = 75/25)")
    args = ap.parse_args()

    # Đọc synonym/lookup và nlu_fallback TRƯỚC khi ghi đè file
    print(f"Đọc synonym/lookup từ {TRAIN_OUT} ...")
    syn_lk   = read_synonym_lookup(TRAIN_OUT) if os.path.exists(TRAIN_OUT) else []
    print(f"  {len(syn_lk)} dòng synonym/lookup")

    print(f"Đọc nlu_fallback từ {TEST_OUT} ...")
    fallback = read_fallback(TEST_OUT) if os.path.exists(TEST_OUT) else []
    print(f"  {len(fallback)} câu fallback")

    print(f"\nĐọc {FULL_IN} ...")
    pool = parse_nlu_file(FULL_IN)
    total = sum(len(v) for v in pool.values())
    print(f"  {total} câu, {len(pool)} intent")

    print(f"\nSplit {args.ratio*100:.0f}/{(1-args.ratio)*100:.0f} (sequential, không shuffle) ...")
    train_out, test_out = sequential_split(pool, args.ratio)

    report(pool, train_out, test_out, fallback)

    print("Ghi file ...")
    write_train(TRAIN_OUT, train_out, syn_lk)
    write_test(TEST_OUT,  test_out,  fallback)
    print("\nDone. Bước tiếp: rasa train")


if __name__ == "__main__":
    main()
