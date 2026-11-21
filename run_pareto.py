"""
Compute and visualise the non-dominated set of one instance.

    python run_pareto.py data/toy_instance.json
    python run_pareto.py data/medium_instance.json --time-per-solve 60
    python run_pareto.py data/large_instance.json --secondary max_projects late_projects
    python run_pareto.py data/large_instance.json --lex sequential --step jump
    python run_pareto.py data/large_instance.json --merge results/large results/large_sequential
        (union of the schedules of several runs, re-checked and re-filtered -> results/large_merged)

Outputs (in results/<instance>/):
    pareto_front.csv      one row per non-dominated point (+ supported flag, solve status)
    pareto_front.json     same + the full schedule of every point
    objective_space.png   3-D scatter + 2-D projections
    schedule_*.png        decision-space view of selected solutions
    summary.json          runtime, #MIP solves, exactness flag, ideal / nadir points
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from src.algorithms.epsilon_constraint import epsilon_constraint
from src.algorithms.pareto import classify_supported, non_dominated, to_min
from src.io_parser import ProblemInstance
from src.models.gurobi_model import OBJECTIVES, check_feasibility, evaluate_schedule
from src.visualization.plots import plot_objective_space, plot_schedule


def compromise_index(front, names):
    """Default recommendation shown to the CEO: the point minimising the weighted
    Tchebychev distance to the ideal point, objectives normalised by (nadir-ideal),
    equal weights. This is a neutral starting point for discussion, NOT a model of
    Margaux's preferences (which we do not know).
    The empty schedule (no project done) is always non-dominated but meaningless;
    it is excluded, otherwise it pulls the ideal point to f2 = f3 = 0."""
    cand = [a for a, s in enumerate(front) if s.completed_jobs] or list(range(len(front)))
    pts = {a: to_min(front[a], names) for a in cand}
    k = len(names)
    ideal = [min(p[i] for p in pts.values()) for i in range(k)]
    nadir = [max(p[i] for p in pts.values()) for i in range(k)]
    span = [(n - i) or 1 for i, n in zip(ideal, nadir)]
    dist = {a: max((p[i] - ideal[i]) / span[i] for i in range(k)) for a, p in pts.items()}
    return min(cand, key=lambda a: (dist[a], pts[a]))


def load_front(inst, run_dir):
    """Schedules of a finished run, with objectives recomputed (not trusted from the file)."""
    pts = json.loads((Path(run_dir) / "pareto_front.json").read_text())
    out = []
    for p in pts:
        sol = evaluate_schedule(inst, [(r["staff"], r["job"], r["qualification"], r["day"])
                                       for r in p["schedule"]])
        sol.status = p["status"]
        out.append(sol)
    return out


def write_outputs(inst, front, names, out, meta):
    supported = classify_supported(front, names)
    for s in front:
        errs = check_feasibility(inst, s.schedule)
        assert not errs, f"infeasible schedule returned: {errs[:3]}"

    best = compromise_index(front, names)
    ideal = {n: (max if OBJECTIVES[n][0] < 0 else min)(s.objectives[n] for s in front) for n in names}
    nadir = {n: (min if OBJECTIVES[n][0] < 0 else max)(s.objectives[n] for s in front) for n in names}

    with open(out / "pareto_front.csv", "w", newline="") as f:
        w = csv.writer(f)
        extra = [n for n in OBJECTIVES if n not in names]  # reported, not optimised
        w.writerow(["id", *names, *extra, "n_completed", "supported", "status"])
        for i, (s, sp) in enumerate(zip(front, supported)):
            w.writerow([i, *[s.objectives[n] for n in names + extra],
                        len(s.completed_jobs), sp, s.status])
    with open(out / "pareto_front.json", "w") as f:
        json.dump([{"id": i, "objectives": s.objectives, "supported": sp, "status": s.status,
                    "completed_jobs": s.completed_jobs,
                    "schedule": [dict(staff=a, job=b, qualification=c, day=d) for a, b, c, d in s.schedule]}
                   for i, (s, sp) in enumerate(zip(front, supported))], f, indent=1)
    summary = {"instance": inst.summary(), "objectives": names, "n_points": len(front),
               "n_supported": sum(supported), **meta, "ideal": ideal, "nadir": nadir,
               "recommended_id": best, "recommended": front[best].objectives}
    with open(out / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    title = f"{inst.name}: {len(front)} non-dominated points ({'exact' if meta['exact'] else 'NOT proven exact'})"
    plot_objective_space(front, names, supported, title, out / "objective_space.png", highlight=best)
    i_profit = max(range(len(front)), key=lambda a: (front[a].objectives["profit"], to_min(front[a], names)))
    plot_schedule(inst, front[i_profit], f"{inst.name} - max-profit solution (#{i_profit})",
                  out / "schedule_max_profit.png")
    plot_schedule(inst, front[best], f"{inst.name} - compromise solution (#{best})",
                  out / "schedule_compromise.png")

    print(f"\n{len(front)} non-dominated points ({sum(supported)} supported), "
          f"{meta['n_mip_solves']} MIP solves, {meta['runtime_sec']:.1f}s, exact={meta['exact']}")
    print(f"ideal={ideal}\nnadir={nadir}\nrecommended #{best}: {front[best].objectives}")
    print(f"-> {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("instance")
    ap.add_argument("--secondary", nargs=2, default=["max_projects", "max_duration"],
                    choices=[k for k in OBJECTIVES if k != "profit"])
    ap.add_argument("--time-per-solve", type=float, default=60.0,
                    help="seconds per MIP (per stage with --lex sequential)")
    ap.add_argument("--total-time", type=float, default=None)
    ap.add_argument("--step", choices=["safe", "jump"], default="safe",
                    help="what to do after a solve that hit its time limit")
    ap.add_argument("--lex", choices=["weighted", "sequential"], default="weighted",
                    help="one weighted MIP per point, or profit / f3 / f2 solved in sequence")
    ap.add_argument("--threads", type=int, default=0, help="0 = all cores")
    ap.add_argument("--merge", nargs="+", metavar="RUN_DIR",
                    help="no solving: merge the fronts of finished runs of this instance")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    inst = ProblemInstance.from_json(args.instance)
    tag = inst.name.replace("_instance", "")
    if args.secondary != ["max_projects", "max_duration"]:
        tag += "_" + "_".join(args.secondary)
    names = ["profit", *args.secondary]
    print(f"Instance {inst.name}: {inst.summary()}")

    if args.merge:
        summaries = [json.loads((Path(d) / "summary.json").read_text()) for d in args.merge]
        assert all(s["objectives"] == names for s in summaries), "runs used different objectives"
        found = [sol for d in args.merge for sol in load_front(inst, d)]
        front = non_dominated(found, names)
        out = Path(args.out) / (tag + "_merged")
        out.mkdir(parents=True, exist_ok=True)
        meta = {"merged_from": [str(d) for d in args.merge],
                "n_mip_solves": sum(s["n_mip_solves"] for s in summaries),
                "runtime_sec": round(sum(s["runtime_sec"] for s in summaries), 2),
                "exact": all(s["exact"] for s in summaries),
                "timed_out": any(s["timed_out"] for s in summaries)}
        write_outputs(inst, front, names, out, meta)
        return

    if args.lex != "weighted":
        tag += "_" + args.lex
    out = Path(args.out) / tag
    out.mkdir(parents=True, exist_ok=True)
    res = epsilon_constraint(inst, secondary=args.secondary,
                             time_limit_per_solve=args.time_per_solve,
                             total_time_limit=args.total_time,
                             threads=args.threads, step=args.step, lex=args.lex,
                             checkpoint_path=out / "checkpoint.jsonl")
    meta = {"lex": args.lex, "step": args.step, "time_per_solve": args.time_per_solve,
            "n_mip_solves": res["n_solves"], "runtime_sec": round(res["runtime"], 2),
            "exact": res["exact"], "timed_out": res["timed_out"]}
    write_outputs(inst, res["front"], res["objectives"], out, meta)


if __name__ == "__main__":
    main()
