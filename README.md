# DMP — CompuOpti Staff Scheduling & Project Allocation
> **MSc Artificial Intelligence — Decision Modeling**  
> *CentraleSupélec & École Centrale Casablanca*

---

##  Project Overview
This repository contains the multi-objective optimization models and resolution algorithms for **CompuOpti**, a decision-support consulting company.

The problem requires scheduling engineers with heterogeneous skills to client projects over a finite planning horizon $H$. Each project requires specific person-days for a set of qualifications, earns revenue upon complete execution, and incurs daily penalties if delivered past its contractual due date.

### Core Objectives
1. **Maximize Total Profit ($f_1$)**: Maximize revenue from completed projects minus late delivery penalties.
2. **Minimize Project Completion Time / Makespan ($f_2$)**: Minimize total duration or latest project completion day.
3. **Minimize Employee Context Switching ($f_3$)**: Reduce fragmentation by minimizing the distinct projects assigned to each engineer.
4. **Minimize Number of Late Projects ($f_4$)**: Minimize projects completed past their contractual deadline.

---

##  Repository Structure

```
DMP/
├── .gitignore               # Ignores licenses, logs, and environments
├── README.md                # Project documentation
├── requirements.txt         # Project dependencies
├── data/                    # Benchmark JSON instances
│   ├── toy_instance.json
│   ├── medium_instance.json
│   └── large_instance.json
├── src/
│   ├── __init__.py
│   ├── io_parser.py         # JSON schema loader & validator
│   ├── generator.py         # Synthetic instance generator
│   ├── models/              # Mathematical formulations (MIP & CP)
│   ├── algorithms/          # Multi-objective algorithms (ε-constraint, etc.)
│   └── visualization/       # Objective space & decision space plots
├── notebooks/               # Experimental analysis notebooks
└── tests/                   # Unit and integration tests
```

---

##  Installation & Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Othmane197/DMP.git
   cd DMP
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python -m venv .venv
   # On Windows:
   .venv\Scripts\activate
   # On Linux / macOS:
   source .venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Solvers**:
   - **Gurobi**: Free academic license available for students via Gurobi's academic portal.
   - **Google OR-Tools**: Fully open-source and included directly in `requirements.txt`.

---


