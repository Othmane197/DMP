"""
Quality certificate for an approximate front (used on the large instance).

For every point i of a finished front with values (profit p_i, f2 = a_i, f3 = b_i):
    solve   max profit   s.t.  f2 <= a_i,  f3 <= b_i      (warm-started from point i)
and record Gurobi's incumbent and its proven upper bound UB_i.
    -> the true best profit in that (f2, f3) box lies in [p_i, UB_i]
    -> gap_i = (UB_i - p_i) / UB_i  measures how far point i can be from the true front.
Points whose profit is already proven optimal for a box containing (a_i, b_i)
(status OPTIMAL) are not re-solved.
Any schedule with a better profit is kept and written as a run that run_pareto.py --merge accepts.

Optionally (--level LEVEL) it also sweeps one missing f2 level (e.g. f2 <= 1, never reached
by the large runs): lex(max profit, min f3, min f2) s.t. f2 <= LEVEL, f3 <= eps, eps = f3 - 1.

    python certify_front.py data/large_instance.json results/large_merged_before_certify \
        --time-per-solve 120 --level 1 --global-ub 817
Resumable: finished solves are appended to <out>/certificate.jsonl and replayed on restart.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from pathlib import Path

from gurobipy import GRB

from src.io_parser import ProblemInstance
from src.models.gurobi_model import CompuOptiGurobiModel, check_feasibility, evaluate_schedule


def lower_priority():
    """Run below normal priority on Windows so the laptop stays usable."""
    if sys.platform == "win32":
        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL_PRIORITY_CLASS


def proven_for_profit(status: str) -> bool:
    # Only a fully proven solve counts. The large sequential run was written while
    # solve_lexicographic named the LAST stage that timed out, so a label such as
    # TIME_LIMIT(max_projects) there does not prove that the profit stage finished
    # (its runtimes show 2-3 stages hitting the 60 s limit).
    return status == "OPTIMAL"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("instance")
    ap.add_argument("run_dir", help="finished run whose pareto_front.json is certified")
    ap.add_argument("--time-per-solve", type=float, default=120.0)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--level", type=int, default=None, help="also sweep this f2 level")
    ap.add_argument("--global-ub", type=float, default=None,
                    help="proven upper bound on the max profit over ALL schedules (e.g. the proven "
                         "max profit); every box bound is capped by it")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    lower_priority()

    inst = ProblemInstance.from_json(args.instance)
    tag = inst.name.replace("_instance", "")
    out = Path(args.out or f"results/{tag}_certify")
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "certificate.jsonl"
    done = {}
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            done[rec["key"]] = rec

    pts = json.loads((Path(args.run_dir) / "pareto_front.json").read_text())
    model = CompuOptiGurobiModel(inst, time_limit_sec=args.time_per_solve, threads=args.threads)
    m = model.model
    t0 = time.time()
    n_new_solves = 0

    def record(rec):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        done[rec["key"]] = rec

    # ---------------------------------------------------------------- 1. box bounds
    for p in pts:
        o = p["objectives"]
        a, b, prof = int(o["max_projects"]), int(o["max_duration"]), float(o["profit"])
        key = f"box:{a}:{b}"
        if key in done or a == 0:
            continue
        at_global_max = args.global_ub is not None and prof >= args.global_ub - 1e-6
        if proven_for_profit(p["status"]) or at_global_max:
            # proven by its own run, or it already reaches the proven max profit over ALL schedules
            record({"key": key, "id": p["id"], "f2": a, "f3": b, "profit_front": prof,
                    "best": prof, "bound": prof,
                    "status": "PROVEN_IN_RUN" if proven_for_profit(p["status"]) else "PROVEN_GLOBAL_UB",
                    "runtime": 0.0, "schedule": None})
            continue
        sched = [(r["staff"], r["job"], r["qualification"], r["day"]) for r in p["schedule"]]
        model.set_bound("max_projects", a)
        model.set_bound("max_duration", b)
        model.set_start(evaluate_schedule(inst, sched))
        m.setObjective(model.expr["profit"], GRB.MAXIMIZE)
        m.optimize()
        n_new_solves += 1
        best = round(m.ObjVal) if m.SolCount else prof
        bound = m.ObjBound if m.SolCount else float("nan")
        status = "OPTIMAL" if m.Status == GRB.OPTIMAL else "TIME_LIMIT"
        new_sched = None
        if m.SolCount and best > prof + 0.5:
            new_sched = [list(k) for k, v in model.x.items() if v.X > 0.5]
        rec = {"key": key, "id": p["id"], "f2": a, "f3": b, "profit_front": prof,
               "best": best, "bound": bound, "status": status, "runtime": round(m.Runtime, 1),
               "schedule": new_sched}
        record(rec)
        print(f"#{p['id']:>2} f2<={a:>2} f3<={b:>2}: front {prof:.0f}, best {best:.0f}, "
              f"bound {bound:.1f} ({status}, {m.Runtime:.0f}s)", flush=True)

    # ---------------------------------------------------- 2. sweep a missing f2 level
    if args.level is not None:
        k = args.level
        model.set_bound("max_projects", k)
        eps = inst.horizon
        while eps >= 0:
            key = f"level:{k}:{eps}"
            rec = done.get(key)
            if rec is None:
                model.set_bound("max_duration", eps)
                model.set_start(None)
                sol = model.solve_lexicographic(["profit", "max_duration", "max_projects"])
                n_new_solves += 1
                rec = {"key": key, "f2_limit": k, "f3_limit": eps, "found": sol is not None,
                       "status": sol.status if sol else None,
                       "runtime": round(sol.runtime, 1) if sol else 0.0,
                       "objectives": sol.objectives if sol else None,
                       "schedule": [list(r) for r in sol.schedule] if sol else None}
                record(rec)
                if sol:
                    print(f"level f2<={k}, f3<={eps:>2}: profit {sol.objectives['profit']:.0f} "
                          f"f2={sol.objectives['max_projects']} f3={sol.objectives['max_duration']} "
                          f"({sol.status}, {sol.runtime:.0f}s)", flush=True)
            if not rec["found"] or not rec["schedule"]:
                break  # infeasible, or only the empty schedule found: nothing shorter to find
            # jump below the duration found, even if unproven (like run_pareto --step jump):
            # this level is approximate anyway and stepping by 1 would cost ~36 solves
            eps = int(rec["objectives"]["max_duration"]) - 1

    # ------------------------------------------------------------- 3. write outputs
    model.set_bound("max_projects", None)
    model.set_bound("max_duration", None)
    boxes = [r for key, r in done.items() if key.startswith("box:")]
    found_scheds = [r["schedule"] for r in boxes if r.get("schedule")]
    found_scheds += [r["schedule"] for key, r in done.items()
                     if key.startswith("level:") and r.get("schedule")]
    front_out = []
    for i, sc in enumerate(found_scheds):
        sched = [tuple(r) for r in sc]
        errs = check_feasibility(inst, sched)
        assert not errs, errs[:3]
        sol = evaluate_schedule(inst, sched)
        front_out.append({"id": i, "objectives": sol.objectives, "supported": None,
                          "status": "CERTIFY", "completed_jobs": sol.completed_jobs,
                          "schedule": [dict(staff=a, job=b, qualification=c, day=d)
                                       for a, b, c, d in sol.schedule]})
    (out / "pareto_front.json").write_text(json.dumps(front_out, indent=1))

    # Box i: the best profit with f2 <= a_i and f3 <= b_i lies in [best_i, UB_i].
    #   gap_before = (UB - profit of the original point) / UB   (quality of the input front)
    #   gap        = (UB - best found)                  / UB   (quality after certification)
    # Profits are integers, so UB = floor(Gurobi bound), capped by --global-ub if given.
    gaps, gaps_before = [], []
    for r in sorted(boxes, key=lambda r: r["id"]):
        ub = int(r["bound"] + 1e-6) if r["bound"] == r["bound"] else None
        if ub is not None and args.global_ub is not None:
            ub = min(ub, int(args.global_ub + 1e-6))
        r["bound_int"] = ub
        r["gap_before_pct"] = round(100 * (ub - r["profit_front"]) / ub, 2) if ub else None
        r["gap_pct"] = round(100 * max(0, ub - r["best"]) / ub, 2) if ub else None
        gaps.append(r["gap_pct"])
        gaps_before.append(r["gap_before_pct"])
    with open(out / "certificate.csv", "w", encoding="utf-8") as f:
        f.write("id,f2,f3,profit_front,best_found,upper_bound,gap_before_pct,gap_pct,status,runtime_s\n")
        for r in sorted(boxes, key=lambda r: r["id"]):
            f.write(f"{r['id']},{r['f2']},{r['f3']},{r['profit_front']:.0f},{r['best']:.0f},"
                    f"{r['bound_int']},{r['gap_before_pct']},{r['gap_pct']},{r['status']},{r['runtime']}\n")
    valid = [g for g in gaps if g is not None]
    valid_before = [g for g in gaps_before if g is not None]
    runtime = sum(r["runtime"] for r in done.values())
    summary = {"objectives": ["profit", "max_projects", "max_duration"],
               "n_points": len(front_out),
               "n_mip_solves": sum(1 for r in done.values()
                                   if r.get("status") not in ("PROVEN_IN_RUN", "PROVEN_GLOBAL_UB")),
               "runtime_sec": round(runtime, 1), "exact": False, "timed_out": False,
               "certified_from": str(args.run_dir), "time_per_solve": args.time_per_solve,
               "global_ub": args.global_ub,
               "boxes": len(boxes),
               "boxes_proven": sum(1 for r in boxes if r["gap_pct"] == 0),
               "boxes_improved": sum(1 for r in boxes if r.get("schedule")),
               "gap_pct_max": max(valid) if valid else None,
               "gap_pct_median": sorted(valid)[len(valid) // 2] if valid else None,
               "gap_pct_mean": round(sum(valid) / len(valid), 2) if valid else None,
               "gap_before_pct_max": max(valid_before) if valid_before else None,
               "gap_before_pct_median": sorted(valid_before)[len(valid_before) // 2] if valid_before else None}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"wall time this session {time.time() - t0:.0f}s, new solves {n_new_solves}")


if __name__ == "__main__":
    main()
