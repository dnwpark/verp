import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

from verp.db import get_config_value, set_config_value
from verp.paths import DATA_DIR, PI_DIR

_PI_PACKAGE_DIR = Path(__file__).parent / "_pi"  # contains verp.ts and skills/

PI_DIR_VERSION = 5


def _migrate_to_v1(conn: sqlite3.Connection) -> None:
    src = _PI_PACKAGE_DIR / "verp.ts"
    dest = DATA_DIR / "pi-extension.ts"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    # Redeploy updated verp.ts (now includes resources_discover).
    src = _PI_PACKAGE_DIR / "verp.ts"
    dest = DATA_DIR / "pi-extension.ts"
    shutil.copy2(src, dest)

    # Deploy skills directory.
    src_skills = _PI_PACKAGE_DIR / "skills"
    dest_skills = PI_DIR / "skills"
    PI_DIR.mkdir(parents=True, exist_ok=True)
    if dest_skills.exists():
        shutil.rmtree(dest_skills)
    shutil.copytree(src_skills, dest_skills)


def _migrate_to_v3(conn: sqlite3.Connection) -> None:
    # Redeploy updated verp.ts (Ctrl+\ handler: drop --focus flag).
    src = _PI_PACKAGE_DIR / "verp.ts"
    dest = DATA_DIR / "pi-extension.ts"
    shutil.copy2(src, dest)


def _migrate_to_v4(conn: sqlite3.Connection) -> None:
    # Redeploy updated verp.ts (Ctrl+\ handler: ensure agent registered).
    src = _PI_PACKAGE_DIR / "verp.ts"
    dest = DATA_DIR / "pi-extension.ts"
    shutil.copy2(src, dest)


def _migrate_to_v5(conn: sqlite3.Connection) -> None:
    # Redeploy updated verp.ts (Ctrl+\ handler: use hook_jump, not hook_agent_settled).
    src = _PI_PACKAGE_DIR / "verp.ts"
    dest = DATA_DIR / "pi-extension.ts"
    shutil.copy2(src, dest)


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_to_v1,
    2: _migrate_to_v2,
    3: _migrate_to_v3,
    4: _migrate_to_v4,
    5: _migrate_to_v5,
}


def init_pi_dir(conn: sqlite3.Connection) -> None:
    current = get_config_value(conn, "pi_dir_version")
    if current >= PI_DIR_VERSION:
        return
    for version in range(current + 1, PI_DIR_VERSION + 1):
        with conn:
            _MIGRATIONS[version](conn)
            set_config_value(conn, "pi_dir_version", version)
