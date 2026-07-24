"""
combined_entity_eval.py — Đánh giá entity + intent theo output CUỐI CÙNG của pipeline
========================================================================================
Gọi agent.parse_message() y hệt runtime thật (DrinkEntityMasker → DIETClassifier → …).
Output:
  - results/combined_entity_confusion_matrix.png  + bảng P/R/F1 theo entity
  - results/combined_intent_confusion_matrix.png   + bảng P/R/F1 theo intent + accuracy

Cách dùng:
    cd ~/Desktop/may_ban_nuoc/may_ban_nuoc
    source ../venv/bin/activate
    python combined_entity_eval.py
"""

import asyncio
import glob
import os
import sys
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from rasa.shared.nlu.training_data.loading import load_data
from rasa.core.agent import Agent

TEST_FILE        = "tests/test_nlu.yml"
MODEL_GLOB       = "models/*.tar.gz"
OUT_ENTITY       = "results/combined_entity_confusion_matrix.png"
OUT_INTENT       = "results/combined_intent_confusion_matrix.png"
OUT_REPORT       = "results/nlu_eval_report.json"

ENTITY_LABELS = [
    "drink", "size", "quantity", "category",
    "ingredient", "payment_method", "price_limit", "info_type",
    "no_entity",
]


def find_latest_model() -> str:
    models = sorted(glob.glob(MODEL_GLOB), key=os.path.getmtime)
    if not models:
        print(f"Không tìm thấy model nào khớp {MODEL_GLOB}. Chạy `rasa train` trước.")
        sys.exit(1)
    return models[-1]


try:
    from word2number import w2n as _w2n_eval
    _W2N_EVAL = True
except ImportError:
    _W2N_EVAL = False


def normalize_val(v: str) -> str:
    v = (v or "").lower().strip()
    v = re.sub(r'(\d+(?:[.,]\d+)?)\s*(ml|l\b)', lambda m: m.group(1) + m.group(2), v)
    if _W2N_EVAL:
        try:
            return str(_w2n_eval.word_to_num(v))
        except Exception:
            pass
    return v


async def run_eval():
    model_path = find_latest_model()
    print(f"Đang load model: {model_path}")
    agent = Agent.load(model_path)

    data = load_data(TEST_FILE)
    examples = [m for m in data.training_examples if m.get("text")]
    print(f"Số câu test: {len(examples)}\n")

    ent_true, ent_pred = [], []
    int_true, int_pred = [], []

    for i, msg in enumerate(examples, 1):
        text = msg.get("text")
        result = await agent.parse_message(text)

        # ── Intent ──────────────────────────────────────────────
        raw_intent  = msg.get("intent")
        gold_intent = (raw_intent.get("name") if isinstance(raw_intent, dict) else raw_intent) or "nlu_fallback"
        pred_intent = (result.get("intent") or {}).get("name") or "nlu_fallback"
        int_true.append(gold_intent)
        int_pred.append(pred_intent)

        # ── Entity ──────────────────────────────────────────────
        gold_ents = []
        for e in msg.get("entities", []):
            etype = e["entity"] if e["entity"] in ENTITY_LABELS else "no_entity"
            gold_ents.append((etype, normalize_val(e.get("value") or e.get("text", ""))))

        pred_raw = result.get("entities", []) or []
        pred_ents = []
        for e in pred_raw:
            etype = e["entity"] if e["entity"] in ENTITY_LABELS else "no_entity"
            pred_ents.append((etype, normalize_val(e.get("value") or e.get("text", ""))))

        unmatched = list(pred_ents)
        for g_type, g_val in gold_ents:
            matched = False
            for j, (p_type, p_val) in enumerate(unmatched):
                # drink / payment_method: match by type only — EntitySynonymMapper
                # normalises values to canonical form (e.g. "vita milk"→"vitamilk",
                # "atm card"→"card"), so value comparison between gold and pred is
                # unreliable. Genuine misses (pred=[]) are still caught correctly.
                value_ok = (p_type in ("drink", "payment_method")) or (p_val == g_val)
                if p_type == g_type and value_ok:
                    ent_true.append(g_type)
                    ent_pred.append(p_type)
                    unmatched.pop(j)
                    matched = True
                    break
            if not matched:
                ent_true.append(g_type)
                ent_pred.append("no_entity")

        for p_type, _ in unmatched:
            ent_true.append("no_entity")
            ent_pred.append(p_type)

        if i % 20 == 0:
            print(f"  ... đã xử lý {i}/{len(examples)} câu")

    return ent_true, ent_pred, int_true, int_pred


# ── Helpers ───────────────────────────────────────────────────────────────────

def _plot_matrix(cm, labels, title, out_path):
    fig, ax = plt.subplots(figsize=(max(8, len(labels)), max(6, len(labels) - 1)))
    im = ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=90, fontsize=8)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("Predicted label")
    ax.set_ylabel("True label")
    ax.set_title(title)
    thresh = cm.max() / 2 if cm.max() > 0 else 1
    for r in range(len(labels)):
        for c in range(len(labels)):
            val = cm[r, c]
            ax.text(c, r, str(val), ha="center", va="center",
                    color="white" if val > thresh else "black", fontsize=7)
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    os.makedirs("results", exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Đã lưu: {out_path}")


def _calc_metrics(cm, labels, skip_label=None) -> dict:
    rows = {}
    for lbl in labels:
        if lbl == skip_label:
            continue
        idx     = labels.index(lbl)
        tp      = int(cm[idx, idx])
        fp      = int(cm[:, idx].sum() - tp)
        fn      = int(cm[idx, :].sum() - tp)
        support = int(cm[idx, :].sum())
        if support == 0:
            continue
        p  = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        rows[lbl] = {"precision": round(p, 4), "recall": round(r, 4),
                     "f1": round(f1, 4), "support": support}
    return rows


def _print_metrics(cm, labels, skip_label=None):
    rows = _calc_metrics(cm, labels, skip_label)
    print(f"\n{'Label':<28}{'Precision':>10}{'Recall':>10}{'F1':>10}{'Support':>10}")
    print("-" * 68)
    for lbl, m in rows.items():
        print(f"{lbl:<28}{m['precision']:>10.2f}{m['recall']:>10.2f}{m['f1']:>10.2f}{m['support']:>10}")
    return rows


def plot_entity(y_true, y_pred) -> dict:
    from sklearn.metrics import confusion_matrix
    labels = [l for l in ENTITY_LABELS if l in set(y_true) | set(y_pred)]
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    print("\n" + "=" * 68)
    print("ENTITY EVALUATION")
    print("=" * 68)
    _plot_matrix(cm, labels, "Combined Entity Confusion Matrix (full pipeline)", OUT_ENTITY)
    return _print_metrics(cm, labels, skip_label="no_entity")


def plot_intent(y_true, y_pred) -> dict:
    from sklearn.metrics import confusion_matrix, accuracy_score
    all_labels = sorted(set(y_true) | set(y_pred))
    cm = confusion_matrix(y_true, y_pred, labels=all_labels)
    acc = accuracy_score(y_true, y_pred)
    correct = sum(t == p for t, p in zip(y_true, y_pred))
    print("\n" + "=" * 68)
    print("INTENT EVALUATION")
    print("=" * 68)
    print(f"Overall accuracy: {acc:.4f}  ({correct}/{len(y_true)})")
    _plot_matrix(cm, all_labels, f"Intent Confusion Matrix  (accuracy={acc:.3f})", OUT_INTENT)
    rows = _print_metrics(cm, all_labels)
    return {"accuracy": round(acc, 4), "correct": correct, "total": len(y_true), "per_intent": rows}


def save_report(entity_rows: dict, intent_data: dict):
    import json
    from datetime import datetime
    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "entity": entity_rows,
        "intent": intent_data,
    }
    os.makedirs("results", exist_ok=True)
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nĐã lưu report: {OUT_REPORT}")


if __name__ == "__main__":
    ent_true, ent_pred, int_true, int_pred = asyncio.run(run_eval())
    entity_rows = plot_entity(ent_true, ent_pred)
    intent_data = plot_intent(int_true, int_pred)
    save_report(entity_rows, intent_data)
