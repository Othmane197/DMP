"""
Data models and JSON parser for CompuOpti problem instances.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Set
import json
from pathlib import Path


@dataclass
class StaffMember:
    name: str
    qualifications: Set[str]
    vacations: Set[int]  # 1-indexed days when employee is absent


@dataclass
class Job:
    name: str
    gain: float
    due_date: int
    daily_penalty: float
    working_days_per_qualification: Dict[str, int]

    @property
    def total_required_days(self) -> int:
        return sum(self.working_days_per_qualification.values())


@dataclass
class ProblemInstance:
    horizon: int
    qualifications: List[str]
    staff: List[StaffMember]
    jobs: List[Job]
    name: str = "instance"

    @classmethod
    def from_json(cls, file_path: str | Path) -> "ProblemInstance":
        path = Path(file_path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        staff_list = [
            StaffMember(
                name=s["name"],
                qualifications=set(s["qualifications"]),
                vacations=set(s.get("vacations", [])),
            )
            for s in data["staff"]
        ]

        jobs_list = [
            Job(
                name=j["name"],
                gain=float(j["gain"]),
                due_date=int(j["due_date"]),
                daily_penalty=float(j["daily_penalty"]),
                working_days_per_qualification=dict(j["working_days_per_qualification"]),
            )
            for j in data["jobs"]
        ]

        return cls(
            horizon=int(data["horizon"]),
            qualifications=list(data["qualifications"]),
            staff=staff_list,
            jobs=jobs_list,
            name=path.stem,
        )

    def summary(self) -> Dict[str, int | float]:
        return {
            "name": self.name,
            "horizon": self.horizon,
            "num_qualifications": len(self.qualifications),
            "num_staff": len(self.staff),
            "num_jobs": len(self.jobs),
            "total_workload_needed": sum(j.total_required_days for j in self.jobs),
            "total_potential_capacity": len(self.staff) * self.horizon - sum(len(s.vacations) for s in self.staff),
        }
