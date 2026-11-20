"""
Visualisation of
  * the non-dominated set in OBJECTIVE space (3-D scatter + the three 2-D projections)
  * one solution in DECISION space (staff x day grid, like the figure in the subject).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from src.io_parser import ProblemInstance  # noqa: E402
from src.models.gurobi_model import OBJECTIVES, Solution  # noqa: E402

SUPPORTED_COLOR = "#1f6fb4"
UNSUPPORTED_COLOR = "#e07b00"


def plot_objective_space(
    front: List[Solution],
    names: Sequence[str],
    supported: List[bool],
    title: str,
    out_path: Path,
    highlight: Optional[int] = None,
):
    labels = [OBJECTIVES[n][1] for n in names]
    vals = [[s.objectives[n] for s in front] for n in names]
    colors = [SUPPORTED_COLOR if sp else UNSUPPORTED_COLOR for sp in supported]

    fig = plt.figure(figsize=(16, 4.6))
    ax3 = fig.add_subplot(1, 4, 1, projection="3d")
    ax3.scatter(vals[0], vals[1], vals[2], c=colors, s=30, depthshade=False)
    ax3.set_xlabel(labels[0], fontsize=8)
    ax3.set_ylabel(labels[1], fontsize=8)
    ax3.set_zlabel(labels[2], fontsize=8)
    ax3.set_title("3-D non-dominated set", fontsize=10)

    for k, (i, j) in enumerate([(1, 0), (2, 0), (1, 2)]):
        ax = fig.add_subplot(1, 4, k + 2)
        ax.scatter(vals[i], vals[j], c=colors, s=30, alpha=0.75, edgecolors="white", linewidths=0.5)
        if highlight is not None:
            ax.scatter([vals[i][highlight]], [vals[j][highlight]], s=160, facecolors="none",
                       edgecolors="crimson", linewidths=1.8)
        ax.set_xlabel(labels[i], fontsize=9)
        ax.set_ylabel(labels[j], fontsize=9)
        ax.grid(alpha=0.3)
        ax.set_title(f"Projection {labels[j].split()[0]} vs {labels[i].split()[0]}", fontsize=10)

    handles = [Patch(color=SUPPORTED_COLOR, label=f"supported ({sum(supported)})"),
               Patch(color=UNSUPPORTED_COLOR, label=f"unsupported ({len(supported) - sum(supported)})")]
    if highlight is not None:
        handles.append(Patch(facecolor="none", edgecolor="crimson", label="recommended"))
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False)
    fig.suptitle(title, fontsize=12)
    fig.tight_layout(rect=(0, 0.07, 1, 0.95))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_schedule(inst: ProblemInstance, sol: Solution, title: str, out_path: Path):
    """Staff x day grid. Each cell shows 'project,skill'; leave days are hatched."""
    staff = [s.name for s in inst.staff]
    H = inst.horizon
    job_names = [j.name for j in inst.jobs]
    cmap = plt.get_cmap("tab20")
    color = {j: cmap(i % 20) for i, j in enumerate(job_names)}
    cell = {(s, t): (j, q) for s, j, q, t in sol.schedule}

    fig_w = max(8, 0.55 * H + 2)
    fig, ax = plt.subplots(figsize=(fig_w, 0.6 * len(staff) + 2))
    for r, s_obj in enumerate(inst.staff):
        for t in range(1, H + 1):
            x0, y0 = t - 1, r
            if t in s_obj.vacations:
                ax.add_patch(plt.Rectangle((x0, y0), 1, 1, facecolor="#dddddd", hatch="xx",
                                           edgecolor="white"))
            elif (s_obj.name, t) in cell:
                j, q = cell[s_obj.name, t]
                ax.add_patch(plt.Rectangle((x0, y0), 1, 1, facecolor=color[j], edgecolor="white"))
                short = j.replace("Job", "J")
                ax.text(x0 + 0.5, y0 + 0.5, f"{short}\n{q}", ha="center", va="center",
                        fontsize=6.5 if H > 25 else 8)
            else:
                ax.add_patch(plt.Rectangle((x0, y0), 1, 1, facecolor="white", edgecolor="#eeeeee"))
        # due dates are per project, so we mark them in the legend instead
    ax.set_xlim(0, H)
    ax.set_ylim(0, len(staff))
    ax.set_xticks([t - 0.5 for t in range(1, H + 1)])
    ax.set_xticklabels(range(1, H + 1), fontsize=7)
    ax.set_yticks([r + 0.5 for r in range(len(staff))])
    ax.set_yticklabels([f"{s.name} ({','.join(sorted(s.qualifications))})" for s in inst.staff],
                       fontsize=8)
    ax.set_xlabel("working day")
    jobs = {j.name: j for j in inst.jobs}
    handles = [Patch(color=color[j], label=f"{j} (due {jobs[j].due_date})") for j in sol.completed_jobs]
    handles.append(Patch(facecolor="#dddddd", hatch="xx", label="leave"))
    ax.legend(handles=handles, bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=7, frameon=False)
    o = sol.objectives
    ax.set_title(f"{title}\nprofit={o['profit']:.0f} | max projects/employee={o['max_projects']} | "
                 f"longest project={o['max_duration']} days | late projects={o['late_projects']}",
                 fontsize=10)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
