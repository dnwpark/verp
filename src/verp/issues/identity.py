"""Caller identity and ownership rules.

The only place the issue tracker depends on verp's project concept.

An issue's owner is a verp project name. The caller is an `Actor`:
- `worker` if `VERP_WORKER` is set (by the verp worker),
- `agent` if `VERP_PID` / `VERP_SOCKET` is set (by `verp claude` / `verp pi`),
- `cli` otherwise: the human operator, who may bypass ownership.

Agents and workers are identified by `VERP_PROJECT`, set at launch, so they
keep their identity regardless of `cd`. The CLI falls back to the cwd.
"""

import os
from pathlib import Path

from verp.db import get_project, is_project_dir
from verp.issues.model import Actor, ActorKind, Issue

ENV_PROJECT = "VERP_PROJECT"
ENV_WORKER = "VERP_WORKER"


def _cwd_project() -> str | None:
    try:
        cwd = Path.cwd().resolve()
    except FileNotFoundError:
        return None
    for p in [cwd, *cwd.parents]:
        if is_project_dir(p):
            return p.name
    return None


def resolve_caller() -> Actor:
    if os.environ.get(ENV_WORKER):
        kind = ActorKind.WORKER
    elif os.environ.get("VERP_PID") or os.environ.get("VERP_SOCKET"):
        kind = ActorKind.AGENT
    else:
        kind = ActorKind.CLI
    if ENV_PROJECT in os.environ:
        project = os.environ[ENV_PROJECT] or None
    else:
        project = _cwd_project()
    return Actor(kind=kind, project=project)


def check_modify(issue: Issue, caller: Actor, force: bool) -> str | None:
    """Return an error message if caller may not modify issue, else None."""
    if issue.project == caller.project:
        return None
    if not caller.is_agent and (force or caller.project is None):
        return None
    owner = f"project '{issue.project}'" if issue.project else "the backlog"
    hint = "" if caller.is_agent else " (use --force to override)"
    if issue.project is None and caller.project is not None:
        hint = f"; claim it first with `verp issue claim {issue.id}`"
    return f"issue {issue.id} belongs to {owner}{hint}"


def handoff_violations(issue: Issue) -> list[str]:
    """Strings from the issue's current project that must not appear in a
    brief handed off to another project."""
    if issue.project is None:
        return []
    info = get_project(issue.project)
    if info is None:
        return []
    forbidden = [info.path, info.branch]
    text = f"{issue.title}\n{issue.body}"
    return [s for s in forbidden if s and s in text]
