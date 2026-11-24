import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verp.db import TerminalInfo

# Ghostty (1.4+) exposes every terminal surface (split pane) to AppleScript
# with its TTY, and a `focus` command that selects its tab and brings its
# window to the front.
_SCRIPT = """
tell application "Ghostty"
    repeat with t in terminals
        if tty of t is "{tty}" then
            focus t
            activate
            return
        end if
    end repeat
end tell
error "no Ghostty terminal with tty {tty}"
"""


class GhosttyFocuser:
    def __init__(self, terminal: "TerminalInfo | None" = None) -> None:
        self._active = (
            terminal is not None and terminal.app.lower() == "ghostty"
        )

    def available(self) -> bool:
        return self._active

    def focus(self, tty: str) -> bool:
        try:
            result = subprocess.run(
                ["osascript", "-e", _SCRIPT.format(tty=tty)],
                capture_output=True,
                check=False,
            )
            return result.returncode == 0
        except OSError:
            return False
