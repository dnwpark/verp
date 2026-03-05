import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

from verp.db import SCHEMA_VERSION, ProjectInfo, set_project_version

_VERSIONS_DIR = Path(__file__).parent / "_versions"


def setup_new(project_info: ProjectInfo) -> None:
    latest = _VERSIONS_DIR / "latest"
    claude_dir = Path(project_info.path) / ".claude"
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(latest / "claude_settings.json", claude_dir / "settings.json")

    dst = hooks_dir / "track.sh"
    shutil.copy2(latest / "track.sh", dst)
    dst.chmod(0o755)


def _migration_v3(project_info: ProjectInfo) -> None:
    v3 = _VERSIONS_DIR / "3"
    claude_dir = Path(project_info.path) / ".claude"
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(v3 / "claude_settings.json", claude_dir / "settings.json")

    dst = hooks_dir / "track.py"
    shutil.copy2(v3 / "track.py", dst)
    dst.chmod(0o755)

    conn = sqlite3.connect(claude_dir / "verp.db")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agents ("
        "    session_id TEXT PRIMARY KEY,"
        "    status     TEXT NOT NULL,"
        "    tool       TEXT,"
        "    updated_at INTEGER NOT NULL"
        ")"
    )
    conn.commit()
    conn.close()


def _migration_v4(project_info: ProjectInfo) -> None:
    v4 = _VERSIONS_DIR / "4"
    claude_dir = Path(project_info.path) / ".claude"
    hooks_dir = claude_dir / "hooks"

    shutil.copy2(v4 / "claude_settings.json", claude_dir / "settings.json")

    dst = hooks_dir / "track.sh"
    shutil.copy2(v4 / "track.sh", dst)
    dst.chmod(0o755)

    old_track = hooks_dir / "track.py"
    if old_track.exists():
        old_track.unlink()

    old_db = claude_dir / "verp.db"
    if old_db.exists():
        old_db.unlink()


def _migration_v5(project_info: ProjectInfo) -> None:
    v5 = _VERSIONS_DIR / "5"
    claude_dir = Path(project_info.path) / ".claude"
    hooks_dir = claude_dir / "hooks"

    shutil.copy2(v5 / "claude_settings.json", claude_dir / "settings.json")

    dst = hooks_dir / "track.sh"
    shutil.copy2(v5 / "track.sh", dst)
    dst.chmod(0o755)


def _migration_v6(project_info: ProjectInfo) -> None:
    v6 = _VERSIONS_DIR / "6"
    hooks_dir = Path(project_info.path) / ".claude" / "hooks"

    dst = hooks_dir / "track.sh"
    shutil.copy2(v6 / "track.sh", dst)
    dst.chmod(0o755)


def _migration_v7(project_info: ProjectInfo) -> None:
    v7 = _VERSIONS_DIR / "7"
    hooks_dir = Path(project_info.path) / ".claude" / "hooks"

    dst = hooks_dir / "track.sh"
    shutil.copy2(v7 / "track.sh", dst)
    dst.chmod(0o755)


def _migration_v8(project_info: ProjectInfo) -> None:
    v8 = _VERSIONS_DIR / "8"
    hooks_dir = Path(project_info.path) / ".claude" / "hooks"

    dst = hooks_dir / "track.sh"
    shutil.copy2(v8 / "track.sh", dst)
    dst.chmod(0o755)


def _migration_v9(project_info: ProjectInfo) -> None:
    v9 = _VERSIONS_DIR / "9"
    claude_dir = Path(project_info.path) / ".claude"
    hooks_dir = claude_dir / "hooks"

    shutil.copy2(v9 / "claude_settings.json", claude_dir / "settings.json")

    dst = hooks_dir / "track.sh"
    shutil.copy2(v9 / "track.sh", dst)
    dst.chmod(0o755)


def _migration_v10(project_info: ProjectInfo) -> None:
    v10 = _VERSIONS_DIR / "10"
    claude_dir = Path(project_info.path) / ".claude"
    hooks_dir = claude_dir / "hooks"

    shutil.copy2(v10 / "claude_settings.json", claude_dir / "settings.json")

    dst = hooks_dir / "track.sh"
    shutil.copy2(v10 / "track.sh", dst)
    dst.chmod(0o755)


_MIGRATIONS: dict[int, Callable[[ProjectInfo], None]] = {
    3: _migration_v3,
    4: _migration_v4,
    5: _migration_v5,
    6: _migration_v6,
    7: _migration_v7,
    8: _migration_v8,
    9: _migration_v9,
    10: _migration_v10,
}


def upgrade_project(project_info: ProjectInfo) -> None:
    for version in range(project_info.version + 1, SCHEMA_VERSION + 1):
        if version in _MIGRATIONS:
            _MIGRATIONS[version](project_info)
        set_project_version(project_info.name, version)
