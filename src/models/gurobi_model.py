"""
Gurobi MIP formulation of the CompuOpti staffing & scheduling problem.

Notation (see docs/PROJECT_EXPLAINED.md for the full write-up)
------------------------------------------------------------------
Parameters
    H               horizon (working days), T = {1..H}
    S, Q, J         staff, qualifications, projects
    QS[s]           qualifications held by s,   V[s] leave days of s
    n[j,q]          person-days of skill q required by project j
    G[j], d[j], c[j]  gain, due date, daily lateness penalty of project j

Decision variables
    x[s,j,q,t] in {0,1}  s works on (j,q) on day t.  Only created when
                         q in QS[s], n[j,q] > 0 and t not in V[s]
                         -> qualification and leave constraints hold by construction.
    y[j]   in {0,1}  project j is fully executed within the horizon
    z[s,j] in {0,1}  s works at least one day on j
    u[j] in {0,1}    j is delivered late
    P, D integer     max #projects per employee, max project duration
  Time variables, default "step" formulation (time-indexed, no big-M):
    st[j,t] in {0,1} j has started on or before day t     (non-decreasing in t)
    fi[j,t] in {0,1} j has finished on or before day t    (non-decreasing, fi <= st)
    x[s,j,q,t] <= st[j,t],  x[s,j,q,t] <= 1 - fi[j,t-1]
    duration(j) = sum_t st[j,t] - sum_t fi[j,t-1]          (#days the project is "open")
    late(j)     = sum_{t > d[j]} (y[j] - fi[j,t-1])        (= max(0, end - d[j]))
  Alternative "bigm" formulation (kept for comparison, much weaker LP relaxation):
    b[j], e[j] in [0,H] start / end day with e[j] >= t x, b[j] <= t + H(1-x)

Objectives
    f1 = sum_j G[j] y[j] - c[j] late(j)        (maximize profit)
    f2 = P = max_s sum_j z[s,j]                (minimize max #projects per employee)
    f3 = D = max_j duration(j)                 (minimize longest project duration)
    f4 = sum_j u[j]                            (minimize #late projects)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import gurobipy as gp
from gurobipy import GRB

from src.io_parser import ProblemInstance

# Objective metadata: name -> (sense, human label)
OBJECTIVES = {
    "profit": (GRB.MAXIMIZE, "f1 Profit"),
    "max_projects": (GRB.MINIMIZE, "f2 Max #projects / employee"),
    "max_duration": (GRB.MINIMIZE, "f3 Longest project duration"),
    "late_projects": (GRB.MINIMIZE, "f4 #Late projects"),
}


@dataclass
class Solution:
    """A schedule plus its objective vector (recomputed from the schedule)."""

    schedule: List[Tuple[str, str, str, int]]  # (staff, job, qualification, day)
    objectives: Dict[str, float]
    completed_jobs: List[str]
    status: str = "OPTIMAL"
    runtime: float = 0.0
    meta: Dict[str, float] = field(default_factory=dict)

    def vector(self, names: Sequence[str]) -> Tuple[float, ...]:
        return tuple(self.objectives[n] for n in names)


class CompuOptiGurobiModel:
    def __init__(
        self,
        instance: ProblemInstance,
        time_limit_sec: float = 60.0,
        verbose: bool = False,
        threads: int = 0,
        formulation: str = "step",
    ):
        assert formulation in ("step", "bigm")
        self.formulation = formulation
        self.inst = instance
        self.H = instance.horizon
        self.T = list(range(1, self.H + 1))
        self.S = [s.name for s in instance.staff]
        self.J = [j.name for j in instance.jobs]
        self.staff = {s.name: s for s in instance.staff}
        self.jobs = {j.name: j for j in instance.jobs}

        self.model = gp.Model(f"CompuOpti_{instance.name}")
        self.model.Params.OutputFlag = 1 if verbose else 0
        self.model.Params.TimeLimit = time_limit_sec
        self.model.Params.Threads = threads
        # All objectives are integer-valued: stop as soon as the gap < 1 unit.
        self.model.Params.MIPGap = 0.0
        self.model.Params.MIPGapAbs = 0.999

        self._build_variables()
        self._build_constraints()
        self._build_objective_expressions()
        self._eps_constrs: Dict[str, gp.Constr] = {}

    # ------------------------------------------------------------------ build
    def _build_variables(self):
        m, H = self.model, self.H
        self.x: Dict[tuple, gp.Var] = {}
        for s_obj in self.inst.staff:
            for j_obj in self.inst.jobs:
                for q, req in j_obj.working_days_per_qualification.items():
                    if req <= 0 or q not in s_obj.qualifications:
                        continue
                    for t in self.T:
                        if t in s_obj.vacations:
                            continue
                        self.x[s_obj.name, j_obj.name, q, t] = m.addVar(
                            vtype=GRB.BINARY, name=f"x[{s_obj.name},{j_obj.name},{q},{t}]"
                        )

        # index helpers
        self.x_by_st: Dict[tuple, List[gp.Var]] = {}
        self.x_by_jq: Dict[tuple, List[gp.Var]] = {}
        self.x_by_sj: Dict[tuple, List[gp.Var]] = {}
        self.x_by_j: Dict[str, List[tuple]] = {j: [] for j in self.J}
        for (s, j, q, t), v in self.x.items():
            self.x_by_st.setdefault((s, t), []).append(v)
            self.x_by_jq.setdefault((j, q), []).append(v)
            self.x_by_sj.setdefault((s, j), []).append(v)
            self.x_by_j[j].append((t, v))

        self.y = {j: m.addVar(vtype=GRB.BINARY, name=f"y[{j}]") for j in self.J}
        self.z = {
            (s, j): m.addVar(vtype=GRB.BINARY, name=f"z[{s},{j}]")
            for (s, j) in self.x_by_sj
        }
        self.u = {j: m.addVar(vtype=GRB.BINARY, name=f"u[{j}]") for j in self.J}
        if self.formulation == "step":
            self.st = {(j, t): m.addVar(vtype=GRB.BINARY, name=f"st[{j},{t}]")
                       for j in self.J for t in self.T}
            self.fi = {(j, t): m.addVar(vtype=GRB.BINARY, name=f"fi[{j},{t}]")
                       for j in self.J for t in self.T}
            for j in self.J:
                self.fi[j, 0] = 0  # nothing is finished before day 1
        else:
            self.b = {j: m.addVar(vtype=GRB.INTEGER, lb=0, ub=H, name=f"b[{j}]") for j in self.J}
            self.e = {j: m.addVar(vtype=GRB.INTEGER, lb=0, ub=H, name=f"e[{j}]") for j in self.J}
            self.l = {j: m.addVar(vtype=GRB.INTEGER, lb=0, ub=H, name=f"l[{j}]") for j in self.J}
        self.P = m.addVar(vtype=GRB.INTEGER, lb=0, ub=len(self.J), name="P")
        self.D = m.addVar(vtype=GRB.INTEGER, lb=0, ub=H, name="D")

    def _build_constraints(self):
        m, H = self.model, self.H

        # (C1) daily uniqueness: one (project, skill) per employee per day.
        for (s, t), vs in self.x_by_st.items():
            m.addConstr(gp.quicksum(vs) <= 1, name=f"unique[{s},{t}]")

        # (C2) coverage + execution uniqueness: a project is either fully staffed
        # exactly once (y=1) or not touched at all (y=0).
        for j_obj in self.inst.jobs:
            j = j_obj.name
            for q, req in j_obj.working_days_per_qualification.items():
                if req <= 0:
                    continue
                vs = self.x_by_jq.get((j, q), [])
                if not vs:  # nobody can do skill q -> project impossible
                    m.addConstr(self.y[j] == 0, name=f"impossible[{j},{q}]")
                else:
                    m.addConstr(gp.quicksum(vs) == req * self.y[j], name=f"cover[{j},{q}]")

        # (C3) z[s,j] = 1 iff s works on j at least once.
        for (s, j), vs in self.x_by_sj.items():
            for v in vs:
                m.addConstr(v <= self.z[s, j])
            m.addConstr(self.z[s, j] <= gp.quicksum(vs))

        # (C4)-(C5) time window, lateness, late flag
        if self.formulation == "step":
            self._time_constraints_step()
        else:
            self._time_constraints_bigm()

        # (C6) objective-defining max constraint for P (D is defined in C4).
        for s in self.S:
            zs = [self.z[s, j] for j in self.J if (s, j) in self.z]
            if zs:
                m.addConstr(self.P >= gp.quicksum(zs), name=f"P[{s}]")

    def _time_constraints_step(self):
        m, H = self.model, self.H
        st, fi = self.st, self.fi
        self.late_expr = {}
        for j_obj in self.inst.jobs:
            j, d = j_obj.name, j_obj.due_date
            for t in self.T:
                if t > 1:
                    m.addConstr(st[j, t - 1] <= st[j, t])
                    m.addConstr(fi[j, t - 1] <= fi[j, t])
                m.addConstr(fi[j, t] <= st[j, t])
            # started (and finished) by the end of the horizon iff executed
            m.addConstr(st[j, H] == self.y[j])
            m.addConstr(fi[j, H] == self.y[j])
            for t, v in self.x_by_j[j]:
                m.addConstr(v <= st[j, t])
                m.addConstr(v <= 1 - fi[j, t - 1])
            duration = gp.quicksum(st[j, t] - fi[j, t - 1] for t in self.T)
            m.addConstr(self.D >= duration, name=f"D[{j}]")
            # days after the due date on which an executed project is not yet finished
            # (= max(0, end - d); counts from d+1 even if the project starts later)
            self.late_expr[j] = gp.quicksum(self.y[j] - fi[j, t - 1] for t in self.T if t > d)
            if d < H:
                # late iff executed and not finished by the due date
                m.addConstr(self.u[j] >= self.y[j] - fi[j, d], name=f"lateflag[{j}]")

    def _time_constraints_bigm(self):
        m, H = self.model, self.H
        for j in self.J:
            for t, v in self.x_by_j[j]:
                m.addConstr(self.e[j] >= t * v)
                m.addConstr(self.b[j] <= t + H * (1 - v))
            m.addConstr(self.b[j] <= self.e[j])
            # only completed projects count (big-M = H+1 deactivates it when y=0)
            m.addConstr(self.D >= self.e[j] - self.b[j] + 1 - (H + 1) * (1 - self.y[j]))
        for j_obj in self.inst.jobs:
            j = j_obj.name
            m.addConstr(self.l[j] >= self.e[j] - j_obj.due_date)
            m.addConstr(self.l[j] <= H * self.u[j])
        self.late_expr = {j: gp.LinExpr(self.l[j]) for j in self.J}

    def _build_objective_expressions(self):
        self.expr = {
            "profit": gp.quicksum(
                self.jobs[j].gain * self.y[j] - self.jobs[j].daily_penalty * self.late_expr[j]
                for j in self.J
            ),
            "max_projects": gp.LinExpr(self.P),
            "max_duration": gp.LinExpr(self.D),
            "late_projects": gp.quicksum(self.u.values()),
        }

    # ------------------------------------------------------------- epsilon
    def set_bound(self, name: str, value: Optional[float]):
        """Add / update / remove an epsilon constraint on objective `name`.
        For a minimised objective the bound is an upper bound, for profit a lower bound."""
        if name in self._eps_constrs:
            self.model.remove(self._eps_constrs.pop(name))
        if value is None:
            return
        sense, _ = OBJECTIVES[name]
        if sense == GRB.MINIMIZE:
            c = self.model.addConstr(self.expr[name] <= value, name=f"eps[{name}]")
        else:
            c = self.model.addConstr(self.expr[name] >= value, name=f"eps[{name}]")
        self._eps_constrs[name] = c

    def set_start(self, sol: Optional["Solution"]):
        """MIP start from a known schedule (only x is given, Gurobi completes the rest).
        A time-limited solve then can never return worse than this schedule."""
        busy = set(sol.schedule) if sol else set()
        for k, v in self.x.items():
            v.Start = 1.0 if k in busy else (0.0 if sol else GRB.UNDEFINED)

    # --------------------------------------------------------------- solve
    def solve_lexicographic(self, order: Sequence[str]) -> Optional[Solution]:
        """Hierarchical (lexicographic) optimisation: order[0] has top priority.
        Returns None if infeasible or no solution found in time."""
        # Implemented by hand (solve, freeze the optimal value, solve the next one):
        # Gurobi 13's native setObjectiveN crashes when objectives are re-set on
        # the same model, and the manual version is easier to explain anyway.
        m = self.model
        frozen: List[gp.Constr] = []
        status, total_time, sol_found = "OPTIMAL", 0.0, True
        try:
            for name in order:
                sense, _ = OBJECTIVES[name]
                m.setObjective(self.expr[name], sense)
                m.optimize()
                total_time += m.Runtime
                if m.SolCount == 0:
                    sol_found = False
                    break
                if m.Status != GRB.OPTIMAL and status == "OPTIMAL":
                    # name the FIRST stage that was not proven: every stage before it is proven
                    # (runs before 2026-11-23 named the last one, which proves nothing)
                    status = f"TIME_LIMIT({name})"
                best = round(m.ObjVal)  # all objectives are integer-valued
                if sense == GRB.MINIMIZE:
                    frozen.append(m.addConstr(self.expr[name] <= best))
                else:
                    frozen.append(m.addConstr(self.expr[name] >= best))
            sol = self._extract(status) if sol_found else None
        finally:
            for c in frozen:
                m.remove(c)
        if sol is not None:
            sol.runtime = total_time
        return sol

    def solve_lex_weighted(self, order: Sequence[str]) -> Optional[Solution]:
        """Same optimum as solve_lexicographic, in ONE MIP. Because every objective is
        an integer with a known range, lex order = weighted sum with weights
        w_k = prod_{i>k} (range_i + 1): one unit of a higher-priority objective is
        worth more than any change of all lower-priority ones together."""
        ranges = {"profit": sum(j.gain for j in self.inst.jobs) + 1,
                  "max_projects": len(self.J), "max_duration": self.H,
                  "late_projects": len(self.J)}
        weight, w = {}, 1.0
        for name in reversed(order):
            weight[name] = w
            w *= ranges[name] + 1
        # minimise; profit enters with a negative sign
        obj = gp.quicksum(
            (-weight[n] if OBJECTIVES[n][0] == GRB.MAXIMIZE else weight[n]) * self.expr[n]
            for n in order
        )
        m = self.model
        m.setObjective(obj, GRB.MINIMIZE)
        m.optimize()
        if m.SolCount == 0:
            return None
        names = {GRB.OPTIMAL: "OPTIMAL", GRB.TIME_LIMIT: "TIME_LIMIT", GRB.INTERRUPTED: "INTERRUPTED"}
        sol = self._extract(names.get(m.Status, f"STATUS_{m.Status}"))
        return sol

    def solve_single(self, name: str) -> Optional[Solution]:
        return self.solve_lexicographic([name])

    def _extract(self, status: str) -> Solution:
        schedule = [k for k, v in self.x.items() if v.X > 0.5]
        sol = evaluate_schedule(self.inst, schedule)
        sol.status = status
        sol.runtime = self.model.Runtime
        return sol


# ---------------------------------------------------------------------------
# Independent evaluation / validation (does NOT trust the solver variables)
# ---------------------------------------------------------------------------

def evaluate_schedule(inst: ProblemInstance, schedule: List[Tuple[str, str, str, int]]) -> Solution:
    """Recompute all objectives from the raw assignment list."""
    jobs = {j.name: j for j in inst.jobs}
    days_by_job: Dict[str, List[int]] = {}
    projects_by_staff: Dict[str, set] = {s.name: set() for s in inst.staff}
    for s, j, q, t in schedule:
        days_by_job.setdefault(j, []).append(t)
        projects_by_staff[s].add(j)

    completed = sorted(days_by_job, key=lambda j: [jj.name for jj in inst.jobs].index(j))
    profit, late, max_dur = 0.0, 0, 0
    for j in completed:
        end, start = max(days_by_job[j]), min(days_by_job[j])
        lateness = max(0, end - jobs[j].due_date)
        profit += jobs[j].gain - jobs[j].daily_penalty * lateness
        late += lateness > 0
        max_dur = max(max_dur, end - start + 1)

    objectives = {
        "profit": profit,
        "max_projects": max((len(p) for p in projects_by_staff.values()), default=0),
        "max_duration": max_dur,
        "late_projects": late,
    }
    return Solution(schedule=sorted(schedule, key=lambda r: (r[0], r[3])),
                    objectives=objectives, completed_jobs=completed)


def check_feasibility(inst: ProblemInstance, schedule: List[Tuple[str, str, str, int]]) -> List[str]:
    """Return the list of violated constraints (empty list = feasible)."""
    errors = []
    staff = {s.name: s for s in inst.staff}
    jobs = {j.name: j for j in inst.jobs}
    seen_day = set()
    done: Dict[tuple, int] = {}
    for s, j, q, t in schedule:
        if not 1 <= t <= inst.horizon:
            errors.append(f"day {t} outside horizon")
        if q not in staff[s].qualifications:
            errors.append(f"{s} lacks skill {q} ({j}, day {t})")
        if t in staff[s].vacations:
            errors.append(f"{s} works on leave day {t}")
        if (s, t) in seen_day:
            errors.append(f"{s} has two assignments on day {t}")
        seen_day.add((s, t))
        if q not in jobs[j].working_days_per_qualification:
            errors.append(f"{j} does not need skill {q}")
        done[j, q] = done.get((j, q), 0) + 1
    for j in {j for (j, _) in done}:
        for q, req in jobs[j].working_days_per_qualification.items():
            if done.get((j, q), 0) != req:
                errors.append(f"{j}/{q}: {done.get((j, q), 0)} days done, {req} required")
    return errors
