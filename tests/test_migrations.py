import shutil
import sqlite3
from pathlib import Path

import pytest

from verp.db import SCHEMA_VERSION, _VERSIONS_DIR, init_db


def _columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, str, bool]]:
    return [
        (row["name"], row["type"], bool(row["notnull"]))
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    ]


def _pk(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        if row["pk"]
    }


@pytest.fixture()
def data_dir(tmp_path: Path):  # type: ignore[no-untyped-def]
    yield tmp_path
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_migrate_from_zero(data_dir: Path) -> None:
    conn = init_db(data_dir)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

        # --- projects ---
        assert _columns(conn, "projects") == [
            ("name",    "TEXT",    False),
            ("path",    "TEXT",    True),
            ("branch",  "TEXT",    True),
            ("version", "INTEGER", True),
        ]
        assert _pk(conn, "projects") == {"name"}

        # --- project_repos ---
        assert _columns(conn, "project_repos") == [
            ("project_name", "TEXT", True),
            ("repo",         "TEXT", True),
        ]
        assert _pk(conn, "project_repos") == {"project_name", "repo"}

        # --- agents ---
        assert _columns(conn, "agents") == [
            ("session_id",    "TEXT",    False),
            ("directory",     "TEXT",    True),
            ("status",        "TEXT",    True),
            ("tool",          "TEXT",    False),
            ("updated_at",    "INTEGER", True),
            ("verp_pid",      "INTEGER", False),
            ("terminal_app",  "TEXT",    False),
            ("terminal_data", "TEXT",    False),
        ]
        assert _pk(conn, "agents") == {"session_id"}

        # --- config ---
        assert _columns(conn, "config") == [
            ("key",   "TEXT", False),
            ("value", "TEXT", True),
        ]
        assert _pk(conn, "config") == {"key"}

        # --- sessions ---
        assert _columns(conn, "sessions") == [
            ("verp_pid",   "INTEGER", False),
            ("session_id", "TEXT",    True),
        ]
        assert _pk(conn, "sessions") == {"verp_pid"}

        # --- deployed files match versioned sources ---
        latest = _VERSIONS_DIR / "latest"
        assert (data_dir / "track.sh").read_text() == (latest / "track.sh").read_text()
        assert (data_dir / "claude-settings.json").read_text() == (
            latest / "claude_settings.json"
        ).read_text()
        assert (data_dir / "kitty" / "verp.conf").read_text() == (
            _VERSIONS_DIR / "20" / "kitty" / "verp.conf"
        ).read_text()

        # --- no unexpected files ---
        actual = {p.relative_to(data_dir) for p in data_dir.rglob("*") if p.is_file()}
        expected = {
            Path("verp.db"),
            Path("track.sh"),
            Path("claude-settings.json"),
            Path("kitty/verp.conf"),
        }
        assert actual == expected

    finally:
        conn.close()
