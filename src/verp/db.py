import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


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
    project: str
    status: str
    tool: str | None
    updated_at: int


DATA_DIR = Path.home() / ".local" / "share" / "verp"
DB_PATH = DATA_DIR / "verp.db"

SCHEMA_VERSION = 10


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
}


def init_db() -> None:
    conn = _db()
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        conn.close()
        return
    for version in range(current + 1, SCHEMA_VERSION + 1):
        with conn:
            _MIGRATIONS[version](conn)
        conn.execute(f"PRAGMA user_version = {version}")
    conn.close()


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


def add_repo_to_project(project_name: str, repo: str) -> None:
    conn = _db()
    with conn:
        conn.execute(
            "INSERT INTO project_repos (project_name, repo) VALUES (?, ?)",
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


def set_agent_status(
    session_id: str, project_name: str, status: str, timestamp: int
) -> None:
    """Create agent if needed and set status. Uses timestamp guard."""
    conn = _db()
    with conn:
        conn.execute(
            "INSERT INTO agents (session_id, project_name, status, tool, updated_at)"
            " VALUES (?, ?, ?, NULL, ?)"
            " ON CONFLICT(session_id) DO UPDATE SET"
            "     status = excluded.status,"
            "     updated_at = excluded.updated_at"
            " WHERE excluded.updated_at >= agents.updated_at",
            (session_id, project_name, status, timestamp),
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
        "SELECT session_id, project_name, status, tool, updated_at"
        " FROM agents WHERE project_name IS NOT NULL ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [
        AgentInfo(
            session_id=str(row["session_id"]),
            project=str(row["project_name"]),
            status=str(row["status"]),
            tool=str(row["tool"]) if row["tool"] is not None else None,
            updated_at=int(row["updated_at"]),
        )
        for row in rows
    ]
