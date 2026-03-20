import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from verp.paths import DATA_DIR


@dataclass
class ProjectInfo:
    name: str
    path: str
    branch: str
    repos: list[str]
    version: int


@dataclass
class AgentInfo:
    session_id: str
    directory: str
    status: str
    tool: str | None
    updated_at: int
    verp_pid: int | None = None


DB_PATH = DATA_DIR / "verp.db"
_VERSIONS_DIR = Path(__file__).parent / "_versions"
SCHEMA_VERSION = 19


def _db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_to_v1(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            name TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            branch TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS project_repos (
            project_name TEXT NOT NULL
                REFERENCES projects(name) ON DELETE CASCADE,
            repo TEXT NOT NULL,
            PRIMARY KEY (project_name, repo)
        )
    """)


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    conn.execute(
        "ALTER TABLE projects ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
    )


def _migrate_to_v4(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            session_id   TEXT PRIMARY KEY,
            project_name TEXT NOT NULL REFERENCES projects(name) ON DELETE CASCADE,
            status       TEXT NOT NULL,
            tool         TEXT,
            updated_at   INTEGER NOT NULL
        )
    """)


def _migrate_to_v6(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE agents SET updated_at = updated_at * 1000")


def _migrate_to_v11(conn: sqlite3.Connection) -> None:
    v11 = _VERSIONS_DIR / "11"

    track = DATA_DIR / "track.sh"
    shutil.copy2(v11 / "track.sh", track)
    track.chmod(0o755)

    shutil.copy2(
        v11 / "claude_settings.json", DATA_DIR / "claude-settings.json"
    )


def _migrate_to_v14(conn: sqlite3.Connection) -> None:
    track = DATA_DIR / "track.sh"
    shutil.copy2(_VERSIONS_DIR / "14" / "track.sh", track)
    track.chmod(0o755)


def _migrate_to_v15(conn: sqlite3.Connection) -> None:
    track = DATA_DIR / "track.sh"
    shutil.copy2(_VERSIONS_DIR / "15" / "track.sh", track)
    track.chmod(0o755)


def _migrate_to_v13(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS agents")
    conn.execute("""
        CREATE TABLE agents (
            session_id  TEXT PRIMARY KEY,
            directory   TEXT NOT NULL,
            status      TEXT NOT NULL,
            tool        TEXT,
            updated_at  INTEGER NOT NULL
        )
    """)


def _migrate_to_v16(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE agents ADD COLUMN verp_pid INTEGER")


def _migrate_to_v17(conn: sqlite3.Connection) -> None:
    shutil.copy2(
        _VERSIONS_DIR / "17" / "claude_settings.json",
        DATA_DIR / "claude-settings.json",
    )


def _migrate_to_v18(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('claude_dir_version', '0')"
    )


def _migrate_to_v19(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            verp_pid   INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL
        )
    """)


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_to_v1,
    2: _migrate_to_v2,
    3: lambda conn: None,
    4: _migrate_to_v4,
    5: lambda conn: None,
    6: _migrate_to_v6,
    7: lambda conn: None,
    8: lambda conn: None,
    9: lambda conn: None,
    10: lambda conn: None,
    11: _migrate_to_v11,
    12: lambda conn: None,
    13: _migrate_to_v13,
    14: _migrate_to_v14,
    15: _migrate_to_v15,
    16: _migrate_to_v16,
    17: _migrate_to_v17,
    18: _migrate_to_v18,
    19: _migrate_to_v19,
}


def get_config_value(conn: sqlite3.Connection, key: str) -> int:
    row = conn.execute(
        "SELECT value FROM config WHERE key = ?", (key,)
    ).fetchone()
    return int(row["value"]) if row is not None else 0


def set_config_value(conn: sqlite3.Connection, key: str, version: int) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, str(version)),
        )


def init_db() -> sqlite3.Connection:
    conn = _db()
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current < SCHEMA_VERSION:
        for version in range(current + 1, SCHEMA_VERSION + 1):
            with conn:
                _MIGRATIONS[version](conn)
            conn.execute(f"PRAGMA user_version = {version}")
    return conn


def project_exists(name: str) -> bool:
    if not DB_PATH.exists():
        return False
    conn = _db()
    row = conn.execute(
        "SELECT 1 FROM projects WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    return row is not None


def get_project(name: str) -> ProjectInfo | None:
    if not DB_PATH.exists():
        return None
    conn = _db()
    row = conn.execute(
        "SELECT name, path, branch, version FROM projects WHERE name = ?",
        (name,),
    ).fetchone()
    if row is None:
        conn.close()
        return None
    repos = [
        str(r[0])
        for r in conn.execute(
            "SELECT repo FROM project_repos"
            " WHERE project_name = ? ORDER BY rowid",
            (name,),
        ).fetchall()
    ]
    conn.close()
    return ProjectInfo(
        name=str(row["name"]),
        path=str(row["path"]),
        branch=str(row["branch"]),
        repos=repos,
        version=int(row["version"]),
    )


def add_project(name: str, project_info: ProjectInfo) -> None:
    conn = _db()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO projects (name, path, branch, version) VALUES (?, ?, ?, ?)",
            (
                name,
                project_info.path,
                project_info.branch,
                project_info.version,
            ),
        )
        conn.execute(
            "DELETE FROM project_repos WHERE project_name = ?", (name,)
        )
        for repo in project_info.repos:
            conn.execute(
                "INSERT INTO project_repos (project_name, repo) VALUES (?, ?)",
                (name, repo),
            )
    conn.close()


def set_project_version(name: str, version: int) -> None:
    conn = _db()
    with conn:
        conn.execute(
            "UPDATE projects SET version = ? WHERE name = ?", (version, name)
        )
    conn.close()


def delete_project(name: str) -> None:
    conn = _db()
    with conn:
        conn.execute("DELETE FROM projects WHERE name = ?", (name,))
    conn.close()


def all_project_infos() -> list[ProjectInfo]:
    if not DB_PATH.exists():
        return []
    conn = _db()
    rows = conn.execute(
        "SELECT name, path, branch, version FROM projects ORDER BY name"
    ).fetchall()
    result = []
    for row in rows:
        repos = [
            str(r[0])
            for r in conn.execute(
                "SELECT repo FROM project_repos"
                " WHERE project_name = ? ORDER BY rowid",
                (row["name"],),
            ).fetchall()
        ]
        result.append(
            ProjectInfo(
                name=str(row["name"]),
                path=str(row["path"]),
                branch=str(row["branch"]),
                repos=repos,
                version=int(row["version"]),
            )
        )
    conn.close()
    return result


def get_project_branch(name: str) -> str | None:
    if not DB_PATH.exists():
        return None
    conn = _db()
    row = conn.execute(
        "SELECT branch FROM projects WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    return str(row["branch"]) if row is not None else None


def is_repo_in_project(project_name: str, repo: str) -> bool:
    if not DB_PATH.exists():
        return False
    conn = _db()
    row = conn.execute(
        "SELECT 1 FROM project_repos WHERE project_name = ? AND repo = ?",
        (project_name, repo),
    ).fetchone()
    conn.close()
    return row is not None


def projects_using_repo(repo: str) -> list[str]:
    if not DB_PATH.exists():
        return []
    conn = _db()
    rows = conn.execute(
        "SELECT project_name FROM project_repos WHERE repo = ? ORDER BY project_name",
        (repo,),
    ).fetchall()
    conn.close()
    return [str(row["project_name"]) for row in rows]


def add_repo_to_project(project_name: str, repo: str) -> None:
    conn = _db()
    with conn:
        conn.execute(
            "INSERT INTO project_repos (project_name, repo) VALUES (?, ?)",
            (project_name, repo),
        )
    conn.close()


def remove_repo_from_project(project_name: str, repo: str) -> None:
    conn = _db()
    with conn:
        conn.execute(
            "DELETE FROM project_repos WHERE project_name = ? AND repo = ?",
            (project_name, repo),
        )
    conn.close()


def is_project_dir(path: Path) -> bool:
    if not DB_PATH.exists():
        return False
    conn = _db()
    row = conn.execute(
        "SELECT 1 FROM projects WHERE path = ?", (str(path.resolve()),)
    ).fetchone()
    conn.close()
    return row is not None


def _verp_pid() -> int | None:
    import os

    sock = os.environ.get("VERP_SOCKET", "")
    if not sock:
        return None
    try:
        return int(sock.rsplit("-", 1)[-1].removesuffix(".sock"))
    except ValueError:
        return None


def set_agent_status(
    session_id: str, directory: str, status: str, timestamp: int
) -> None:
    """Create agent if needed and set status. Uses timestamp guard."""
    pid = _verp_pid()
    conn = _db()
    with conn:
        conn.execute(
            "INSERT INTO agents (session_id, directory, status, tool, updated_at, verp_pid)"
            " VALUES (?, ?, ?, NULL, ?, ?)"
            " ON CONFLICT(session_id) DO UPDATE SET"
            "     status = excluded.status,"
            "     updated_at = excluded.updated_at"
            " WHERE excluded.updated_at >= agents.updated_at",
            (session_id, directory, status, timestamp, pid),
        )
    conn.close()


def set_agent_status_by_session(session_id: str, status: str) -> None:
    """Directly set an agent's status by session ID (for manual status changes)."""
    conn = _db()
    with conn:
        conn.execute(
            "UPDATE agents SET status = ? WHERE session_id = ?",
            (status, session_id),
        )
    conn.close()


def has_agent_by_verp_pid(verp_pid: int) -> bool:
    if not DB_PATH.exists():
        return False
    conn = _db()
    row = conn.execute(
        "SELECT 1 FROM agents WHERE verp_pid = ?", (verp_pid,)
    ).fetchone()
    conn.close()
    return row is not None


def register_session(verp_pid: int, session_id: str) -> None:
    conn = _db()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (verp_pid, session_id) VALUES (?, ?)",
            (verp_pid, session_id),
        )
    conn.close()


def get_session_id(verp_pid: int) -> str | None:
    if not DB_PATH.exists():
        return None
    conn = _db()
    row = conn.execute(
        "SELECT session_id FROM sessions WHERE verp_pid = ?", (verp_pid,)
    ).fetchone()
    conn.close()
    return str(row["session_id"]) if row is not None else None


def remove_agents_by_pid(pid: int) -> None:
    conn = _db()
    with conn:
        conn.execute("DELETE FROM agents WHERE verp_pid = ?", (pid,))
        conn.execute("DELETE FROM sessions WHERE verp_pid = ?", (pid,))
    conn.close()


def set_agents_status_by_pid(pid: int, status: str, timestamp: int) -> None:
    conn = _db()
    with conn:
        conn.execute(
            "UPDATE agents SET status = ?, updated_at = ? WHERE verp_pid = ?",
            (status, timestamp, pid),
        )
    conn.close()


def set_agent_tool(session_id: str, tool: str) -> None:
    """Set tool on an existing agent."""
    conn = _db()
    with conn:
        conn.execute(
            "UPDATE agents SET tool = ? WHERE session_id = ?",
            (tool, session_id),
        )
    conn.close()


def reset_agent_tool(session_id: str) -> None:
    """Clear tool on an existing agent."""
    conn = _db()
    with conn:
        conn.execute(
            "UPDATE agents SET tool = NULL WHERE session_id = ?", (session_id,)
        )
    conn.close()


def remove_agent(session_id: str) -> None:
    conn = _db()
    with conn:
        conn.execute("DELETE FROM agents WHERE session_id = ?", (session_id,))
    conn.close()


def clear_agent_by_prefix(prefix: str) -> bool:
    conn = _db()
    row = conn.execute(
        "SELECT session_id FROM agents WHERE session_id LIKE ?", (prefix + "%",)
    ).fetchone()
    if row is None:
        conn.close()
        return False
    with conn:
        conn.execute(
            "DELETE FROM agents WHERE session_id = ?", (row["session_id"],)
        )
    conn.close()
    return True


def get_all_agents() -> list[AgentInfo]:
    if not DB_PATH.exists():
        return []
    conn = _db()
    rows = conn.execute(
        "SELECT session_id, directory, status, tool, updated_at, verp_pid"
        " FROM agents ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [
        AgentInfo(
            session_id=str(row["session_id"]),
            directory=str(row["directory"]),
            status=str(row["status"]),
            tool=str(row["tool"]) if row["tool"] is not None else None,
            updated_at=int(row["updated_at"]),
            verp_pid=(
                int(row["verp_pid"]) if row["verp_pid"] is not None else None
            ),
        )
        for row in rows
    ]


def get_agent_by_prefix(prefix: str) -> AgentInfo | None:
    if not DB_PATH.exists():
        return None
    conn = _db()
    row = conn.execute(
        "SELECT session_id, directory, status, tool, updated_at, verp_pid"
        " FROM agents WHERE session_id LIKE ?",
        (prefix + "%",),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return AgentInfo(
        session_id=str(row["session_id"]),
        directory=str(row["directory"]),
        status=str(row["status"]),
        tool=str(row["tool"]) if row["tool"] is not None else None,
        updated_at=int(row["updated_at"]),
        verp_pid=int(row["verp_pid"]) if row["verp_pid"] is not None else None,
    )
