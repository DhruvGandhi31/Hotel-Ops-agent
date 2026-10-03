import json
from pathlib import Path

import pytest

from hotel_datagen.build import build_dataset
from hotel_datagen.output import write_dataset

GATE_N, GATE_SEED = 200, 42


@pytest.fixture(scope="session")
def gate_out(tmp_path_factory) -> Path:
    """The P1 gate dataset (n=200, seed=42), written to disk exactly as generate.py does."""
    out = tmp_path_factory.mktemp("gate")
    write_dataset(build_dataset(GATE_N, GATE_SEED), out)
    return out


@pytest.fixture(scope="session")
def ground_truths(gate_out) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((gate_out / "ground_truth").glob("*.json"))]


@pytest.fixture(scope="session")
def master(gate_out) -> dict:
    return json.loads((gate_out / "master_data.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def manifest(gate_out) -> dict:
    return json.loads((gate_out / "manifest.json").read_text(encoding="utf-8"))
