# DMP — CompuOpti Staff Scheduling & Project Allocation
> **MSc Artificial Intelligence — Decision Modeling** (CentraleSupélec & École Centrale Casablanca)

Multi-objective staffing of engineers on client projects for the company *CompuOpti*.
We compute the set of **non-dominated solutions** with an exact ε-constraint method on top of a
Gurobi MIP, and visualise them in objective space and decision space.

**Report (deliverable): [docs/report/CompuOpti_report.pdf](docs/report/CompuOpti_report.pdf)**, rebuilt from `results/` by `python docs/report/build_report.py`.
Longer plain-language explanation: [docs/PROJECT_EXPLAINED.md](docs/PROJECT_EXPLAINED.md)

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
├── certify_front.py           # per-point optimality gap (upper bounds) of an approximate front
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
├── docs/
│   ├── report/                # report template + builder -> CompuOpti_report.pdf
│   ├── slides/                # slide figures (profit for every pair of limits)
│   └── PROJECT_EXPLAINED.md
└── tests/                     # pytest
```

## Usage

```bash
pip install -r requirements.txt          # needs a Gurobi licence (free academic licence)
python -m pytest -q tests
python run_pareto.py data/toy_instance.json
python run_pareto.py data/medium_instance.json --time-per-solve 300
python run_pareto.py data/large_instance.json --time-per-solve 60 --total-time 5400 --step jump
python run_pareto.py data/large_instance.json --lex sequential --time-per-solve 60 --total-time 3600 --step jump
python run_pareto.py data/large_instance.json --merge results/large results/large_sequential
#   -> writes results/large_merged; we copied it to results/large_merged_before_certify (input of the certificate)
python certify_front.py data/large_instance.json results/large_merged_before_certify --time-per-solve 120 --level 1 --global-ub 817
python run_pareto.py data/large_instance.json --merge results/large results/large_sequential results/large_certify
python run_pareto.py data/toy_instance.json --secondary max_projects late_projects
python benchmark.py
python docs/slides/make_slide_figures.py
python docs/report/build_report.py
```

Interrupted runs resume from `results/<instance>/checkpoint.jsonl` (certificate: `results/large_certify/certificate.jsonl`) when the same command is re-run; finished solves are replayed, not recomputed.

## Results (measured, one laptop)

| Instance | Non-dominated points | Proven complete? | Max profit | Time |
|---|---:|---|---:|---:|
| toy | 10 | yes | 65 | < 4 s |
| medium | 46 | yes | 413 | 53 min |
| large | 73 (2 runs + certificate, merged) | **no**, approximation. Proven: the max profit, the best profit for 16 of 67 limit pairs, and the whole "1 project per engineer" slice. Certified gap elsewhere: median 6.0%, max 38% | 817 | 4.7 h of solves |

Benchmark on 12 generated instances: `results/benchmark.csv`. Details and limitations: [docs/PROJECT_EXPLAINED.md](docs/PROJECT_EXPLAINED.md).
