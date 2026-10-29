"""
Synthetic Problem Instance Generator for CompuOpti.
Generates problem instances of configurable size and hardness.
"""

import json
import random
from pathlib import Path
from typing import Dict, Any, List


def generate_instance(
    horizon: int = 15,
    num_staff: int = 5,
    num_jobs: int = 8,
    qualifications: List[str] = None,
    vacation_rate: float = 0.1,
    skill_coverage: float = 0.4,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Generate a random instance with realistic distributions.
    """
    rng = random.Random(seed)
    if qualifications is None:
        qualifications = ["A", "B", "C", "D", "E"]

    # Generate staff
    staff = []
    first_names = [
        "Alice", "Bob", "Charlie", "David", "Eve", "Frank", "Grace",
        "Heidi", "Ivan", "Judy", "Mallory", "Niaj", "Olivia", "Peggy"
    ]
    for i in range(num_staff):
        name = first_names[i % len(first_names)] + (f"_{i}" if i >= len(first_names) else "")
        # Give each staff member at least 1 qualification
        num_skills = max(1, rng.binomialvariate(n=len(qualifications), p=skill_coverage) if hasattr(rng, 'binomialvariate') else round(len(qualifications) * skill_coverage))
        s_quals = sorted(rng.sample(qualifications, k=min(num_skills, len(qualifications))))

        # Random vacation days (1-indexed)
        vacations = sorted([
            day for day in range(1, horizon + 1)
            if rng.random() < vacation_rate
        ])

        staff.append({
            "name": name,
            "qualifications": s_quals,
            "vacations": vacations,
        })

    # Generate jobs
    jobs = []
    for j in range(1, num_jobs + 1):
        # Pick 1 to 3 required skills
        k_skills = rng.randint(1, min(3, len(qualifications)))
        job_quals = rng.sample(qualifications, k=k_skills)

        workload = {}
        for q in job_quals:
            workload[q] = rng.randint(1, max(2, horizon // 4))

        total_days = sum(workload.values())
        due_date = rng.randint(max(2, total_days // 2), horizon)
        gain = rng.randint(10, 50)
        daily_penalty = rng.randint(2, 5)

        jobs.append({
            "name": f"Job{j}",
            "gain": gain,
            "due_date": due_date,
            "daily_penalty": daily_penalty,
            "working_days_per_qualification": workload,
        })

    return {
        "horizon": horizon,
        "qualifications": qualifications,
        "staff": staff,
        "jobs": jobs,
    }


def save_instance_to_json(instance_data: Dict[str, Any], output_path: str | Path):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(instance_data, f, indent=4)
