"""
benchmark_plot_system_metrics.py — Vẽ biểu đồ RAM / CPU / Temp
theo từng câu test cho cả 3 pipeline: STT / NLU / TTS.

Tự động tìm file JSON mới nhất của mỗi loại. Output: results/benchmark_system_metrics.png

Cách dùng:
    python benchmark_plot_system_metrics.py
    python benchmark_plot_system_metrics.py --stt benchmark_stt_xxx.json
    python benchmark_plot_system_metrics.py --nlu ... --tts ... --out custom.png
    python benchmark_plot_system_metrics.py --dpi 200
"""

import os, glob, json, argparse
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.gridspec import GridSpec
from matplotlib.lines import Line2D

# ── Dark theme ─────────────────────────────────────────────────────────────────
BG   = "#0D1117"   # page background
CARD = "#161B22"   # subplot background
GRID = "#1C2333"   # grid / spine color
TEXT = "#E6EDF3"   # primary labels
MUTE = "#7D8590"   # secondary / axis labels

# ── Categorical palette: magenta | sky-blue | amber ───────────────────────────
# Hue separation: ~155° / ~157° / ~58° — distinct under D/P CVD (lightness+hue both differ)
C = {
    "STT": "#FF4FA3",   # hot pink / magenta
    "NLU": "#4CC9F0",   # sky blue
    "TTS": "#F9C74F",   # golden amber
}
A = {"STT": 0.22, "NLU": 0.20, "TTS": 0.22}   # fill alpha

ORDER   = ["STT", "NLU", "TTS"]
LABELS  = {
    "STT": "STT  (faster-whisper)",
    "NLU": "NLU  (Rasa DIET + Masker)",
    "TTS": "TTS  (Piper)",
}

METRICS = [
    ("ram_used_mb", "RAM (MB)",    lambda v: f"{v:.0f} MB"),
    ("cpu_pct",     "CPU (%)",     lambda v: f"{v:.1f}%"),
    ("temp_c",      "Temp (°C)",   lambda v: f"{v:.0f}°C"),
    ("latency_ms",  "Độ trễ (ms)", lambda v: f"{v:.0f} ms"),
]

# Mỗi giải pháp đặt tên field độ trễ per-câu khác nhau và đơn vị khác nhau
# (STT: giây/file audio, TTS: giây tổng hợp giọng nói, NLU: mili-giây phân
# loại) → quy đổi hết về ms để so sánh cùng thang đo trên một biểu đồ.
LATENCY_FIELD = {
    "STT": ("time_s",      1000.0),
    "NLU": ("nlu_ms",      1.0),
    "TTS": ("synth_time_s", 1000.0),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def find_latest(prefix: str) -> str | None:
    files = sorted(glob.glob(f"benchmark_{prefix}_*.json"), key=os.path.getmtime)
    return files[-1] if files else None


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def extract(data: dict, key: str, bname: str | None = None) -> np.ndarray:
    if key == "latency_ms":
        field, mult = LATENCY_FIELD[bname]
        raw = [(s.get(field, 0) or 0) * mult for s in data.get("sentences", [])]
        return np.array(raw, dtype=float)
    raw = [s.get(key, 0) or 0 for s in data.get("sentences", [])]
    arr = np.array(raw, dtype=float)
    if key == "temp_c":
        arr[arr <= 0] = np.nan   # no-sensor sentinel
    return arr


def moving_avg(arr: np.ndarray, w: int = 14) -> np.ndarray:
    kernel = np.ones(w) / w
    padded = np.pad(np.where(np.isnan(arr), 0, arr), (w // 2, w // 2), mode="edge")
    out    = np.convolve(padded, kernel, mode="valid")[: len(arr)]
    out[np.isnan(arr)] = np.nan
    return out


def style_ax(ax, title: str, ylabel: str):
    ax.set_facecolor(CARD)
    ax.set_title(title, color=TEXT, fontsize=9, fontweight="bold", pad=4)
    ax.set_ylabel(ylabel, color=MUTE, fontsize=7.5)
    ax.set_xlabel("Câu #", color=MUTE, fontsize=7.5)
    ax.tick_params(axis="both", colors=MUTE, labelsize=7)
    for sp in ax.spines.values():
        sp.set_color(GRID)
    ax.grid(True, color=GRID, lw=0.55, alpha=0.7)
    ax.set_axisbelow(True)


def draw_area_line(ax, x: np.ndarray, y: np.ndarray, color: str, alpha: float,
                   fmt_fn, label: str):
    """Draw: faint scatter points + transparent fill + smoothed trend + avg dashed line."""
    # Raw area fill
    ax.fill_between(x, y, alpha=alpha * 0.45, color=color, zorder=1)
    # Raw line (very faint)
    ax.plot(x, y, color=color, alpha=0.22, lw=0.8, zorder=2)
    # Smoothed trend
    ys = moving_avg(y, w=min(14, max(3, len(x) // 20)))
    ax.plot(x, ys, color=color, lw=2.4, zorder=3, solid_capstyle="round")

    valid = y[~np.isnan(y)]
    if len(valid) == 0:
        return

    avg_v = float(np.nanmean(y))
    max_v = float(np.nanmax(y))
    min_v = float(np.nanmin(y))

    # Avg dashed horizontal
    ax.axhline(avg_v, color=color, lw=1.0, ls=(0, (5, 4)), alpha=0.5, zorder=4)

    # Avg label (y-axis transform: x in axis fraction, y in data coords)
    ax.text(0.015, avg_v, f"avg {fmt_fn(avg_v)}",
            transform=ax.get_yaxis_transform(),
            color=color, fontsize=6.8, va="bottom", fontweight="bold", zorder=5)

    # Max annotation
    max_idx = int(np.nanargmax(y))
    ax.annotate(
        f"↑{fmt_fn(max_v)}",
        xy=(max_idx, max_v),
        xytext=(max(0, max_idx - len(x) // 8), max_v + (max_v - min_v) * 0.04),
        color=color, fontsize=6.5, fontweight="bold",
        arrowprops=dict(arrowstyle="-", color=color, lw=0.7, alpha=0.7),
        zorder=5,
    )


def draw_violin_row(ax, key: str, ylabel: str, fmt_fn, data_by_bench: dict):
    """Row 3: violin + box plot comparing all 3 benchmarks for one metric."""
    ax.set_facecolor(CARD)
    all_vals, positions, colors_v = [], [], []

    for gi, bname in enumerate(ORDER):
        y = data_by_bench[bname][key]
        vals = y[~np.isnan(y)]
        if len(vals) == 0:
            continue
        all_vals.append(vals.tolist())
        positions.append(gi)
        colors_v.append(C[bname])

    if all_vals:
        vp = ax.violinplot(all_vals, positions=positions, widths=0.65,
                           showmeans=False, showmedians=False, showextrema=False)
        for pc, color in zip(vp["bodies"], colors_v):
            pc.set_facecolor(color)
            pc.set_alpha(0.45)
            pc.set_edgecolor(color)
            pc.set_linewidth(1.0)

        # Overlay box plot for quartiles
        bp = ax.boxplot(
            all_vals, positions=positions, widths=0.18, patch_artist=True,
            showfliers=False, manage_ticks=False,
            boxprops    =dict(linewidth=0),
            whiskerprops=dict(color=MUTE, lw=1.0),
            capprops    =dict(color=MUTE, lw=1.0),
            medianprops =dict(color=TEXT, lw=1.8),
        )
        for patch, color in zip(bp["boxes"], colors_v):
            patch.set_facecolor(color)
            patch.set_alpha(0.8)

        # Mean dot
        for gi, (vals, color) in enumerate(zip(all_vals, colors_v)):
            mean_v = float(np.mean(vals))
            ax.plot(positions[gi], mean_v, "o", color="white",
                    markersize=4.5, zorder=6, markeredgecolor=color, markeredgewidth=1.2)
            ax.text(positions[gi], mean_v, f"\n  {fmt_fn(mean_v)}",
                    ha="left", va="top", color=color, fontsize=6.5, fontweight="bold")

    ax.set_xticks(range(len(ORDER)))
    ax.set_xticklabels(ORDER, fontsize=9, fontweight="bold")
    for lbl, bname in zip(ax.get_xticklabels(), ORDER):
        lbl.set_color(C[bname])
    ax.tick_params(axis="y", colors=MUTE, labelsize=7)
    ax.tick_params(axis="x", length=0)
    ax.set_ylabel(ylabel, color=MUTE, fontsize=7.5)
    ax.set_title(f"Phân phối {ylabel}", color=TEXT, fontsize=9, fontweight="bold", pad=4)
    for sp in ax.spines.values():
        sp.set_color(GRID)
    ax.grid(True, axis="y", color=GRID, lw=0.5, alpha=0.6)
    ax.set_axisbelow(True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Vẽ RAM / CPU / Temp từ benchmark JSON")
    ap.add_argument("--stt", help="STT benchmark JSON (mặc định: file mới nhất)")
    ap.add_argument("--nlu", help="NLU benchmark JSON (mặc định: file mới nhất)")
    ap.add_argument("--tts", help="TTS benchmark JSON (mặc định: file mới nhất)")
    ap.add_argument("--out", default="results/benchmark_system_metrics.png",
                    help="Đường dẫn output PNG")
    ap.add_argument("--dpi", type=int, default=160, help="DPI output (mặc định 160)")
    args = ap.parse_args()

    paths = {
        "STT": args.stt or find_latest("stt"),
        "NLU": args.nlu or find_latest("nlu"),
        "TTS": args.tts or find_latest("tts"),
    }

    missing = [k for k, v in paths.items() if not v]
    if missing:
        print(f"[!] Không tìm thấy file JSON cho: {', '.join(missing)}")
        print("    Dùng --stt / --nlu / --tts để chỉ định thủ công.")
        return

    print("─" * 62)
    print("  File JSON đang dùng:")
    for bname, p in paths.items():
        print(f"    {bname}: {p}")
    print("─" * 62)

    raw_data = {bname: load_json(p) for bname, p in paths.items()}
    bench = {
        bname: {key: extract(raw_data[bname], key, bname) for key, *_ in METRICS}
        for bname in ORDER
    }

    # ── Figure setup ──────────────────────────────────────────────────────────
    n_rows = len(METRICS)
    fig = plt.figure(figsize=(20, 21), facecolor=BG)
    fig.suptitle(
        "System Resource Monitor — STT / NLU / TTS Benchmark",
        color=TEXT, fontsize=15, fontweight="bold", y=0.985,
    )

    # 2 GridSpec riêng: line charts (n_rows×3) + violin row (1×n_metrics)
    # vì số cột khác nhau (3 vs 4) không thể dùng chung 1 lưới vuông.
    gs = GridSpec(
        n_rows, len(ORDER), figure=fig,
        height_ratios=[2.6] * n_rows,
        hspace=0.55, wspace=0.30,
        left=0.055, right=0.975, top=0.925, bottom=0.155,
    )
    gs_violin = GridSpec(
        1, n_rows, figure=fig,
        left=0.055, right=0.975, top=0.125, bottom=0.045, wspace=0.30,
    )

    # ── Rows 0..n-1: per-sentence area+line charts ────────────────────────────
    for row, (key, ylabel, fmt_fn) in enumerate(METRICS):
        for col, bname in enumerate(ORDER):
            ax  = fig.add_subplot(gs[row, col])
            y   = bench[bname][key]
            x   = np.arange(len(y))
            n   = raw_data[bname].get("total", len(y))
            color = C[bname]

            has_data = np.any(~np.isnan(y))
            if has_data:
                draw_area_line(ax, x, y, color, A[bname], fmt_fn, bname)
            else:
                ax.text(0.5, 0.5, "Không có dữ liệu\n(sensor không khả dụng)",
                        ha="center", va="center", color=MUTE, fontsize=9,
                        transform=ax.transAxes)

            style_ax(ax, f"{bname}  ·  {ylabel}  [{n} câu]", ylabel)

            # Auto-zoom y-axis to data range so variation is visible
            valid = y[~np.isnan(y)]
            if len(valid) > 0:
                vmin, vmax = float(np.nanmin(y)), float(np.nanmax(y))
                spread = max(vmax - vmin, 1)
                ax.set_ylim(vmin - spread * 0.15, vmax + spread * 0.25)

            # Colored left spine accent
            ax.spines["left"].set_color(color)
            ax.spines["left"].set_linewidth(2.2)

    # ── Bottom row: violin + box distribution comparison (4 metrics) ──────────
    for col, (key, ylabel, fmt_fn) in enumerate(METRICS):
        ax = fig.add_subplot(gs_violin[0, col])
        draw_violin_row(ax, key, ylabel, fmt_fn, bench)

    # ── Legend (top center) ───────────────────────────────────────────────────
    legend_handles = [
        Line2D([0], [0], color=C[b], lw=3.5, label=LABELS[b], solid_capstyle="round")
        for b in ORDER
    ]
    fig.legend(
        handles=legend_handles,
        loc="upper center", ncol=3,
        frameon=True, framealpha=0.15,
        facecolor=CARD, edgecolor=GRID,
        labelcolor=TEXT, fontsize=9,
        bbox_to_anchor=(0.5, 0.955),   # below suptitle
        columnspacing=2.5, handlelength=2,
    )

    # ── Footer: summary stats ─────────────────────────────────────────────────
    parts = []
    for bname in ORDER:
        r = raw_data[bname]
        avg_lat = float(np.nanmean(bench[bname]["latency_ms"])) if len(bench[bname]["latency_ms"]) else 0
        parts.append(
            f"{bname}  {r.get('total','?')} câu  "
            f"RAM≈{r.get('avg_ram_mb', 0):.0f}MB  "
            f"CPU≈{r.get('avg_cpu_pct', 0):.1f}%  "
            f"T≈{r.get('avg_temp_c') or 0:.0f}°C  "
            f"Trễ≈{avg_lat:.0f}ms"
        )
    fig.text(
        0.5, 0.012,
        "   ·   ".join(parts),
        ha="center", color=MUTE, fontsize=7.5,
    )
    fig.text(
        0.975, 0.012,
        f"file: {os.path.basename(args.out)}",
        ha="right", color=MUTE, fontsize=6.5,
    )

    # ── Save ──────────────────────────────────────────────────────────────────
    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    fig.savefig(args.out, dpi=args.dpi, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"\n  Đã lưu: {args.out}  ({args.dpi} DPI)")
    print(f"  Mở: xdg-open {args.out}")


if __name__ == "__main__":
    main()
