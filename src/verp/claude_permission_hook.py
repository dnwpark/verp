import fcntl
import json
import os
import select
import socket
import struct
import sys
import termios
from dataclasses import asdict, dataclass
from pathlib import Path

from verp.db import set_agent_status, set_agent_tool


@dataclass
class PermissionDecision:
    behavior: str  # "allow" or "deny"
    updated_input: dict[str, str] | None = (
        None  # allow only: modifies tool input
    )
    updated_permissions: list[dict[str, str]] | None = (
        None  # allow only: applies "always allow" rules
    )
    message: str | None = None  # deny only: tells Claude why
    interrupt: bool = False  # deny only: stops Claude


def _format_question(tool: str, tool_input: dict[str, str]) -> str:
    if tool == "Write":
        name = Path(tool_input.get("file_path", "file")).name
        return f"Do you want to write {name}?"
    elif tool in ("Edit", "MultiEdit"):
        name = Path(tool_input.get("file_path", "file")).name
        return f"Do you want to edit {name}?"
    elif tool == "Bash":
        cmd = tool_input.get("command", "")
        cols = struct.unpack(
            "hhhh",
            fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\x00" * 8),
        )[1]
        if len(cmd) > cols - 8:
            cmd = cmd[: cols - 11] + "..."
        return f"Run: {cmd}"
    elif tool == "Read":
        name = Path(tool_input.get("file_path", "file")).name
        return f"Do you want to read {name}?"
    else:
        return f"Allow {tool}?"


def _render_options(stdout_fd: int, selected: int, tool: str) -> None:
    options = [
        "Yes",
        f"Yes, allow {tool} this session",
        "No",
    ]
    for i, label in enumerate(options):
        if i == selected:
            line = f"\r \x1b[34m❯ {i + 1}. {label}\x1b[0m\x1b[K\r\n"
        else:
            line = f"\r   {i + 1}. {label}\x1b[K\r\n"
        os.write(stdout_fd, line.encode())


def _show_permission_dialog(
    tool: str,
    tool_input: dict[str, str],
    stdin_fd: int,
    session_allowed: set[str],
) -> PermissionDecision:
    stdout_fd = sys.stdout.fileno()

    question = _format_question(tool, tool_input)
    os.write(stdout_fd, f"\r\n \x1b[1m{question}\x1b[0m\r\n\r\n".encode())

    selected = 0
    _render_options(stdout_fd, selected, tool)
    os.write(stdout_fd, " \x1b[2mEsc to cancel\x1b[0m\r\n".encode())

    termios.tcflush(stdin_fd, termios.TCIFLUSH)

    def _clear_dialog() -> None:
        os.write(stdout_fd, b"\x1b[6A\r\x1b[0J")
        termios.tcflush(stdin_fd, termios.TCIFLUSH)

    in_escape = False
    in_csi = False
    while True:
        ch = os.read(stdin_fd, 1)
        b = ch[0]
        if in_escape:
            if b == 0x5B:  # [
                in_csi = True
            elif in_csi:
                if 0x40 <= b <= 0x7E:  # final byte
                    if b == 0x41 and selected > 0:  # up
                        selected -= 1
                        os.write(stdout_fd, b"\x1b[4A")
                        _render_options(stdout_fd, selected, tool)
                        os.write(stdout_fd, b"\x1b[1B")
                    elif b == 0x42 and selected < 2:  # down
                        selected += 1
                        os.write(stdout_fd, b"\x1b[4A")
                        _render_options(stdout_fd, selected, tool)
                        os.write(stdout_fd, b"\x1b[1B")
                    in_escape = in_csi = False
            else:
                in_escape = False
            continue
        if ch == b"\x1b":
            r, _, _ = select.select([stdin_fd], [], [], 0.05)
            if not r:
                _clear_dialog()
                return PermissionDecision("deny")
            in_escape = True
            continue
        if ch in (b"y", b"Y"):
            selected = 0
            break
        if ch in (b"a", b"A"):
            selected = 1
            break
        if ch in (b"n", b"N", b"\x03"):
            selected = 2
            break
        if ch in (b"\r", b"\n", b" "):
            break

    termios.tcflush(stdin_fd, termios.TCIFLUSH)
    os.write(stdout_fd, b"\x1b[4A")
    _render_options(stdout_fd, selected, tool)
    os.write(stdout_fd, b"\x1b[0J\r\n")
    if selected == 1:
        session_allowed.add(tool)
    return PermissionDecision("allow" if selected < 2 else "deny")


def handle_permission_request(
    conn: socket.socket, stdin_fd: int, session_allowed: set[str]
) -> None:
    try:
        chunks = []
        while chunk := conn.recv(4096):
            chunks.append(chunk)
        req = json.loads(b"".join(chunks))
    except Exception:
        conn.sendall(json.dumps(asdict(PermissionDecision("deny"))).encode())
        conn.close()
        return

    tool = req.get("tool", "unknown")
    tool_input = req.get("input", {})
    if tool in session_allowed:
        decision = PermissionDecision("allow")
    else:
        decision = _show_permission_dialog(
            tool, tool_input, stdin_fd, session_allowed
        )
    conn.sendall(json.dumps(asdict(decision)).encode())
    conn.close()


def cmd_internal_hook_permission_request(
    session_id: str, directory: str, tool: str, timestamp: int
) -> int:
    if directory:
        set_agent_status(session_id, directory, "waiting_permission", timestamp)
        set_agent_tool(session_id, tool)

    sock_path = os.environ.get("VERP_SOCKET")
    if not sock_path:
        return 0

    try:
        data = json.loads(sys.stdin.read())
        tool_input = data.get("tool_input", {})
    except Exception:
        tool_input = {}

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(sock_path)
        sock.sendall(json.dumps({"tool": tool, "input": tool_input}).encode())
        sock.shutdown(socket.SHUT_WR)
        raw = b""
        while chunk := sock.recv(4096):
            raw += chunk
        sock.close()
    except OSError:
        return 0

    try:
        decision = PermissionDecision(**json.loads(raw.decode()))
    except Exception:
        return 0

    decision_obj: dict[str, object] = {"behavior": decision.behavior}
    if decision.updated_input is not None:
        decision_obj["updatedInput"] = decision.updated_input
    if decision.updated_permissions is not None:
        decision_obj["updatedPermissions"] = decision.updated_permissions
    if decision.message is not None:
        decision_obj["message"] = decision.message
    if decision.interrupt:
        decision_obj["interrupt"] = True

    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": decision_obj,
                }
            }
        )
    )
    return 0
