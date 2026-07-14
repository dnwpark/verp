from verp.agent import AgentKind
from verp.db import (
    AgentStatus,
    _verp_pid,
    register_session,
    remove_agent,
    reset_agent_tool,
    set_agent_status,
    set_agent_tool,
)


def cmd_internal_hook_pi_session_start(session_id: str, timestamp: int) -> int:
    pid = _verp_pid()
    if pid is not None:
        register_session(pid, session_id)
    return 0


def cmd_internal_hook_pi_session_end(session_id: str, timestamp: int) -> int:
    remove_agent(session_id)
    return 0


def cmd_internal_hook_pi_agent_start(
    session_id: str, directory: str, timestamp: int
) -> int:
    if not directory:
        return 0
    set_agent_status(
        session_id, directory, AgentStatus.WORKING, timestamp, AgentKind.PI
    )
    return 0


def cmd_internal_hook_pi_agent_settled(
    session_id: str, directory: str, timestamp: int
) -> int:
    if not directory:
        return 0
    set_agent_status(
        session_id,
        directory,
        AgentStatus.WAITING_PROMPT,
        timestamp,
        AgentKind.PI,
    )
    return 0


def cmd_internal_hook_pi_tool_call(
    session_id: str, directory: str, tool: str, timestamp: int
) -> int:
    if not directory:
        return 0
    set_agent_status(
        session_id, directory, AgentStatus.WORKING, timestamp, AgentKind.PI
    )
    set_agent_tool(session_id, tool)
    return 0


def cmd_internal_hook_pi_tool_result(
    session_id: str, directory: str, tool: str, timestamp: int
) -> int:
    if not directory:
        return 0
    set_agent_status(
        session_id, directory, AgentStatus.WORKING, timestamp, AgentKind.PI
    )
    reset_agent_tool(session_id)
    return 0


__all__ = [
    "cmd_internal_hook_pi_session_start",
    "cmd_internal_hook_pi_session_end",
    "cmd_internal_hook_pi_agent_start",
    "cmd_internal_hook_pi_agent_settled",
    "cmd_internal_hook_pi_tool_call",
    "cmd_internal_hook_pi_tool_result",
]
