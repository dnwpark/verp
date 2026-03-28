import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verp.db import TerminalInfo


class ITermFocuser:
    def __init__(self, terminal: "TerminalInfo | None" = None) -> None:
        self._active = terminal is not None and terminal.app == "iTerm.app"

    def available(self) -> bool:
        return self._active

    def focus(self, tty: str) -> bool:
        script = f"""
tell application "iTerm2"
    repeat with w in windows
        set tabCount to count of tabs of w
        repeat with tabIndex from 1 to tabCount
            set t to tab tabIndex of w
            set sessionCount to count of sessions of t
            repeat with sessionIndex from 1 to sessionCount
                set s to session sessionIndex of t
                if tty of s is "{tty}" then
                    select t
                    set index of w to 1
                    activate
                    return
                end if
            end repeat
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
