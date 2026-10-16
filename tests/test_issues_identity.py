"""Tests for issue caller identity (`resolve_caller`).

Identity comes from the process environment and cwd, so each case runs in a
subprocess with a controlled environment and an isolated `VERP_DATA_DIR`.
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = """
import json
from verp.issues.identity import resolve_caller
a = resolve_caller()
print(json.dumps({"kind": str(a.kind), "project": a.project}))
"""


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """A data dir with a minimal `projects` table containing `alpha`.

    Built by hand rather than via `init_db`, which has side effects outside
    the data dir (e.g. kitty config).
    """
    d = tmp_path / "data"
    d.mkdir()
    (tmp_path / "alpha" / "sub").mkdir(parents=True)
    conn = sqlite3.connect(d / "verp.db")
    conn.execute(
        "CREATE TABLE projects (name TEXT PRIMARY KEY, path TEXT, branch TEXT,"
        " version INTEGER)"
    )
    conn.execute(
        "INSERT INTO projects VALUES ('alpha', ?, 'me/alpha', 1)",
        (str((tmp_path / "alpha").resolve()),),
    )
    conn.commit()
    conn.close()
    return d


def resolve(data_dir: Path, cwd: Path, **env: str) -> dict[str, str | None]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("VERP_")}
    base["VERP_DATA_DIR"] = str(data_dir)
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        cwd=cwd,
        env={**base, **env},
        capture_output=True,
        text=True,
        check=True,
    )
    out: dict[str, str | None] = json.loads(result.stdout)
    return out


def test_cli_outside_project(data_dir: Path, tmp_path: Path) -> None:
    assert resolve(data_dir, tmp_path) == {"kind": "cli", "project": None}


@pytest.mark.parametrize("cwd", ["alpha", "alpha/sub"])
def test_cli_falls_back_to_cwd(
    data_dir: Path, tmp_path: Path, cwd: str
) -> None:
    assert resolve(data_dir, tmp_path / cwd) == {
        "kind": "cli",
        "project": "alpha",
    }


def test_agent_via_pid(data_dir: Path, tmp_path: Path) -> None:
    got = resolve(data_dir, tmp_path, VERP_PID="1", VERP_PROJECT="alpha")
    assert got == {"kind": "agent", "project": "alpha"}


def test_agent_via_socket(data_dir: Path, tmp_path: Path) -> None:
    got = resolve(
        data_dir, tmp_path, VERP_SOCKET="/tmp/verp-1.sock", VERP_PROJECT="alpha"
    )
    assert got == {"kind": "agent", "project": "alpha"}


def test_worker(data_dir: Path, tmp_path: Path) -> None:
    got = resolve(
        data_dir, tmp_path, VERP_PID="1", VERP_WORKER="1", VERP_PROJECT="alpha"
    )
    assert got == {"kind": "worker", "project": "alpha"}


def test_empty_verp_project_means_no_project(
    data_dir: Path, tmp_path: Path
) -> None:
    # Launched outside a project: VERP_PROJECT is set but empty, and must not
    # fall back to the cwd.
    got = resolve(data_dir, tmp_path / "alpha", VERP_PID="1", VERP_PROJECT="")
    assert got == {"kind": "agent", "project": None}


def test_verp_project_wins_over_cwd(data_dir: Path, tmp_path: Path) -> None:
    got = resolve(
        data_dir, tmp_path / "alpha", VERP_PID="1", VERP_PROJECT="beta"
    )
    assert got == {"kind": "agent", "project": "beta"}
