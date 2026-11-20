"""
Exact epsilon-constraint method for 3 objectives:
    max f1 (profit),  min fA,  min fB     (fA, fB integer-valued, e.g. f2 and f3)

Algorithm
---------
for kA = UB_A, ..., 0 (outer level, bound fA <= kA):
    epsB = UB_B
    repeat:
        solve  lex( max f1, min fB, min fA )  s.t.  fA <= kA, fB <= epsB
        (one MIP, lexicographic order encoded by integer weights)
        record the point p;  epsB = fB(p) - 1
    until infeasible / epsB < 0
    level skipping: if every point found at level kA has fA <= m < kA, then the
    levels m..kA-1 would return exactly the same points -> jump to kA = m - 1.
Finally remove dominated points.

Why it is exact: with integer objectives, stepping epsB by 1 below the last value
visits every non-dominated (f1, fB) pair of the slice fA <= kA, and the
lexicographic tie-breaking guarantees each returned point is not weakly dominated.
Unlike a weighted sum, this also finds UNSUPPORTED points (course: MOCO).
Exactness holds only if each MIP is solved to optimality (status is recorded).
"""

from __future__ import annotations

import time
from typing import Callable, List, Optional, Sequence

from src.io_parser import ProblemInstance
from src.models.gurobi_model import CompuOptiGurobiModel, Solution
from src.algorithms.pareto import non_dominated


def epsilon_constraint(
    instance: ProblemInstance,
    secondary: Sequence[str] = ("max_projects", "max_duration"),
    time_limit_per_solve: float = 60.0,
    total_time_limit: Optional[float] = None,
    threads: int = 0,
    step: str = "safe",
    log: Callable[[str], None] = print,
) -> dict:
    a_name, b_name = secondary
    model = CompuOptiGurobiModel(instance, time_limit_sec=time_limit_per_solve, threads=threads)
    start = time.time()

    ub = {
        "max_projects": len(instance.jobs),
        "max_duration": instance.horizon,
        "late_projects": len(instance.jobs),
    }
    # Start from the trivial bound; the first level is the unconstrained slice and
    # level skipping immediately jumps below the largest useful value of A.
    k_a = ub[a_name]

    found: List[Solution] = []
    n_solves, all_optimal, timed_out = 0, True, False
    while k_a >= 0:
        model.set_bound(a_name, k_a)
        eps_b = ub[b_name]
        level_max_a = -1
        while eps_b >= 0:
            if total_time_limit and time.time() - start > total_time_limit:
                timed_out = True
                break
            model.set_bound(b_name, eps_b)
            # warm start: best known schedule that satisfies the current bounds
            ok = [s for s in found
                  if s.objectives[a_name] <= k_a and s.objectives[b_name] <= eps_b]
            model.set_start(max(ok, key=lambda s: s.objectives["profit"]) if ok else None)
            sol = model.solve_lex_weighted(["profit", b_name, a_name])
            n_solves += 1
            if sol is None:
                break
            all_optimal &= sol.status == "OPTIMAL"
            found.append(sol)
            fa, fb = int(sol.objectives[a_name]), int(sol.objectives[b_name])
            level_max_a = max(level_max_a, fa)
            log(f"  [{a_name}<={k_a:>2}, {b_name}<={eps_b:>2}] -> "
                f"profit={sol.objectives['profit']:.0f} {a_name}={fa} {b_name}={fb} "
                f"({sol.runtime:.1f}s, {sol.status})")
            # Jumping below fb is only valid if p is proven optimal; otherwise a better
            # point with fb < f3 <= eps_b may exist. step="safe" then steps by one
            # (better coverage, slow); step="jump" jumps anyway (fast, for instances
            # where optimality cannot be proven and the front is approximate regardless).
            if sol.status == "OPTIMAL" or step == "jump":
                eps_b = fb - 1
            else:
                eps_b -= 1
        if timed_out:
            break
        if level_max_a < 0:
            break  # nothing feasible at this level -> nothing below either
        k_a = min(k_a, level_max_a) - 1
    model.set_bound(a_name, None)
    model.set_bound(b_name, None)

    names = ["profit", a_name, b_name]
    front = non_dominated(found, names)
    return {
        "front": front,
        "objectives": names,
        "n_solves": n_solves,
        "runtime": time.time() - start,
        "exact": all_optimal and not timed_out,
        "timed_out": timed_out,
    }
