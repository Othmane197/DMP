# CompuOpti project: what was built, how, and how good it is

This document explains the project end to end: the problem, the mathematical model, the multi-objective algorithm, the code, the results, and the known weaknesses. Read it before the defense: the jury will probe the modelling choices in sections 2–4 and the weaknesses in section 7.

---

## 0. TL;DR

* **Problem.** Assign each engineer, each working day, to at most one (project, skill) pair so that projects are fully staffed. Some projects cannot be done at all (demand > capacity).
* **Model.** A Gurobi MIP with binary variables `x[s,j,q,t]` and a **time-indexed "started / finished" formulation** for project durations and lateness. This formulation has no big-M constraints.
* **Criteria.** f1 profit (max), f2 the largest number of projects any one employee works on (min), f3 the longest project duration (min). f4, the number of late projects, is also computed and can replace f2 or f3.
* **Algorithm.** An **exact ε-constraint method** for 3 objectives. It finds every non-dominated point, both supported and unsupported, as long as each MIP is solved to optimality. Points are then classified as supported or unsupported with a small LP.
* **Validation.** Every returned schedule is re-checked by an **independent checker** that does not trust the solver's variables. Max profit on toy = **65** and on medium = **413**.
* **Results.** See section 6 (filled in from `results/*/summary.json`).

---

## 1. The problem in plain words

The company has a horizon of `H` working days and a set of employees. Each employee has a set of skills and some leave days. Each project needs a given number of person-days per skill (for example 4 days of A and 2 days of B). It earns a gain if it is **fully** completed, and pays a penalty for every day it finishes after its due date.

A solution is a timetable: for every (employee, day), either nothing or one (project, skill).

Constraints from the subject:

1. **Qualification.** Someone can only work on skill `q` if they have it.
2. **Daily uniqueness.** At most one (project, skill) per employee per day.
3. **Leave.** No work on leave days.
4. **Coverage.** A project counts as done only if every skill requirement is fully covered.
5. **Execution uniqueness.** A project is done at most once.

---

## 2. Mathematical model

### 2.1 Parameters (data, fixed)

| Symbol | Meaning |
|---|---|
| `H`, `T = {1..H}` | horizon, working days |
| `S`, `Q`, `J` | staff, qualifications (skills), projects |
| `QS_s ⊆ Q` | skills of employee `s` |
| `V_s ⊆ T` | leave days of employee `s` |
| `n_{j,q}` | person-days of skill `q` needed by project `j` |
| `G_j`, `d_j`, `c_j` | gain, due date, daily penalty of project `j` |

### 2.2 Decision variables

| Variable | Domain | Meaning |
|---|---|---|
| `x[s,j,q,t]` | {0,1} | `s` works on skill `q` of project `j` on day `t` |
| `y[j]` | {0,1} | project `j` is completed |
| `z[s,j]` | {0,1} | `s` works on `j` at least once |
| `st[j,t]` | {0,1} | `j` has **started** on or before day `t` |
| `fi[j,t]` | {0,1} | `j` has **finished** on or before day `t` (`fi[j,0] = 0`) |
| `u[j]` | {0,1} | `j` is delivered late |
| `P`, `D` | integer | max #projects per employee, max project duration |

`x[s,j,q,t]` is **only created** when `q ∈ QS_s`, `n_{j,q} > 0` and `t ∉ V_s`. So the qualification and leave constraints (1) and (3) hold *by construction*, and the model is smaller.

### 2.3 Constraints

| # | Constraint | Meaning |
|---|---|---|
| C1 | `Σ_{j,q} x[s,j,q,t] ≤ 1` ∀ s,t | daily uniqueness |
| C2 | `Σ_{s,t} x[s,j,q,t] = n_{j,q} · y[j]` ∀ j,q | coverage **and** execution uniqueness: a project is staffed exactly once, or not at all (no wasted partial work) |
| C3 | `x[s,j,q,t] ≤ z[s,j]`, `z[s,j] ≤ Σ_{q,t} x[s,j,q,t]` | `z` = "s touches j" |
| C4 | `st[j,t-1] ≤ st[j,t]`, `fi[j,t-1] ≤ fi[j,t]`, `fi[j,t] ≤ st[j,t]` | started / finished are monotone step functions |
| C5 | `st[j,H] = fi[j,H] = y[j]` | a completed project starts and ends inside the horizon; an uncompleted one never starts |
| C6 | `x[s,j,q,t] ≤ st[j,t]`, `x[s,j,q,t] ≤ 1 − fi[j,t−1]` | work happens only while the project is open |
| C7 | `D ≥ Σ_t (st[j,t] − fi[j,t−1])` ∀ j | the right side is the number of days the project is open, i.e. its duration |
| C8 | `u[j] ≥ y[j] − fi[j,d_j]` ∀ j with `d_j < H` | late = completed but not finished by the due date |
| C9 | `P ≥ Σ_j z[s,j]` ∀ s | max number of projects per employee |

Lateness of `j` (an expression, not a variable): `late_j = Σ_{t > d_j} (y[j] − fi[j,t−1])`, which equals `max(0, end_j − d_j)`.

### 2.4 Objectives

* **f1 (max)** `Σ_j G_j·y[j] − c_j·late_j`: profit.
* **f2 (min)** `P`: the largest number of distinct projects assigned to one employee. This is our reading of "minimize the number of projects an employee works on". We take the **max over employees** (fairness, the worst-off employee) rather than the sum. The sum is a defensible alternative; say so if asked.
* **f3 (min)** `D`: the longest project duration (first to last worked day, inclusive). This is our reading of "minimize the actual project execution time".
* **f4 (min)** `Σ_j u[j]`: number of late projects. It is always computed and reported. You can optimise it instead of f2 or f3 with `--secondary`.

### 2.5 Why the "started / finished" formulation (a measured decision)

The first version used the textbook big-M encoding: `e_j ≥ t·x`, `b_j ≤ t + H(1 − x)`, `D ≥ e_j − b_j + 1 − (H+1)(1 − y_j)`. It is correct, but its LP relaxation is very weak. On **medium**, solving `lex(max f1, min f2, min f3)` **did not finish in 120 s** (status TIME_LIMIT).

The time-indexed version (section 2.3) has no big-M and solved the same problem to proven optimality in **40 s**. Both formulations are still in the code (`formulation="bigm"` / `"step"`) so the comparison can be reproduced.

| medium, lex(f1, f2, f3) | big-M | step |
|---|---|---|
| status | TIME_LIMIT at 120 s/stage | OPTIMAL |
| time | > 131 s | 40 s |

*(Measured once on an 8-thread laptop; single runs, so treat these as orders of magnitude, not precise speed-ups.)*

---

## 3. Multi-objective algorithm: exact ε-constraint

### 3.1 Why not the weighted sum

For combinatorial problems the non-dominated set contains **unsupported** points: points inside the convex hull, which no positive weighted sum ever selects (MOCO course). A weighted-sum method would miss them. On toy, 3 of the 10 non-dominated points are unsupported (see section 6).

### 3.2 The method

All three objectives are **integers**: the data has integer gains and penalties. This makes an exact ε-constraint possible with steps of 1:

```
for kA = UB_A down to 0:                       # bound on f2
    epsB = UB_B                                # bound on f3
    repeat:
        p = argmax lex( f1 ↑, f3 ↓, f2 ↓ )  s.t. f2 ≤ kA, f3 ≤ epsB
        store p ;  epsB = f3(p) − 1
    until epsB < 0
    level skipping: if all points of level kA have f2 ≤ m < kA, levels m..kA−1 would
    return exactly the same points → jump to kA = m − 1
remove dominated points
```

* **Why it is exact.** Inside a slice `f2 ≤ kA`, lowering `epsB` one step below the last f3 value visits every non-dominated (f1, f3) pair of the slice. Every non-dominated point with `f2 = kA` is non-dominated inside its slice, so it is found. The lexicographic tie-break (f3, then f2) guarantees each returned point is not weakly dominated.
* **One MIP per point, not three.** `lex(f1, f3, f2)` is solved as **one** MIP with weights `K₁·f1 − K₂·f3 − f2`, where `K₂ = UB_f2 + 1` and `K₁ = (UB_f3 + 1)·K₂`. With integer objectives this gives exactly the lexicographic optimum. On medium it was **2–3× faster** than three successive solves, and it proved optimality where the three-stage version timed out (63 s vs > 192 s on one ε-constrained case). Gurobi's own `setObjectiveN` crashed (access violation, Gurobi 13.0.3) when objectives were re-set on the same model, which is another reason for doing it by hand.
* **Exactness is conditional.** It holds only if every MIP is proven optimal. Each solve's status is recorded, and `summary.json → "exact"` is `false` as soon as one solve hit its time limit or the global budget ran out. In that case the output is an **approximation** of the front (every point is feasible, but some may be dominated by points that were not found).

### 3.3 Supported / unsupported classification

A point `p` is *supported* if some strictly positive weight vector λ makes `p` optimal among the front points: `λ·(p − q) ≤ 0` for all `q`. One LP per point, with objectives normalised to [0,1] (`src/algorithms/pareto.py`).

### 3.4 The empty schedule

"Do nothing" (profit 0, f2 = 0, f3 = 0) is always non-dominated because nothing beats 0 on f2 and f3. It is kept in the front for mathematical correctness. It is excluded when choosing the recommended compromise.

---

## 4. Decision support: which schedule should the CEO pick?

The algorithm gives a **set** of efficient schedules. As a neutral starting point we highlight the **compromise solution**: the point that minimises the weighted Tchebychev distance to the ideal point. Objectives are normalised by (nadir − ideal), with equal weights, and the empty schedule is excluded.

**Be honest about this in the defense:** equal weights are an arbitrary choice, not Margaux's preferences. We never elicited them. The proper next step would be preference elicitation: ask the CEO to compare a few schedules from the front and infer a value model, for example with **UTA** (see `../Exercises/solvers/uta_solver.py`, already written for the course). Then rank the front with that model. This step is **not implemented**.

---

## 5. Code map

| File | Role |
|---|---|
| `src/io_parser.py` | JSON → `ProblemInstance` (unchanged from the initial commit) |
| `src/models/gurobi_model.py` | MIP (`step` and `bigm` variants), ε-bounds, `solve_lexicographic` (3 stages), `solve_lex_weighted` (1 MIP), `evaluate_schedule` and `check_feasibility` (independent of the solver) |
| `src/algorithms/epsilon_constraint.py` | exact ε-constraint with level skipping, time budgets, exactness flag |
| `src/algorithms/pareto.py` | dominance, non-dominated filter, supported/unsupported LP |
| `src/visualization/plots.py` | objective space (3-D + three 2-D projections, supported vs unsupported, recommended point) and decision space (staff × day grid like the figure in the subject, leave hatched) |
| `src/generator.py` | instance generator: size, skills per employee, leave rate, **load ratio** (demand / capacity), due-date tightness; guarantees every required skill exists in the team |
| `run_pareto.py` | CLI: front + CSV/JSON + figures + summary for one instance |
| `benchmark.py` | scalability study on generated instances |
| `tests/` | 8 pytest tests: toy optimum = 65, the checker catches violations, mutual non-dominance of the toy front, generator round-trip |

**Validation principle.** Reported objective values are **recomputed from the schedule** (`evaluate_schedule`), not read from solver variables. Every schedule written to disk passes `check_feasibility` (`run_pareto.py` asserts it).

---

## 6. Results

**Status: pending.** Only the toy instance is final:

| Instance | Non-dominated points | Supported | MIP solves | Time | Exact |
|---|---|---|---|---|---|
| toy | 10 | 7 (3 unsupported) | 15 | < 4 s | yes |

Medium, large and the generated-instance benchmark are still to be run. A first medium run was
discarded: a time-limited solve returned the empty schedule and part of the front was skipped
(bug fixed since: warm start from the best known schedule + step-by-one after unproven solves).

---

## 7. Weaknesses and what a jury will attack (read this)

**Status: pending** — to be written from the medium / large / benchmark results.

---

## 8. What changed in the repository (this work session)

* `src/models/gurobi_model.py`: **rewritten**. The old model had the 4 constraint families but no ε-constraint support, no lateness flag, a weak big-M time model, and created variables on leave days.
* New: `src/algorithms/epsilon_constraint.py`, `src/algorithms/pareto.py`, `src/visualization/plots.py`, `run_pareto.py`, `benchmark.py`, `tests/test_models.py`, `docs/PROJECT_EXPLAINED.md`, `results/`.
* `src/generator.py`: **rewritten**. The old one could create projects needing a skill nobody had, and had no control over the workload.
* `requirements.txt`: trimmed to what is actually used (`gurobipy`, `matplotlib`, `pytest`). OR-Tools, pandas, seaborn, plotly and jupyter were listed but never used.
* `.gitignore`: `results/**/*.png` is now versioned (figures for the slides).
* Parent folder reorganised (all names in English): `Exercices/` → `Exercises/`, `Exercices_Solvers/` → `Exercises/solvers/` (comments translated to English), the 4 study guides → `Guides/`, and a top-level `README.md`. The duplicate `DMP/PROJECT_STEP_BY_STEP_ROADMAP.md` was removed; an identical copy is in `Guides/`.
* **Nothing was committed or pushed.** Review, then commit on your branch.
