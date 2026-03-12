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
    updated_permissions: list[dict[str, object]] | None = None
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
        try:
            cols = struct.unpack(
                "hhhh",
                fcntl.ioctl(
                    sys.stdout.fileno(), termios.TIOCGWINSZ, b"\x00" * 8
                ),
            )[1]
        except Exception:
            cols = 80
        max_len = cols - 8
        wrapped = []
        for line in cmd.split("\n"):
            while len(line) > max_len:
                wrapped.append(line[:max_len])
                line = line[max_len:]
            wrapped.append(line)
        return "Run: " + "\n".join(wrapped)
    elif tool == "Read":
        name = Path(tool_input.get("file_path", "file")).name
        return f"Do you want to read {name}?"
    else:
        return f"Allow {tool}?"


def _session_allow_label(
    tool: str, permission_suggestions: list[dict[str, object]]
) -> str:
    if permission_suggestions:
        s = permission_suggestions[0]
        if s.get("type") == "toolAlwaysAllow":
            return f"Yes, always allow {s.get('tool', tool)}"
        if s.get("type") == "addRules":
            rules = s.get("rules") or []
            if rules and isinstance(rules, list):
                r = rules[0]
                name = r.get("toolName", tool)
                content = r.get("ruleContent", "")
                rule_str = f"{name}({content})" if content else str(name)
                return f"Yes, always allow {rule_str}"
    return f"Yes, allow {tool} this session"


def _build_options(
    tool: str, permission_suggestions: list[dict[str, object]]
) -> list[str]:
    return [
        "Yes",
        _session_allow_label(tool, permission_suggestions),
        "No",
    ]


def _render_options(
    stdout_fd: int,
    selected: int,
    options: list[str],
) -> None:
    for i, label in enumerate(options):
        if i == selected:
            line = f"\r \x1b[34m❯ {i + 1}. {label}\x1b[0m\x1b[K\r\n"
        else:
            line = f"\r   {i + 1}. {label}\x1b[K\r\n"
        os.write(stdout_fd, line.encode())


def _claude_dialog_lines(tool: str, tool_input: dict[str, str]) -> int:
    try:
        cols = struct.unpack(
            "hhhh",
            fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\x00" * 8),
        )[1]
    except Exception:
        cols = 80
    if tool == "Bash":
        command = tool_input.get("command", "")
        # Claude's dialog: "Bash command" header (1) + blank (1)
        header = 2
        # Footer: blank + question + 2 options + blank + help = 6 lines.
        # When the command has newlines, Claude adds a warning (+ blank before
        # and after it), growing the footer by 2.
        footer = 8 if "\n" in command else 6
        # Each physical line in the command is displayed with a 3-space indent
        # and wraps at (cols - 3) characters.
        cols_avail = max(cols - 3, 1)
        cmd_display: int = sum(
            max(1, (len(line) + cols_avail - 1) // cols_avail)
            for line in command.split("\n")
        )
        return header + cmd_display + footer + 1  # +1 buffer
    return 7


def _show_permission_dialog(
    tool: str,
    tool_input: dict[str, str],
    stdin_fd: int,
    permission_suggestions: list[dict[str, object]],
    session_id: str = "",
    directory: str = "",
) -> PermissionDecision:
    stdout_fd = sys.stdout.fileno()

    try:
        cols = struct.unpack(
            "hhhh",
            fcntl.ioctl(stdout_fd, termios.TIOCGWINSZ, b"\x00" * 8),
        )[1]
    except Exception:
        cols = 80

    question = _format_question(tool, tool_input)
    question_lines = question.count("\n") + 1
    question_terminal = question.replace("\n", "\r\n")
    options = _build_options(tool, permission_suggestions)
    # prefix is " ❯ N. " or "   N. " = 5 chars; label wraps at cols - 5
    option_cols = max(cols - 5, 1)
    options_display_lines = sum(
        max(1, (len(label) + option_cols - 1) // option_cols)
        for label in options
    )
    n = _claude_dialog_lines(tool, tool_input)
    os.write(stdout_fd, f"\x1b[{n}A\r\x1b[J".encode())
    os.write(
        stdout_fd, f"\r\n \x1b[1m{question_terminal}\x1b[0m\r\n\r\n".encode()
    )

    selected = 0
    _render_options(stdout_fd, selected, options)
    os.write(stdout_fd, " \x1b[2mEsc to cancel\x1b[0m\r\n".encode())

    termios.tcflush(stdin_fd, termios.TCIFLUSH)

    def _clear_dialog() -> None:
        # Clear verp dialog lines and return cursor to row R (the jump target).
        # Layout: question_lines + blank(1) + options_display_lines + esc(1).
        dialog_lines = question_lines + 1 + options_display_lines + 1
        os.write(stdout_fd, b"\x1b[1A\r\x1b[K" * dialog_lines + b"\x1b[1A")
        # Restore cursor to C_end where Claude expects it (R + n).
        if n > 0:
            os.write(stdout_fd, f"\x1b[{n}B".encode())
        termios.tcflush(stdin_fd, termios.TCIFLUSH)

    in_escape = False
    in_csi = False
    while True:
        r, _, _ = select.select([stdin_fd], [], [], 3.0)
        if not r:
            if session_id and directory:
                import time

                set_agent_status(
                    session_id,
                    directory,
                    "waiting_permission",
                    int(time.time() * 1000),
                )
            continue
        ch = os.read(stdin_fd, 1)
        b = ch[0]
        if in_escape:
            if b == 0x5B:  # [
                in_csi = True
            elif in_csi:
                if 0x40 <= b <= 0x7E:  # final byte
                    if b == 0x41 and selected > 0:  # up
                        selected -= 1
                        os.write(
                            stdout_fd,
                            f"\x1b[{options_display_lines + 1}A".encode(),
                        )
                        _render_options(stdout_fd, selected, options)
                        os.write(stdout_fd, b"\x1b[1B")
                    elif b == 0x42 and selected < len(options) - 1:  # down
                        selected += 1
                        os.write(
                            stdout_fd,
                            f"\x1b[{options_display_lines + 1}A".encode(),
                        )
                        _render_options(stdout_fd, selected, options)
                        os.write(stdout_fd, b"\x1b[1B")
                    in_escape = in_csi = False
            else:
                in_escape = False
            continue
        if ch == b"\x1b":
            r, _, _ = select.select([stdin_fd], [], [], 0.05)
            if not r:
                _clear_dialog()
                return PermissionDecision("deny", interrupt=True)
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

    _clear_dialog()
    if selected == 1:
        return PermissionDecision(
            "allow",
            updated_permissions=permission_suggestions or None,
        )
    if selected == 2:
        return PermissionDecision("deny", interrupt=True)
    return PermissionDecision("allow")


def handle_permission_request(conn: socket.socket, stdin_fd: int) -> None:
    try:
        chunks = []
        while chunk := conn.recv(4096):
            chunks.append(chunk)
        req = json.loads(b"".join(chunks))
    except Exception:
        try:
            conn.sendall(
                json.dumps(asdict(PermissionDecision("deny"))).encode()
            )
        except BrokenPipeError:
            pass
        conn.close()
        return

    tool = req.get("tool", "unknown")
    tool_input = req.get("input", {})
    permission_suggestions = req.get("permission_suggestions", [])
    session_id = req.get("session_id", "")
    directory = req.get("directory", "")
    decision = _show_permission_dialog(
        tool,
        tool_input,
        stdin_fd,
        permission_suggestions,
        session_id,
        directory,
    )
    try:
        conn.sendall(json.dumps(asdict(decision)).encode())
    except BrokenPipeError:
        pass
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
        permission_suggestions = data.get("permission_suggestions", [])
    except Exception:
        tool_input = {}
        permission_suggestions = []

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(sock_path)
        sock.sendall(
            json.dumps(
                {
                    "tool": tool,
                    "input": tool_input,
                    "permission_suggestions": permission_suggestions,
                    "session_id": session_id,
                    "directory": directory,
                }
            ).encode()
        )
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

    if directory and decision.behavior == "allow":
        import time

        set_agent_status(
            session_id, directory, "working", int(time.time() * 1000)
        )

    decision_obj: dict[str, object] = {"behavior": decision.behavior}
    if decision.updated_input is not None:
        decision_obj["updatedInput"] = decision.updated_input
    if decision.updated_permissions is not None:
        decision_obj["updatedPermissions"] = decision.updated_permissions
    if decision.message is not None:
        decision_obj["message"] = decision.message
    if decision.interrupt:
        decision_obj["interrupt"] = True

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": decision_obj,
                }
            }
        ),
        flush=True,
    )
    return 0
