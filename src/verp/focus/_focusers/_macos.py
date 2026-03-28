import subprocess


def _focus_via_osascript(tty: str) -> bool:
    script = f"""
tell application "Terminal"
    repeat with w in windows
        repeat with t in tabs of w
            if tty of t is "{tty}" then
                set selected of t to true
                set index of w to 1
                activate
                return
            end if
        end repeat
    end repeat
end tell
"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except OSError:
        return False


def _tty_owner_pids(tty: str) -> list[int]:
    tty_short = tty.removeprefix("/dev/")
    try:
        result = subprocess.run(
            ["ps", "-t", tty_short, "-o", "pid="],
            capture_output=True,
            text=True,
            check=False,
        )
        pids = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line:
                try:
                    pids.append(int(line))
                except ValueError:
                    pass
        return pids
    except OSError:
        return []


def _ancestor_commands(pid: int, depth: int = 8) -> list[str]:
    commands = []
    current = pid
    for _ in range(depth):
        try:
            result = subprocess.run(
                ["ps", "-p", str(current), "-o", "ppid=,comm="],
                capture_output=True,
                text=True,
                check=False,
            )
            line = result.stdout.strip()
            if not line:
                break
            parts = line.split(None, 1)
            if len(parts) < 2:
                break
            ppid_str, comm = parts
            commands.append(comm)
            current = int(ppid_str)
            if current <= 1:
                break
        except (OSError, ValueError):
            break
    return commands


# Maps substrings in ancestor command names to the macOS app name to activate
_APP_PATTERNS: list[tuple[str, str]] = [
    ("Cursor", "Cursor"),
    ("Code", "Code"),
    ("kitty", "kitty"),
]


def _detect_editor(tty: str) -> str | None:
    for pid in _tty_owner_pids(tty):
        for cmd in _ancestor_commands(pid):
            for pattern, app in _APP_PATTERNS:
                if pattern in cmd:
                    return app
    return None


def _focus_app(app: str) -> bool:
    try:
        result = subprocess.run(
            ["osascript", "-e", f'tell application "{app}" to activate'],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except OSError:
        return False


class MacOSFocuser:
    def available(self) -> bool:
        return True

    def focus(self, tty: str) -> bool:
        if app := _detect_editor(tty):
            return _focus_app(app)
        return _focus_via_osascript(tty)
