"""
Unit tests for data parsing and instance generation.
"""

from pathlib import Path
from src.io_parser import ProblemInstance
from src.generator import generate_instance


def test_load_toy_instance():
    data_path = Path(__file__).parent.parent / "data" / "toy_instance.json"
    assert data_path.exists(), "toy_instance.json must exist in data/"
    inst = ProblemInstance.from_json(data_path)
    assert inst.horizon == 5
    assert len(inst.staff) == 3
    assert len(inst.jobs) == 5
    assert "A" in inst.qualifications


def test_load_medium_and_large():
    base_data = Path(__file__).parent.parent / "data"
    for name, expected_horizon in [("medium_instance.json", 22), ("large_instance.json", 36)]:
        p = base_data / name
        assert p.exists()
        inst = ProblemInstance.from_json(p)
        assert inst.horizon == expected_horizon
        assert len(inst.jobs) > 0


def test_generator():
    data = generate_instance(horizon=10, num_staff=4, num_jobs=6, seed=123)
    assert data["horizon"] == 10
    assert len(data["staff"]) == 4
    assert len(data["jobs"]) == 6
