# NOTE: untested
import shutil
import subprocess

import os


class TmuxFocuser:
    def available(self) -> bool:
        return shutil.which("tmux") is not None and bool(os.environ.get("TMUX"))

    def focus(self, tty: str) -> bool:
        try:
            result = subprocess.run(
                ["tmux", "list-panes", "-a", "-F", "#{pane_tty} #{pane_id}"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return False

        for line in result.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0] == tty:
                subprocess.run(
                    ["tmux", "select-pane", "-t", parts[1]],
                    check=False,
                )
                return True

        return False
