from pathlib import Path
from typing import NamedTuple

from verp.time import now_ms


class DirectoryParts(NamedTuple):
    project_name: str | None  # highlighted portion, None if not under a project
    suffix: str  # greyed portion (relative path, ~/path, or full path)


def directory_parts(directory: str) -> DirectoryParts:
    """Compute the display parts of a directory path for agent list rendering."""
    from verp.db import is_project_dir

    path = Path(directory)
    if is_project_dir(path):
        return DirectoryParts(project_name=path.name, suffix="")
    for p in path.parents:
        if is_project_dir(p):
            return DirectoryParts(
                project_name=p.name, suffix=f"/{path.relative_to(p)}"
            )
    home = Path.home()
    try:
        return DirectoryParts(
            project_name=None, suffix=f"~/{path.relative_to(home)}"
        )
    except ValueError:
        return DirectoryParts(project_name=None, suffix=directory)


def format_age(updated_at: int) -> str:
    secs = (now_ms() - updated_at) // 1000
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"
