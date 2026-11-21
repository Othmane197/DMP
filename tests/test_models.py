"""Tests for the MIP model, the feasibility checker and the Pareto machinery."""

from pathlib import Path

import pytest

from src.algorithms.epsilon_constraint import epsilon_constraint
from src.algorithms.pareto import classify_supported, dominates, non_dominated, to_min
from src.generator import generate_instance, save_instance_to_json
from src.io_parser import ProblemInstance
from src.models.gurobi_model import CompuOptiGurobiModel, check_feasibility

DATA = Path(__file__).parent.parent / "data"


@pytest.fixture(scope="module")
def toy():
    return ProblemInstance.from_json(DATA / "toy_instance.json")


def test_toy_max_profit_is_65(toy):
    sol = CompuOptiGurobiModel(toy).solve_single("profit")
    assert sol.status == "OPTIMAL"
    assert sol.objectives["profit"] == 65
    assert check_feasibility(toy, sol.schedule) == []


def test_checker_detects_violations(toy):
    # Liam is on leave day 1 and Emma has no skill A
    bad = [("Liam", "Job1", "A", 1), ("Emma", "Job1", "A", 2), ("Olivia", "Job1", "B", 1),
           ("Olivia", "Job1", "C", 1)]
    errs = check_feasibility(toy, bad)
    assert any("leave" in e for e in errs)
    assert any("lacks skill" in e for e in errs)
    assert any("two assignments" in e for e in errs)


def test_dominance():
    assert dominates((1, 1), (1, 2))
    assert not dominates((1, 2), (1, 2))
    assert not dominates((0, 3), (1, 2))


def test_toy_front_is_consistent(toy):
    res = epsilon_constraint(toy, log=lambda _: None)
    front, names = res["front"], res["objectives"]
    assert res["exact"]
    assert max(s.objectives["profit"] for s in front) == 65
    vecs = [to_min(s, names) for s in front]
    for a in vecs:  # mutual non-dominance
        assert not any(dominates(b, a) for b in vecs if b != a)
    for s in front:
        assert check_feasibility(toy, s.schedule) == []
    sup = classify_supported(front, names)
    assert len(sup) == len(front) and any(sup)
    assert non_dominated(front, names) == front


def test_generator_roundtrip(tmp_path):
    data = generate_instance(horizon=10, num_staff=4, num_jobs=6, seed=1)
    save_instance_to_json(data, tmp_path / "g.json")
    inst = ProblemInstance.from_json(tmp_path / "g.json")
    held = set().union(*(s.qualifications for s in inst.staff))
    for j in inst.jobs:  # every required skill is held by someone
        assert set(j.working_days_per_qualification) <= held
    sol = CompuOptiGurobiModel(inst, time_limit_sec=30).solve_single("profit")
    assert sol is not None and check_feasibility(inst, sol.schedule) == []


def test_checkpoint_resume_gives_same_front(toy, tmp_path):
    ck = tmp_path / "ck.jsonl"
    first = epsilon_constraint(toy, log=lambda _: None, checkpoint_path=ck)
    n_lines = len(ck.read_text().splitlines())
    second = epsilon_constraint(toy, log=lambda _: None, checkpoint_path=ck)
    names = first["objectives"]
    assert [to_min(s, names) for s in first["front"]] == [to_min(s, names) for s in second["front"]]
    assert second["exact"] == first["exact"]
    assert len(ck.read_text().splitlines()) == n_lines  # nothing was re-solved
