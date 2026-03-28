import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from verp.focus._proc import pid_to_tty

if TYPE_CHECKING:
    from verp.db import TerminalInfo


def ensure_kitty_config() -> None:
    """If running inside kitty, ensure verp's include line is in kitty.conf."""
    from verp.db import _terminal_info

    terminal = _terminal_info()
    if not terminal or terminal.app != "kitty":
        return

    from verp.paths import DATA_DIR

    verp_conf = DATA_DIR / "kitty" / "verp.conf"
    kitty_conf = Path.home() / ".config" / "kitty" / "kitty.conf"
    include_line = f"include {verp_conf}"
    if kitty_conf.exists():
        if include_line not in kitty_conf.read_text():
            with open(kitty_conf, "a") as f:
                f.write(f"\n# verp\n{include_line}\n")
            print(
                "verp: added kitty remote control config — restart kitty to enable window focusing",
                file=sys.stderr,
            )
    else:
        kitty_conf.parent.mkdir(parents=True, exist_ok=True)
        kitty_conf.write_text(f"# verp\n{include_line}\n")
        print(
            "verp: created kitty.conf with remote control config — restart kitty to enable window focusing",
            file=sys.stderr,
        )


class KittyFocuser:
    def __init__(self, terminal: "TerminalInfo | None" = None) -> None:
        self._listen_on: str | None = None
        if terminal and terminal.app == "kitty":
            self._listen_on = terminal.data.get("listen_on")

    def available(self) -> bool:
        return shutil.which("kitten") is not None and bool(self._listen_on)

    def focus(self, tty: str) -> bool:
        listen_on = self._listen_on
        if not listen_on:
            return False
        try:
            result = subprocess.run(
                ["kitten", "@", "--to", listen_on, "ls"],
                capture_output=True,
                text=True,
                check=False,
            )
            windows = json.loads(result.stdout)
        except Exception:
            return False

        for os_window in windows:
            for tab in os_window.get("tabs", []):
                for window in tab.get("windows", []):
                    for proc in window.get("foreground_processes", []):
                        proc_pid = proc.get("pid")
                        if proc_pid and pid_to_tty(proc_pid) == tty:
                            win_id = window.get("id")
                            if win_id is None:
                                continue
                            subprocess.run(
                                [
                                    "kitten",
                                    "@",
                                    "--to",
                                    listen_on,
                                    "focus-window",
                                    "--match",
                                    f"id:{win_id}",
                                ],
                                check=False,
                            )
                            if sys.platform == "darwin":
                                subprocess.run(
                                    [
                                        "osascript",
                                        "-e",
                                        'tell application "kitty" to activate',
                                    ],
                                    capture_output=True,
                                    check=False,
                                )
                            return True

        return False
