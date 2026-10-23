"""SQLite layer for the issue tracker.

Lives in its own file (`DATA_DIR/issues.db`) with its own schema version, so
it is independent of `verp.db` and could be extracted later.
"""

import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from verp.issues.model import (
    Actor,
    ActorKind,
    Event,
    EventKind,
    Issue,
    IssueStatus,
)
from verp.paths import DATA_DIR
from verp.time import now_ms

SCHEMA_VERSION = 1


def _db_path(data_dir: Path) -> Path:
    return data_dir / "issues.db"


def _migrate_to_v1(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            project TEXT,
            outcome TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            closed_at INTEGER
        )
    """)
    conn.execute("CREATE INDEX issues_project ON issues (project)")
    conn.execute("""
        CREATE TABLE issue_deps (
            issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            depends_on INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            PRIMARY KEY (issue_id, depends_on),
            CHECK (issue_id != depends_on)
        )
    """)
    conn.execute("""
        CREATE TABLE issue_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            at INTEGER NOT NULL,
            actor_kind TEXT NOT NULL,
            actor_project TEXT,
            kind TEXT NOT NULL,
            message TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX issue_events_issue ON issue_events (issue_id, at)"
    )


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_to_v1,
}


def open_connection(path: Path | str) -> sqlite3.Connection:
    """Open and migrate an issues database. `path` may be `":memory:"`."""
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version in range(current + 1, SCHEMA_VERSION + 1):
        with conn:
            _MIGRATIONS[version](conn)
        conn.execute(f"PRAGMA user_version = {version}")
    return conn


# When set, `connect()` yields this connection instead of opening the file.
# Used by tests to share one in-memory database across commands.
_override: sqlite3.Connection | None = None


@contextmanager
def override_connection(
    conn: sqlite3.Connection,
) -> Generator[None, None, None]:
    global _override
    previous, _override = _override, conn
    try:
        yield
    finally:
        _override = previous


@contextmanager
def connect(
    data_dir: Path = DATA_DIR,
) -> Generator[sqlite3.Connection, None, None]:
    if _override is not None:
        yield _override
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = open_connection(_db_path(data_dir))
    try:
        yield conn
    finally:
        conn.close()


# --- reads -----------------------------------------------------------------


def _issue_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> Issue:
    deps = [
        int(r[0])
        for r in conn.execute(
            "SELECT depends_on FROM issue_deps WHERE issue_id = ?"
            " ORDER BY depends_on",
            (row["id"],),
        )
    ]
    return Issue(
        id=int(row["id"]),
        title=str(row["title"]),
        body=str(row["body"]),
        status=IssueStatus(str(row["status"])),
        project=row["project"],
        outcome=row["outcome"],
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
        closed_at=row["closed_at"],
        depends_on=deps,
    )


def get_issue(conn: sqlite3.Connection, issue_id: int) -> Issue | None:
    row = conn.execute(
        "SELECT * FROM issues WHERE id = ?", (issue_id,)
    ).fetchone()
    return _issue_from_row(conn, row) if row is not None else None


def list_issues(
    conn: sqlite3.Connection,
    *,
    project: str | None = None,
    backlog: bool = False,
    statuses: list[IssueStatus] | None = None,
) -> list[Issue]:
    where: list[str] = []
    params: list[object] = []
    if backlog:
        where.append("project IS NULL")
    elif project is not None:
        where.append("project = ?")
        params.append(project)
    if statuses:
        where.append(f"status IN ({','.join('?' * len(statuses))})")
        params.extend(str(s) for s in statuses)
    sql = "SELECT * FROM issues"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY project IS NOT NULL, project, id"
    return [_issue_from_row(conn, r) for r in conn.execute(sql, params)]


def get_events(conn: sqlite3.Connection, issue_id: int) -> list[Event]:
    return [
        Event(
            id=int(r["id"]),
            issue_id=int(r["issue_id"]),
            at=int(r["at"]),
            actor=Actor(
                kind=ActorKind(str(r["actor_kind"])),
                project=r["actor_project"],
            ),
            kind=EventKind(str(r["kind"])),
            message=r["message"],
        )
        for r in conn.execute(
            "SELECT * FROM issue_events WHERE issue_id = ? ORDER BY at, id",
            (issue_id,),
        )
    ]


def dependents(conn: sqlite3.Connection, issue_id: int) -> list[int]:
    """Issues that directly depend on issue_id."""
    return [
        int(r[0])
        for r in conn.execute(
            "SELECT issue_id FROM issue_deps WHERE depends_on = ?"
            " ORDER BY issue_id",
            (issue_id,),
        )
    ]


def depends_transitively(
    conn: sqlite3.Connection, issue_id: int, target: int
) -> bool:
    """True if issue_id depends on target, directly or indirectly."""
    row = conn.execute(
        """
        WITH RECURSIVE up(id) AS (
            SELECT depends_on FROM issue_deps WHERE issue_id = ?
            UNION
            SELECT d.depends_on FROM issue_deps d JOIN up ON d.issue_id = up.id
        )
        SELECT 1 FROM up WHERE id = ? LIMIT 1
        """,
        (issue_id, target),
    ).fetchone()
    return row is not None


def open_deps(conn: sqlite3.Connection, issue_id: int) -> list[int]:
    """Direct dependencies of issue_id that are not yet closed."""
    return [
        int(r[0])
        for r in conn.execute(
            "SELECT d.depends_on FROM issue_deps d"
            " JOIN issues i ON i.id = d.depends_on"
            " WHERE d.issue_id = ? AND i.status NOT IN (?, ?)"
            " ORDER BY d.depends_on",
            (issue_id, str(IssueStatus.DONE), str(IssueStatus.DROPPED)),
        )
    ]


_OPEN = (str(IssueStatus.OPEN), str(IssueStatus.IN_PROGRESS))


def open_issue_ids(conn: sqlite3.Connection, project: str) -> list[int]:
    """Open (or in-progress) issues owned by project."""
    return [
        int(r[0])
        for r in conn.execute(
            "SELECT id FROM issues WHERE project = ? AND status IN (?, ?)"
            " ORDER BY id",
            (project, *_OPEN),
        )
    ]


def open_owner_projects(conn: sqlite3.Connection) -> set[str]:
    """Projects that own at least one open (or in-progress) issue."""
    return {
        str(r[0])
        for r in conn.execute(
            "SELECT DISTINCT project FROM issues"
            " WHERE project IS NOT NULL AND status IN (?, ?)",
            _OPEN,
        )
    }


# --- writes ----------------------------------------------------------------
# Callers are responsible for permission checks and for wrapping in a
# transaction (`with conn:`).


def add_event(
    conn: sqlite3.Connection,
    issue_id: int,
    actor: Actor,
    kind: EventKind,
    message: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO issue_events"
        " (issue_id, at, actor_kind, actor_project, kind, message)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            issue_id,
            now_ms(),
            str(actor.kind),
            actor.project,
            str(kind),
            message,
        ),
    )


def add_dep(conn: sqlite3.Connection, issue_id: int, depends_on: int) -> None:
    conn.execute(
        "INSERT INTO issue_deps (issue_id, depends_on) VALUES (?, ?)",
        (issue_id, depends_on),
    )
    touch(conn, issue_id)


def remove_dep(
    conn: sqlite3.Connection, issue_id: int, depends_on: int
) -> bool:
    cur = conn.execute(
        "DELETE FROM issue_deps WHERE issue_id = ? AND depends_on = ?",
        (issue_id, depends_on),
    )
    if cur.rowcount:
        touch(conn, issue_id)
    return cur.rowcount > 0


def create_issue(
    conn: sqlite3.Connection,
    title: str,
    body: str,
    project: str | None,
    actor: Actor,
) -> int:
    now = now_ms()
    cur = conn.execute(
        "INSERT INTO issues (title, body, status, project,"
        " created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            title,
            body,
            str(IssueStatus.OPEN),
            project,
            now,
            now,
        ),
    )
    issue_id = int(cur.lastrowid or 0)
    add_event(
        conn, issue_id, actor, EventKind.CREATED, f"in {project or 'backlog'}"
    )
    return issue_id


def update_content(
    conn: sqlite3.Connection,
    issue_id: int,
    *,
    title: str | None = None,
    body: str | None = None,
) -> None:
    if title is not None:
        conn.execute(
            "UPDATE issues SET title = ? WHERE id = ?", (title, issue_id)
        )
    if body is not None:
        conn.execute(
            "UPDATE issues SET body = ? WHERE id = ?", (body, issue_id)
        )
    touch(conn, issue_id)


def set_status(
    conn: sqlite3.Connection,
    issue_id: int,
    status: IssueStatus,
    outcome: str | None = None,
) -> None:
    closed_at = now_ms() if status.closed else None
    conn.execute(
        "UPDATE issues SET status = ?, outcome = ?, closed_at = ? WHERE id = ?",
        (str(status), outcome, closed_at, issue_id),
    )
    touch(conn, issue_id)


def set_project(
    conn: sqlite3.Connection, issue_id: int, project: str | None
) -> None:
    conn.execute(
        "UPDATE issues SET project = ? WHERE id = ?",
        (project, issue_id),
    )
    touch(conn, issue_id)


def release_issue(
    conn: sqlite3.Connection, issue_id: int, actor: Actor, reason: str
) -> None:
    """Return an issue to the backlog; in-progress work goes back to open."""
    issue = get_issue(conn, issue_id)
    if issue is None:
        return
    set_project(conn, issue_id, None)
    if issue.status == IssueStatus.IN_PROGRESS:
        set_status(conn, issue_id, IssueStatus.OPEN)
    add_event(conn, issue_id, actor, EventKind.RELEASED, reason)


def touch(conn: sqlite3.Connection, issue_id: int) -> None:
    conn.execute(
        "UPDATE issues SET updated_at = ? WHERE id = ?", (now_ms(), issue_id)
    )
