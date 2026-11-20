"""
Pareto utilities: dominance, filtering, supported / unsupported classification.

Convention: every objective vector is converted to "minimisation form"
(profit is negated) before comparison.
"""

from __future__ import annotations

from typing import Dict, List, Sequence

import gurobipy as gp
from gurobipy import GRB

from src.models.gurobi_model import OBJECTIVES, Solution


def to_min(sol: Solution, names: Sequence[str]) -> tuple:
    return tuple(
        -sol.objectives[n] if OBJECTIVES[n][0] == GRB.MAXIMIZE else sol.objectives[n]
        for n in names
    )


def dominates(a: Sequence[float], b: Sequence[float]) -> bool:
    """a dominates b (minimisation): a <= b everywhere and a < b somewhere."""
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def non_dominated(sols: List[Solution], names: Sequence[str]) -> List[Solution]:
    """Keep one solution per non-dominated objective vector."""
    unique: Dict[tuple, Solution] = {}
    for s in sols:
        unique.setdefault(to_min(s, names), s)
    vecs = list(unique)
    keep = [v for v in vecs if not any(dominates(w, v) for w in vecs if w != v)]
    return [unique[v] for v in sorted(keep)]


def classify_supported(sols: List[Solution], names: Sequence[str], eps: float = 1e-6) -> List[bool]:
    """A non-dominated point p is *supported* iff it minimises some strictly positive
    weighted sum over the front, i.e. there exists lambda > 0 with
    lambda.(p - q) <= 0 for every other point q. Objectives are normalised to [0,1]
    so that the weights are comparable. One small LP per point."""
    pts = [to_min(s, names) for s in sols]
    k = len(names)
    lo = [min(p[i] for p in pts) for i in range(k)]
    hi = [max(p[i] for p in pts) for i in range(k)]
    span = [(h - l) or 1.0 for l, h in zip(lo, hi)]
    norm = [[(p[i] - lo[i]) / span[i] for i in range(k)] for p in pts]

    result = []
    env = gp.Env(params={"OutputFlag": 0})
    for a, p in enumerate(norm):
        m = gp.Model(env=env)
        lam = m.addVars(k, lb=0.0)
        delta = m.addVar(lb=-GRB.INFINITY)  # min weight, maximised
        m.addConstr(lam.sum() == 1)
        m.addConstrs(delta <= lam[i] for i in range(k))
        for b, q in enumerate(norm):
            if a != b:
                m.addConstr(gp.quicksum(lam[i] * (p[i] - q[i]) for i in range(k)) <= 0)
        m.setObjective(delta, GRB.MAXIMIZE)
        m.optimize()
        result.append(m.Status == GRB.OPTIMAL and delta.X > eps)
        m.dispose()
    env.dispose()
    return result
