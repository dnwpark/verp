import fcntl
import os
import pty
import select
import signal
import socket
import sys
import termios
import tty

from verp.claude_permission_hook import (
    _query_cursor_pos,
    handle_permission_request,
)
from verp.commands import get_current_project
from verp.db import (
    AgentStatus,
    _terminal_info,
    has_agent_by_verp_pid,
    get_session_id,
    remove_agents_by_pid,
    set_agent_status,
    set_agents_status_by_pid,
)
from verp.paths import DATA_DIR
from verp.time import now_ms


def _set_winsize(fd: int) -> None:
    size = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, size)


def get_project_system_prompt() -> str | None:
    project_info = get_current_project()
    if project_info is None:
        return None
    return (
        f"You are working inside a verp project named '{project_info.name}' "
        f"at {project_info.path}."
    )


def _build_claude_cmd(args: list[str]) -> list[str]:
    from verp.paths import CLAUDE_DIR, CONFIG_DIR, USER_CLAUDE_DIR

    settings = DATA_DIR / "claude-settings.json"
    add_dirs = ["--add-dir", str(CLAUDE_DIR)]
    if USER_CLAUDE_DIR.is_dir():
        add_dirs += ["--add-dir", str(CONFIG_DIR)]
    system_prompt = get_project_system_prompt()
    append_system = (
        ["--append-system-prompt", system_prompt] if system_prompt else []
    )
    return (
        ["claude", "--settings", str(settings)]
        + add_dirs
        + append_system
        + args
    )


def _setup_socket() -> tuple[str, socket.socket]:
    from verp.paths import verp_sock_path

    sock_path = verp_sock_path(os.getpid())
    os.environ["VERP_SOCKET"] = sock_path
    listen_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listen_sock.bind(sock_path)
    listen_sock.listen(1)
    return sock_path, listen_sock


def _build_jump_sequences() -> list[bytes]:
    sequences: list[bytes] = [b"\x1c"]
    terminal = _terminal_info()
    if terminal and terminal.app == "kitty":
        sequences.append(b"\x1b[92;5u")
    elif terminal and terminal.app == "iTerm.app":
        sequences.append(b"\x1b[27;5;92~")
    return sequences


def _handle_stdin(
    data: bytes,
    jump_sequences: list[bytes],
) -> bytes:
    if b"\x03" in data:
        set_agents_status_by_pid(
            os.getpid(), AgentStatus.WAITING_PROMPT, now_ms()
        )
    if any(seq in data for seq in jump_sequences):
        from verp.monitor import focus_existing_monitor

        focus_existing_monitor()
        pid = os.getpid()
        if not has_agent_by_verp_pid(pid):
            session_id = get_session_id(pid)
            if session_id:
                set_agent_status(
                    session_id,
                    os.getcwd(),
                    AgentStatus.WAITING_PROMPT,
                    now_ms(),
                )
        for seq in jump_sequences:
            data = data.replace(seq, b"")
    return data


def _handle_permission(
    conn: socket.socket,
    stdin_fd: int,
    master_fd: int,
    pty_output_buf: bytearray,
) -> None:
    cursor_before = _query_cursor_pos(stdin_fd)
    result = handle_permission_request(conn, stdin_fd, master_fd)
    cursor_after = _query_cursor_pos(stdin_fd)
    if os.environ.get("VERP_DEBUG"):
        try:
            from verp.debug import build_snapshot, save_snapshot

            save_snapshot(
                build_snapshot(
                    cursor_before=cursor_before,
                    cursor_start=result.cursor_start,
                    cursor_end=result.cursor_end,
                    cursor_after=cursor_after,
                    pty_buffer=bytes(pty_output_buf),
                    tool=result.tool,
                    directory=result.directory,
                    decision=result.decision,
                )
            )
        except Exception:
            pass


def _pty_loop(
    master_fd: int,
    stdin_fd: int,
    listen_sock: socket.socket,
    jump_sequences: list[bytes],
) -> None:
    _PTY_BUF_MAX = 2048
    pty_output_buf = bytearray()

    old = termios.tcgetattr(stdin_fd)
    tty.setraw(stdin_fd)
    try:
        while True:
            try:
                fds, _, _ = select.select(
                    [master_fd, sys.stdin, listen_sock], [], []
                )
            except (KeyboardInterrupt, OSError):
                break
            if master_fd in fds:
                try:
                    data = os.read(master_fd, 1024)
                except OSError:
                    break
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
                pty_output_buf.extend(data)
                if len(pty_output_buf) > _PTY_BUF_MAX:
                    del pty_output_buf[:-_PTY_BUF_MAX]
            if sys.stdin in fds:
                data = os.read(stdin_fd, 1024)
                data = _handle_stdin(data, jump_sequences)
                if not data:
                    continue
                try:
                    os.write(master_fd, data)
                except OSError:
                    break
            if listen_sock in fds:
                conn, _ = listen_sock.accept()
                _handle_permission(conn, stdin_fd, master_fd, pty_output_buf)
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, old)


def cmd_claude(args: list[str]) -> int:
    from verp.debug import set_claude_version

    set_claude_version()

    cmd = _build_claude_cmd(args)
    sock_path, listen_sock = _setup_socket()
    jump_sequences = _build_jump_sequences()

    pid, master_fd = pty.fork()
    if pid == 0:
        listen_sock.close()
        env = os.environ.copy()
        env["CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"] = "1"
        os.execvpe(cmd[0], cmd, env)

    _set_winsize(master_fd)
    signal.signal(signal.SIGWINCH, lambda _s, _f: _set_winsize(master_fd))

    try:
        _pty_loop(master_fd, sys.stdin.fileno(), listen_sock, jump_sequences)
    finally:
        os.close(master_fd)
        listen_sock.close()
        try:
            os.unlink(sock_path)
        except OSError:
            pass
        try:
            remove_agents_by_pid(os.getpid())
        except Exception:
            pass

    try:
        _, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status)
    except ChildProcessError:
        return 0
