# NOTE: untested
import os
import shutil
import subprocess

from verp.focus._proc import TERMINAL_EMULATORS


def _ppid_chain_to_terminal(pid: int) -> int | None:
    current = pid
    for _ in range(20):
        try:
            status = open(f"/proc/{current}/status").read()
        except OSError:
            return None
        name = ""
        ppid = 0
        for line in status.splitlines():
            if line.startswith("Name:"):
                name = line.split(None, 1)[1].strip()
            elif line.startswith("PPid:"):
                ppid = int(line.split(None, 1)[1].strip())
        if name in TERMINAL_EMULATORS:
            return current
        if ppid <= 1:
            return None
        current = ppid
    return None


def _tty_to_pid(tty: str) -> int | None:
    """Find a PID with stdin on the given TTY by scanning /proc."""
    try:
        tty_dev = os.stat(tty).st_rdev
    except OSError:
        return None
    try:
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                if os.stat(f"/proc/{entry.name}/fd/0").st_rdev == tty_dev:
                    return int(entry.name)
            except OSError:
                continue
    except OSError:
        pass
    return None


class LinuxX11Focuser:
    def available(self) -> bool:
        try:
            import ewmh  # type: ignore[import-not-found]  # noqa: F401

            return True
        except ImportError:
            return False

    def focus(self, tty: str) -> bool:
        pid = _tty_to_pid(tty)
        if pid is None:
            return False
        term_pid = _ppid_chain_to_terminal(pid)
        if term_pid is None:
            return False

        try:
            import ewmh
            from Xlib.display import Display  # type: ignore[import-untyped]

            display = Display()
            e = ewmh.EWMH(_display=display)
            for w in e.getClientList():
                if e.getWmPid(w) == term_pid:
                    e.setActiveWindow(w)
                    display.flush()
                    return True
        except Exception:
            pass

        # Fallback: xdotool
        if shutil.which("xdotool"):
            try:
                result = subprocess.run(
                    ["xdotool", "search", "--pid", str(term_pid)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                win_ids = result.stdout.split()
                if win_ids:
                    subprocess.run(
                        ["xdotool", "windowactivate", win_ids[0]],
                        check=False,
                    )
                    return True
            except OSError:
                pass

        return False
