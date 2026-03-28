import os
import subprocess
import sys
from typing import TYPE_CHECKING

from verp.focus._base import TerminalFocuser

if TYPE_CHECKING:
    from verp.db import TerminalInfo

TERMINAL_EMULATORS = frozenset(
    {
        "wezterm-gui",
        "kitty",
        "iTerm2",
        "Terminal",
        "gnome-terminal-server",
        "alacritty",
        "foot",
        "xterm",
        "xfce4-terminal",
        "konsole",
        "tilix",
        "hyper",
    }
)


def pid_to_tty(pid: int) -> str | None:
    """Return the controlling TTY device path for the given PID."""
    if sys.platform == "linux":
        try:
            return os.readlink(f"/proc/{pid}/fd/0")
        except OSError:
            return None
    else:
        try:
            result = subprocess.run(
                ["lsof", "-p", str(pid), "-a", "-d", "0", "-F", "n"],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in result.stdout.splitlines():
                if line.startswith("n/dev/"):
                    return line[1:]
        except OSError:
            pass
        return None


def focus_by_tty(tty: str, terminal: "TerminalInfo | None" = None) -> bool:
    from verp.focus._focusers._wezterm import WeztermFocuser
    from verp.focus._focusers._kitty import KittyFocuser
    from verp.focus._focusers._tmux import TmuxFocuser

    focusers: list[TerminalFocuser] = [
        WeztermFocuser(),
        KittyFocuser(terminal=terminal),
        TmuxFocuser(),
    ]

    if sys.platform == "darwin":
        from verp.focus._focusers._macos import MacOSFocuser

        focusers.append(MacOSFocuser())
    else:
        from verp.focus._focusers._linux_x11 import LinuxX11Focuser

        focusers.append(LinuxX11Focuser())

    for focuser in focusers:
        if not focuser.available():
            continue
        try:
            if focuser.focus(tty):
                return True
        except Exception:
            continue

    return False
