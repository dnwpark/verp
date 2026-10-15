"""Shared launch helpers for agent wrappers (`verp claude`, `verp pi`)."""

import os

from verp.commands import get_current_project


def get_project_system_prompt() -> str | None:
    project_info = get_current_project()
    if project_info is None:
        return None
    return (
        f"You are working inside a verp project named '{project_info.name}' "
        f"at {project_info.path}."
    )


def agent_env() -> dict[str, str]:
    """Environment for a launched agent process.

    Sets VERP_PROJECT, the agent's fixed identity for `verp issue` (empty when
    launched outside a project), so it survives the agent changing directory.
    """
    env = os.environ.copy()
    project_info = get_current_project()
    env["VERP_PROJECT"] = project_info.name if project_info else ""
    return env
