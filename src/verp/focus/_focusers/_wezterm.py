# NOTE: untested
import json
import shutil
import subprocess


class WeztermFocuser:
    def available(self) -> bool:
        return shutil.which("wezterm") is not None

    def focus(self, tty: str) -> bool:
        try:
            result = subprocess.run(
                ["wezterm", "cli", "list", "--format=json"],
                capture_output=True,
                text=True,
                check=False,
            )
            panes = json.loads(result.stdout)
        except Exception:
            return False

        for pane in panes:
            if pane.get("tty_name") == tty:
                pane_id = pane.get("pane_id")
                if pane_id is None:
                    continue
                subprocess.run(
                    [
                        "wezterm",
                        "cli",
                        "activate-pane",
                        "--pane-id",
                        str(pane_id),
                    ],
                    check=False,
                )
                return True

        return False
