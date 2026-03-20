import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

from verp.db import get_config_value, set_config_value
from verp.paths import CLAUDE_DIR, USER_CLAUDE_DIR

_CLAUDE_PACKAGE_DIR = Path(__file__).parent / "_claude"  # symlink → /_claude/

CLAUDE_DIR_VERSION = 1


def _warn_skill_conflicts() -> None:
    user_skills = USER_CLAUDE_DIR / "skills"
    managed_skills = _CLAUDE_PACKAGE_DIR / "skills"
    if not user_skills.is_dir() or not managed_skills.is_dir():
        return
    user_names = {p.name for p in user_skills.iterdir() if p.is_dir()}
    managed_names = {p.name for p in managed_skills.iterdir() if p.is_dir()}
    for name in sorted(user_names & managed_names):
        print(
            f"warning: skill '{name}' in {USER_CLAUDE_DIR}/skills/ conflicts"
            " with managed skill — user skill takes precedence"
        )


def _migrate_to_v1(conn: sqlite3.Connection) -> None:
    _warn_skill_conflicts()
    dest = CLAUDE_DIR / ".claude"
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(_CLAUDE_PACKAGE_DIR, dest)


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_to_v1,
}


def init_claude_dir(conn: sqlite3.Connection) -> None:
    current = get_config_value(conn, "claude_dir_version")
    if current >= CLAUDE_DIR_VERSION:
        return
    for version in range(current + 1, CLAUDE_DIR_VERSION + 1):
        with conn:
            _MIGRATIONS[version](conn)
            set_config_value(conn, "claude_dir_version", version)
