import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from verp.db import ProjectInfo


@dataclass
class AgentInfo:
    session_id: str
    project: str
    status: str
    tool: str | None
    updated_at: int


def _agent_db(project_dir: Path) -> sqlite3.Connection | None:
    db_path = project_dir / ".claude" / "verp.db"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def list_agents(project_infos: list[ProjectInfo]) -> list[AgentInfo]:
    agents: list[AgentInfo] = []
    for pi in project_infos:
        name, project_dir = pi.name, Path(pi.path)
        conn = _agent_db(project_dir)
        if conn is None:
            continue
        rows = conn.execute(
            "SELECT session_id, status, tool, updated_at FROM agents ORDER BY updated_at DESC"
        ).fetchall()
        conn.close()
        for row in rows:
            agents.append(
                AgentInfo(
                    session_id=str(row["session_id"]),
                    project=name,
                    status=str(row["status"]),
                    tool=str(row["tool"]) if row["tool"] is not None else None,
                    updated_at=int(row["updated_at"]),
                )
            )
    return agents


def clear_agent(
    session_id_prefix: str, project_infos: list[ProjectInfo]
) -> bool:
    for pi in project_infos:
        project_dir = Path(pi.path)
        conn = _agent_db(project_dir)
        if conn is None:
            continue
        row = conn.execute(
            "SELECT session_id FROM agents WHERE session_id LIKE ?",
            (session_id_prefix + "%",),
        ).fetchone()
        if row is not None:
            with conn:
                conn.execute(
                    "DELETE FROM agents WHERE session_id = ?",
                    (row["session_id"],),
                )
            conn.close()
            return True
        conn.close()
    return False


def format_age(updated_at: int) -> str:
    secs = int(time.time()) - updated_at
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"
