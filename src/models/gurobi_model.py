"""
Gurobi MIP Formulation for CompuOpti Staff Scheduling & Project Allocation.
"""

from typing import Dict, Any, Optional
import gurobipy as gp
from gurobipy import GRB
from src.io_parser import ProblemInstance


class CompuOptiGurobiModel:
    def __init__(self, instance: ProblemInstance, time_limit_sec: float = 60.0):
        self.inst = instance
        self.time_limit = time_limit_sec
        self.model = gp.Model(f"CompuOpti_{instance.name}")
        self.model.Params.TimeLimit = self.time_limit
        self.model.Params.OutputFlag = 1  # Print solver logs

        # Sets & Indices
        self.S = [s.name for s in instance.staff]
        self.J = [j.name for j in instance.jobs]
        self.Q = instance.qualifications
        self.T = list(range(1, instance.horizon + 1))

        # Lookups
        self.staff_dict = {s.name: s for s in instance.staff}
        self.jobs_dict = {j.name: j for j in instance.jobs}

        # Model containers
        self.X: Dict[tuple, gp.Var] = {}
        self.Y: Dict[str, gp.Var] = {}
        self.C: Dict[str, gp.Var] = {}
        self.L: Dict[str, gp.Var] = {}
        self.Z: Dict[tuple, gp.Var] = {}

        self._build_variables()
        self._build_constraints()

    def _build_variables(self):
        """Create decision variables."""
        # X[s, j, q, t] = 1 if staff s works on job j with skill q on day t
        for s_obj in self.inst.staff:
            s = s_obj.name
            for j_obj in self.inst.jobs:
                j = j_obj.name
                for q, req in j_obj.working_days_per_qualification.items():
                    # Qualification constraint built into variable creation
                    if q in s_obj.qualifications:
                        for t in self.T:
                            self.X[s, j, q, t] = self.model.addVar(
                                vtype=GRB.BINARY,
                                name=f"X_{s}_{j}_{q}_{t}"
                            )

        # Y[j] = 1 if job j is completed within horizon
        for j in self.J:
            self.Y[j] = self.model.addVar(vtype=GRB.BINARY, name=f"Y_{j}")

        # C[j] = completion day of job j (0 if uncompleted)
        for j in self.J:
            self.C[j] = self.model.addVar(
                vtype=GRB.INTEGER,
                lb=0,
                ub=self.inst.horizon,
                name=f"C_{j}"
            )

        # L[j] = delay past due_date (tardiness)
        for j in self.J:
            self.L[j] = self.model.addVar(
                vtype=GRB.INTEGER,
                lb=0,
                ub=self.inst.horizon,
                name=f"L_{j}"
            )

        # Z[s, j] = 1 if staff s ever works on job j (for context-switching)
        for s in self.S:
            for j in self.J:
                self.Z[s, j] = self.model.addVar(vtype=GRB.BINARY, name=f"Z_{s}_{j}")

    def _build_constraints(self):
        """Apply operational constraints."""
        H = self.inst.horizon

        # 1. Daily Uniqueness & Vacation: at most 1 task per day; 0 on vacation
        for s_obj in self.inst.staff:
            s = s_obj.name
            for t in self.T:
                relevant_vars = [
                    self.X[s, j, q, t]
                    for j in self.J
                    for q in self.jobs_dict[j].working_days_per_qualification
                    if (s, j, q, t) in self.X
                ]
                max_capacity = 0 if t in s_obj.vacations else 1
                self.model.addConstr(
                    gp.quicksum(relevant_vars) <= max_capacity,
                    name=f"DailyUniqueness_{s}_{t}"
                )

        # 2. Workload Requirement per Qualification for completed jobs
        for j_obj in self.inst.jobs:
            j = j_obj.name
            for q, req in j_obj.working_days_per_qualification.items():
                relevant_vars = [
                    self.X[s, j, q, t]
                    for s in self.S
                    for t in self.T
                    if (s, j, q, t) in self.X
                ]
                # Exact equality: satisfy requirement if Y[j]=1, else 0
                self.model.addConstr(
                    gp.quicksum(relevant_vars) == req * self.Y[j],
                    name=f"Workload_{j}_{q}"
                )

        # 3. Completion Day definition: C[j] >= t if active on day t
        for (s, j, q, t), var in self.X.items():
            self.model.addConstr(
                self.C[j] >= t * var,
                name=f"CompletionDay_lower_{s}_{j}_{q}_{t}"
            )

        # C[j] <= H * Y[j] (0 if uncompleted)
        for j in self.J:
            self.model.addConstr(
                self.C[j] <= H * self.Y[j],
                name=f"CompletionDay_upper_{j}"
            )

        # 4. Tardiness: L[j] >= C[j] - due_date
        for j_obj in self.inst.jobs:
            j = j_obj.name
            self.model.addConstr(
                self.L[j] >= self.C[j] - j_obj.due_date,
                name=f"Tardiness_{j}"
            )

        # 5. Context Switching: X[s, j, q, t] <= Z[s, j]
        for (s, j, q, t), var in self.X.items():
            self.model.addConstr(
                var <= self.Z[s, j],
                name=f"ContextSwitch_{s}_{j}_{q}_{t}"
            )

    def set_single_objective(self, objective_name: str = "profit", sense: int = GRB.MAXIMIZE):
        """Set a single objective for optimization."""
        if objective_name == "profit":
            # Maximize Total Gain - Penalties
            profit_expr = gp.quicksum(
                self.jobs_dict[j].gain * self.Y[j] - self.jobs_dict[j].daily_penalty * self.L[j]
                for j in self.J
            )
            self.model.setObjective(profit_expr, sense)
        elif objective_name == "makespan":
            # Minimize total completion day sum
            self.model.setObjective(gp.quicksum(self.C[j] for j in self.J), GRB.MINIMIZE)
        elif objective_name == "context_switch":
            # Minimize employee fragmentation
            self.model.setObjective(gp.quicksum(self.Z[s, j] for s in self.S for j in self.J), GRB.MINIMIZE)

    def optimize(self) -> Optional[Dict[str, Any]]:
        """Solve and return solution dictionary."""
        self.model.optimize()

        if self.model.Status not in [GRB.OPTIMAL, GRB.TIME_LIMIT] or self.model.SolCount == 0:
            return None

        profit = sum(
            self.jobs_dict[j].gain * self.Y[j].X - self.jobs_dict[j].daily_penalty * self.L[j].X
            for j in self.J
        )
        total_completion = sum(self.C[j].X for j in self.J)
        context_switches = sum(self.Z[s, j].X for s in self.S for j in self.J)

        # Extract schedule: staff -> list of (day, job, skill)
        schedule = []
        for (s, j, q, t), var in self.X.items():
            if var.X > 0.5:
                schedule.append({
                    "staff": s,
                    "day": t,
                    "job": j,
                    "qualification": q,
                })

        return {
            "status": "OPTIMAL" if self.model.Status == GRB.OPTIMAL else "FEASIBLE",
            "profit": profit,
            "total_completion_time": total_completion,
            "total_context_switches": context_switches,
            "completed_jobs": [j for j in self.J if self.Y[j].X > 0.5],
            "schedule": schedule,
        }

