"""
Synthetic instance generator for CompuOpti.

The provided instances share a recognisable structure (gains 10-80, penalty 3/day,
1-3 skills per project, 2-10 person-days per skill, ~10% leave). The generator
reproduces that structure and exposes the parameters that drive difficulty:

    horizon, num_staff, num_jobs, num_qualifications
    skills_per_staff   (min, max) number of skills per employee
    vacation_rate      probability that a given day is a leave day
    load_ratio         total demanded person-days / total available person-days
                       (>1 means not every project can be staffed, as in the subject)
    due_tightness      due dates drawn in [workload-based lower bound, tightness * H]

Every skill required by a project is held by at least one employee, so no project
is trivially impossible.
"""

from __future__ import annotations

import json
import random
import string
from pathlib import Path
from typing import Any, Dict, Tuple

FIRST_NAMES = ["Olivia", "Liam", "Emma", "Noah", "Amelia", "Oliver", "Ava", "Elijah",
               "Sophia", "Lucas", "Mia", "Levi", "Isla", "Mateo", "Zoe", "Ezra"]


def generate_instance(
    horizon: int = 20,
    num_staff: int = 5,
    num_jobs: int = 10,
    num_qualifications: int = 6,
    skills_per_staff: Tuple[int, int] = (2, 4),
    vacation_rate: float = 0.1,
    load_ratio: float = 1.3,
    due_tightness: float = 0.8,
    seed: int = 42,
) -> Dict[str, Any]:
    rng = random.Random(seed)
    quals = list(string.ascii_uppercase[:num_qualifications])

    # --- staff: guarantee every skill is covered by someone
    staff = []
    for i in range(num_staff):
        k = rng.randint(*skills_per_staff)
        staff.append(set(rng.sample(quals, min(k, len(quals)))))
    for q in quals:
        if not any(q in s for s in staff):
            rng.choice(staff).add(q)
    staff_json = []
    for i, s in enumerate(staff):
        name = FIRST_NAMES[i % len(FIRST_NAMES)] + ("" if i < len(FIRST_NAMES) else str(i))
        vac = [t for t in range(1, horizon + 1) if rng.random() < vacation_rate]
        staff_json.append({"name": name, "qualifications": sorted(s), "vacations": vac})

    capacity = num_staff * horizon - sum(len(s["vacations"]) for s in staff_json)
    target_demand = load_ratio * capacity

    # --- jobs: draw shapes, then scale person-days to hit the target load
    shapes = []
    for _ in range(num_jobs):
        k = rng.choice([1, 2, 2, 3, 3])
        qs = rng.sample(quals, min(k, len(quals)))
        shapes.append({q: rng.uniform(1, 3) for q in qs})
    raw_total = sum(sum(s.values()) for s in shapes)
    scale = target_demand / raw_total

    jobs = []
    for idx, shape in enumerate(shapes, start=1):
        wd = {q: max(1, round(v * scale)) for q, v in shape.items()}
        total = sum(wd.values())
        # a project needs at least max_q(wd[q]) days even with many people -> lower bound
        lb = min(horizon, max(max(wd.values()), total // max(1, len(wd))))
        due = rng.randint(lb, max(lb, round(due_tightness * horizon)))
        gain = int(round(total * rng.uniform(2.0, 5.0) / 5.0) * 5) or 5
        jobs.append({
            "name": f"Job{idx}",
            "gain": gain,
            "due_date": due,
            "daily_penalty": 3,
            "working_days_per_qualification": wd,
        })

    return {"horizon": horizon, "qualifications": quals, "staff": staff_json, "jobs": jobs}


def save_instance_to_json(instance_data: Dict[str, Any], output_path: str | Path):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(instance_data, f, indent=4)
