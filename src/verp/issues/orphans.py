"""Keeping issue ownership consistent with verp's projects.

Open issues must be owned by an existing project or be in the backlog.
`verp delete` releases a project's open issues explicitly; `reconcile_orphans`
repairs anything that slipped through (e.g. a project removed some other way).
Closed issues keep their project name as history.
"""

import sqlite3
import sys

from verp.db import project_names
from verp.issues import db
from verp.issues.model import Actor, Issue, IssueStatus


def open_issues(project: str) -> list[Issue]:
    """Open (or in-progress) issues owned by project."""
    with db.connect() as conn:
        return db.list_issues(
            conn,
            project=project,
            statuses=[IssueStatus.OPEN, IssueStatus.IN_PROGRESS],
        )


def release_project_issues(project: str, actor: Actor) -> list[int]:
    """Release all open issues of a deleted project to the backlog."""
    with db.connect() as conn:
        ids = db.open_issue_ids(conn, project)
        with conn:
            for issue_id in ids:
                db.release_issue(
                    conn, issue_id, actor, f"project '{project}' deleted"
                )
    return ids


def reconcile_orphans(conn: sqlite3.Connection, actor: Actor) -> list[int]:
    """Release open issues whose owning project no longer exists.

    Guarded against releasing everything when verp.db cannot be trusted: does
    nothing if it is missing or unreadable, or if it has no projects at all
    while issues still have owners (more likely a broken or wrong database
    than every project having been deleted).
    """
    owners = db.open_owner_projects(conn)
    if not owners:
        return []
    known = project_names()
    if known is None:
        return []
    if not known:
        print(
            "warning: verp.db has no projects but issues are still owned by "
            f"{', '.join(sorted(owners))}; not releasing them",
            file=sys.stderr,
        )
        return []
    released: list[int] = []
    with conn:
        for project in sorted(owners - known):
            for issue_id in db.open_issue_ids(conn, project):
                issue = db.get_issue(conn, issue_id)
                title = f" {issue.title}" if issue else ""
                db.release_issue(
                    conn,
                    issue_id,
                    actor,
                    f"project '{project}' no longer exists",
                )
                released.append(issue_id)
                print(
                    f"warning: released #{issue_id}{title} to the backlog "
                    f"(project '{project}' no longer exists)",
                    file=sys.stderr,
                )
    return released
