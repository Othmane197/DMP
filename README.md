# DMP — CompuOpti Staff Scheduling & Project Allocation
> **MSc Artificial Intelligence — Decision Modeling** (CentraleSupélec & École Centrale Casablanca)

Multi-objective staffing of engineers on client projects for the company *CompuOpti*.
We compute the set of **non-dominated solutions** with an exact ε-constraint method on top of a
Gurobi MIP, and visualise them in objective space and decision space.

Full explanation (model, algorithm, results, limitations): **[docs/PROJECT_EXPLAINED.md](docs/PROJECT_EXPLAINED.md)**

## Objectives

| | Criterion | Sense |
|---|---|---|
| f1 | Profit = gains of completed projects − daily lateness penalties | max |
| f2 | Largest number of distinct projects assigned to one employee | min |
| f3 | Longest project duration (first to last worked day) | min |
| f4 | Number of late projects | min (reported; can replace f2 or f3) |

## Repository structure

```
DMP/
├── run_pareto.py              # compute + plot the non-dominated set of one instance
├── benchmark.py               # scalability study on generated instances
├── data/                      # toy / medium / large instances (+ generated/)
├── src/
│   ├── io_parser.py           # JSON -> typed ProblemInstance
│   ├── generator.py           # synthetic instance generator
│   ├── models/gurobi_model.py # MIP model, lexicographic solves, independent feasibility checker
│   ├── algorithms/
│   │   ├── epsilon_constraint.py  # exact 3-objective ε-constraint
│   │   └── pareto.py              # dominance filter, supported / unsupported test
│   └── visualization/plots.py # objective-space and decision-space figures
├── results/<instance>/        # CSV/JSON fronts, figures, summary.json
├── docs/PROJECT_EXPLAINED.md
└── tests/                     # pytest
```

## Usage

```bash
pip install -r requirements.txt          # needs a Gurobi licence (free academic licence)
python -m pytest -q tests
python run_pareto.py data/toy_instance.json
python run_pareto.py data/medium_instance.json --time-per-solve 120
python run_pareto.py data/large_instance.json --time-per-solve 60 --total-time 3600
python run_pareto.py data/toy_instance.json --secondary max_projects late_projects
python benchmark.py
```
