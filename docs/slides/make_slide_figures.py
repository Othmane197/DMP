"""
Slide figures built from the fronts in results/ (run from the DMP folder):

    python docs/slides/make_slide_figures.py

heatmap_<instance>.png : best profit available for every pair of limits
    (max projects per engineer <= k, longest project <= e days).
    On an exact front this IS the optimal profit under those limits (the front contains,
    for every box, a point at least as good as the box optimum). On an approximate front
    it is the best profit found, i.e. a lower bound.
certificate_large.png : certified gap of every (f2, f3) box of the large front, before / after
    certify_front.py (needs results/large_certify).
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent

INK, MUTED, SURFACE = "#1d2433", "#4a5568", "#f7f6f2"
# sequential blue ramp, steps 100 -> 700 (light = low profit, dark = high profit)
RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
        "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
CMAP = LinearSegmentedColormap.from_list("seq_blue", RAMP)


def load(name):
    with open(ROOT / "results" / name / "pareto_front.csv", newline="") as f:
        return [{k: float(v) if k in ("profit", "max_projects", "max_duration") else v
                 for k, v in r.items()} for r in csv.DictReader(f)]


def best_grid(rows, ks, es):
    return [[max([r["profit"] for r in rows if r["max_projects"] <= k and r["max_duration"] <= e] + [0])
             for k in ks] for e in es]


def heatmap(name, out, exact, e_max=None, rings=()):
    rows = load(name)
    ks = list(range(1, int(max(r["max_projects"] for r in rows)) + 1))
    top = int(max(r["max_duration"] for r in rows))
    es = list(range(2, min(top, e_max or top) + 1))
    grid = best_grid(rows, ks, es)
    vmax = max(max(row) for row in grid)

    fig, ax = plt.subplots(figsize=(6.4, 4.7), dpi=170)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    fs = 12.5 if len(es) <= 12 else 9.5
    for i, e in enumerate(es):
        for j, k in enumerate(ks):
            v = grid[i][j]
            frac = v / vmax if vmax else 0
            ax.add_patch(Rectangle((j, i), 1, 1, facecolor=CMAP(frac), edgecolor=SURFACE, linewidth=2))
            ax.text(j + 0.5, i + 0.5, f"{v:.0f}", ha="center", va="center", fontsize=fs,
                    color=SURFACE if frac > 0.55 else INK)
    for (k, e) in rings:
        if k in ks and e in es:
            ax.add_patch(Rectangle((ks.index(k) + 0.06, es.index(e) + 0.06), 0.88, 0.88, fill=False,
                                   edgecolor="#8a1538", linewidth=2.6))
    ax.set_xlim(0, len(ks))
    ax.set_ylim(0, len(es))
    ax.set_xticks([j + 0.5 for j in range(len(ks))], [str(k) for k in ks])
    ax.set_yticks([i + 0.5 for i in range(len(es))], [str(e) for e in es])
    ax.tick_params(length=0, labelsize=11.5, colors=MUTED)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xlabel("max projects per engineer  ≤ k", color=MUTED, fontsize=12.5)
    ax.set_ylabel("longest project  ≤ e days", color=MUTED, fontsize=12.5)
    label = "optimal profit (exact front)" if exact else "best profit found (lower bound)"
    ax.set_title(f"{label} for each pair of limits", color=INK, fontsize=12.5, loc="left")
    fig.tight_layout()
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)
    return ks, es, grid


def certificate_plot(out):
    """Large: certified gap (UB - best) / UB of every (f2, f3) box of certify_front.py,
    for the first approximation (runs A + B, hollow) and after the certificate (run C, filled)."""
    import json
    import statistics
    cdir = ROOT / "results" / "large_certify"
    with open(cdir / "certificate.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    gub = json.loads((cdir / "summary.json").read_text())["global_ub"]
    pf = [float(r["profit_front"]) for r in rows]
    bf = [float(r["best_found"]) for r in rows]
    gb = [float(r["gap_before_pct"]) for r in rows]
    ga = [float(r["gap_pct"]) for r in rows]
    blue, gray, line, band = "#256abf", "#8b94a3", "#c9ced6", "#e3ebf5"

    fig, ax = plt.subplots(figsize=(6.4, 4.7), dpi=170)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    thr = 0.9 * gub
    ymax = 5 * (int(max(gb + ga)) // 5 + 1) + 2
    ax.axvspan(thr, gub * 1.03, color=band, lw=0, zorder=0)
    top = [g for b, g in zip(bf, ga) if b >= thr]
    ax.text(thr - 8, ymax - 1.5, f"profit ≥ 90% of max:\nmedian gap {statistics.median(top):.1f}%",
            ha="right", va="top", fontsize=11, color=INK)
    for y in range(10, int(ymax), 10):
        ax.axhline(y, color="#dfe2e7", lw=0.8, zorder=0)
    for x0, x1, y0, y1 in zip(pf, bf, gb, ga):
        if (x0, y0) != (x1, y1):
            ax.plot([x0, x1], [y0, y1], color=line, lw=1.0, zorder=1)
    ax.scatter(pf, gb, s=42, facecolors="none", edgecolors=gray, linewidths=1.3, zorder=2,
               label="first approximation (A + B)")
    ax.scatter(bf, ga, s=42, color=blue, edgecolors=SURFACE, linewidths=0.8, zorder=3,
               label="after the certificate (C)")
    n0 = sum(1 for g in ga if g == 0)
    ax.set_xlim(0, gub * 1.03)
    ax.set_ylim(-1.5, ymax)
    yt = list(range(0, int(ymax), 10))
    ax.set_yticks(yt, ["0 = proven"] + [str(y) for y in yt[1:]])
    ax.tick_params(labelsize=11, colors=MUTED, length=0)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    for s in ["left", "bottom"]:
        ax.spines[s].set_color("#b9bfc9")
    ax.set_xlabel("best profit found within the limits (f2 ≤ a, f3 ≤ b)", color=MUTED, fontsize=12)
    ax.set_ylabel("certified gap  (UB − profit) / UB, %", color=MUTED, fontsize=12)
    ax.set_title("Large: how far each point can be from the optimum", color=INK, fontsize=12.5, loc="left",
                 pad=30)
    ax.legend(loc="lower left", bbox_to_anchor=(-0.02, 1.0), ncol=2, fontsize=10.5, frameon=False,
              labelcolor=INK, handletextpad=0.3, columnspacing=1.2)
    fig.tight_layout()
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)
    return n0, statistics.median(top)


def main():
    which = sys.argv[1:] or ["medium", "large"]
    if "medium" in which:
        ks, es, g = heatmap("medium", OUT / "heatmap_medium.png", exact=True, rings=[(3, 5), (5, 6)])
        print("medium k=", ks, "e=", es)
        for e, row in zip(es, g):
            print(f"  e<={e:2d}", [int(v) for v in row])
    if "large" in which:
        ks, es, g = heatmap("large_merged", OUT / "heatmap_large.png", exact=False, e_max=16)
        print("large k=", ks, "e=", es)
        for e, row in zip(es, g):
            print(f"  e<={e:2d}", [int(v) for v in row])
        if (ROOT / "results" / "large_certify" / "certificate.csv").exists():
            n0, med_top = certificate_plot(OUT / "certificate_large.png")
            print(f"certificate_large.png: {n0} boxes proven, median gap at >= 90% of max {med_top:.2f}%")


if __name__ == "__main__":
    main()
