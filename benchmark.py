"""
Scalability benchmark on generated instances.

For each size (horizon, staff, jobs) and several seeds:
  1. generate an instance (saved to data/generated/)
  2. time the single-objective max-profit MIP
  3. run the full epsilon-constraint front under a global time budget
Results -> results/benchmark.csv and results/benchmark.png

    python benchmark.py                       # default grid
    python benchmark.py --front-budget 300 --seeds 1 2
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from src.algorithms.epsilon_constraint import epsilon_constraint  # noqa: E402
from src.generator import generate_instance, save_instance_to_json  # noqa: E402
from src.io_parser import ProblemInstance  # noqa: E402
from src.models.gurobi_model import CompuOptiGurobiModel  # noqa: E402

# (horizon, staff, jobs) - toy=(5,3,5), medium=(22,5,15), large=(36,6,25)
SIZES = [(5, 3, 5), (10, 3, 6), (12, 4, 8), (15, 4, 10), (18, 5, 12), (22, 5, 15)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2])
    ap.add_argument("--profit-limit", type=float, default=120)
    ap.add_argument("--time-per-solve", type=float, default=30)
    ap.add_argument("--front-budget", type=float, default=300)
    args = ap.parse_args()

    out = Path("results")
    out.mkdir(exist_ok=True)
    rows = []
    for (H, S, J) in SIZES:
        for seed in args.seeds:
            name = f"gen_H{H}_S{S}_J{J}_seed{seed}"
            path = Path("data/generated") / f"{name}.json"
            save_instance_to_json(
                generate_instance(horizon=H, num_staff=S, num_jobs=J,
                                  num_qualifications=min(10, max(3, J // 2)), seed=seed),
                path)
            inst = ProblemInstance.from_json(path)
            m = CompuOptiGurobiModel(inst, time_limit_sec=args.profit_limit)
            m.model.update()
            sol = m.solve_single("profit")
            res = epsilon_constraint(inst, time_limit_per_solve=args.time_per_solve,
                                     total_time_limit=args.front_budget, log=lambda _: None)
            row = {
                "instance": name, "H": H, "S": S, "J": J, "seed": seed,
                "n_vars": m.model.NumVars, "n_constrs": m.model.NumConstrs,
                "max_profit": sol.objectives["profit"] if sol else None,
                "profit_time_s": round(sol.runtime, 2) if sol else None,
                "profit_optimal": bool(sol and sol.status == "OPTIMAL"),
                "front_points": len(res["front"]), "front_solves": res["n_solves"],
                "front_time_s": round(res["runtime"], 1), "front_exact": res["exact"],
            }
            rows.append(row)
            print(row, flush=True)
            with open(out / "benchmark.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(row))
                w.writeheader()
                w.writerows(rows)

    # plot: front computation time vs instance size (log scale)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for ax, key, label in [(axes[0], "profit_time_s", "max-profit MIP time (s)"),
                           (axes[1], "front_time_s", "full front time (s)")]:
        xs = [r["n_vars"] for r in rows]
        ys = [r[key] or 0.01 for r in rows]
        exact = [r["profit_optimal"] if key == "profit_time_s" else r["front_exact"] for r in rows]
        ax.scatter([x for x, e in zip(xs, exact) if e], [y for y, e in zip(ys, exact) if e],
                   label="proven optimal / exact", color="#1f6fb4")
        ax.scatter([x for x, e in zip(xs, exact) if not e], [y for y, e in zip(ys, exact) if not e],
                   label="hit time limit", color="#d62728", marker="x")
        ax.set_yscale("log")
        ax.set_xlabel("#variables of the MIP")
        ax.set_ylabel(label)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle("Scalability on generated instances")
    fig.tight_layout()
    fig.savefig(out / "benchmark.png", dpi=140)


if __name__ == "__main__":
    main()
