"""Issue 1: .env must actually reach Settings.

Settings reads the environment at import time, so load_dotenv() has to run
before the escalation package is imported. That ordering can only be tested
honestly in a fresh interpreter, hence the subprocess.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = str(Path(__file__).resolve().parent.parent)

PROBE = (
    "import {module}; "
    "from escalation.config import settings; "
    "print(settings.classifier_model)"
)


def run_probe(module: str, cwd: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k != "CLASSIFIER_MODEL"}
    env["PYTHONPATH"] = REPO_ROOT
    result = subprocess.run(
        [sys.executable, "-c", PROBE.format(module=module)],
        cwd=cwd, capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().splitlines()[-1]


@pytest.fixture
def dotenv_dir(tmp_path):
    (tmp_path / ".env").write_text("CLASSIFIER_MODEL=model-from-dotenv\n")
    return tmp_path


def test_api_loads_dotenv_before_settings_are_built(dotenv_dir):
    assert run_probe("escalation.api", dotenv_dir) == "model-from-dotenv"


def test_demo_loads_dotenv_before_settings_are_built(dotenv_dir):
    assert run_probe("escalation.demo", dotenv_dir) == "model-from-dotenv"


def test_a_real_environment_variable_still_wins_over_dotenv(dotenv_dir):
    """load_dotenv must not clobber credentials already exported in the shell."""
    env = {**os.environ, "PYTHONPATH": REPO_ROOT, "CLASSIFIER_MODEL": "model-from-shell"}
    result = subprocess.run(
        [sys.executable, "-c", PROBE.format(module="escalation.api")],
        cwd=dotenv_dir, capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "model-from-shell"
