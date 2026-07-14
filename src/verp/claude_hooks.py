from verp.agent import AgentKind
from verp.claude_permission_hook import cmd_internal_hook_permission_request
from verp.db import (
    AgentStatus,
    _verp_pid,
    register_session,
    remove_agent,
    reset_agent_tool,
    set_agent_status,
    set_agent_tool,
)


def cmd_internal_hook_session_start(session_id: str, timestamp: int) -> int:
    pid = _verp_pid()
    if pid is not None:
        register_session(pid, session_id)
    return 0


def cmd_internal_hook_session_end(session_id: str, timestamp: int) -> int:
    remove_agent(session_id)
    return 0


def cmd_internal_hook_pre_tool_use(
    session_id: str, directory: str, tool: str, timestamp: int
) -> int:
    if not directory:
        return 0
    set_agent_status(
        session_id, directory, AgentStatus.WORKING, timestamp, AgentKind.CLAUDE
    )
    set_agent_tool(session_id, tool)
    return 0


def cmd_internal_hook_post_tool_use(
    session_id: str, directory: str, tool: str, timestamp: int
) -> int:
    if not directory:
        return 0
    set_agent_status(
        session_id, directory, AgentStatus.WORKING, timestamp, AgentKind.CLAUDE
    )
    reset_agent_tool(session_id)
    return 0


def cmd_internal_hook_post_tool_use_failure(
    session_id: str, directory: str, tool: str, timestamp: int
) -> int:
    if not directory:
        return 0
    set_agent_status(
        session_id,
        directory,
        AgentStatus.WAITING_PROMPT,
        timestamp,
        AgentKind.CLAUDE,
    )
    reset_agent_tool(session_id)
    return 0


def cmd_internal_hook_user_prompt_submit(
    session_id: str, directory: str, timestamp: int
) -> int:
    if not directory:
        return 0
    set_agent_status(
        session_id, directory, AgentStatus.WORKING, timestamp, AgentKind.CLAUDE
    )
    return 0


def cmd_internal_hook_stop(
    session_id: str, directory: str, timestamp: int
) -> int:
    if not directory:
        return 0
    set_agent_status(
        session_id,
        directory,
        AgentStatus.WAITING_PROMPT,
        timestamp,
        AgentKind.CLAUDE,
    )
    return 0


__all__ = [
    "cmd_internal_hook_permission_request",
    "cmd_internal_hook_session_start",
    "cmd_internal_hook_session_end",
    "cmd_internal_hook_pre_tool_use",
    "cmd_internal_hook_post_tool_use",
    "cmd_internal_hook_post_tool_use_failure",
    "cmd_internal_hook_user_prompt_submit",
    "cmd_internal_hook_stop",
]
