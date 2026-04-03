import asyncio
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

from verp.agent import format_age
from verp.paths import DATA_DIR
from verp.db import (
    AgentInfo,
    AgentStatus,
    TerminalInfo,
    clear_agent_by_prefix,
    get_all_agents,
    is_project_dir,
    set_agent_status_by_session,
)
from verp.focus import focus_by_tty, pid_to_tty

_LOCK_FILE = DATA_DIR / "monitor.pid"


@dataclass
class MonitorLock:
    pid: int
    tty: str
    terminal: TerminalInfo | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "MonitorLock":
        d = json.loads(text)
        terminal_d = d.get("terminal")
        terminal = (
            TerminalInfo(
                app=terminal_d["app"], data=terminal_d.get("data") or {}
            )
            if terminal_d
            else None
        )
        return cls(pid=d["pid"], tty=d["tty"], terminal=terminal)


def _write_lock() -> None:
    from verp.db import _terminal_info

    tty = pid_to_tty(os.getpid())
    if tty:
        lock = MonitorLock(pid=os.getpid(), tty=tty, terminal=_terminal_info())
        _LOCK_FILE.write_text(lock.to_json())


def _clear_lock() -> None:
    _LOCK_FILE.unlink(missing_ok=True)


def focus_existing_monitor() -> bool:
    """Focus a running monitor if one exists. Returns True if focused."""
    if not _LOCK_FILE.exists():
        return False
    try:
        lock = MonitorLock.from_json(_LOCK_FILE.read_text())
        os.kill(lock.pid, 0)  # check liveness
        return focus_by_tty(lock.tty, terminal=lock.terminal)
    except Exception:
        return False


_STATUS_STYLE = {
    AgentStatus.WORKING: "fg:ansigreen",
    AgentStatus.WAITING_PROMPT: "fg:ansiyellow",
    AgentStatus.ASKING_QUESTION: "fg:#ff8700",
    AgentStatus.WAITING_PERMISSION: "fg:#ff8700",
    AgentStatus.PAUSED: "fg:grey",
}


def _format_directory(directory: str) -> list[tuple[str, str]]:
    path = Path(directory)
    if is_project_dir(path):
        return [("fg:mediumpurple", path.name)]
    for p in path.parents:
        if is_project_dir(p):
            return [
                ("fg:mediumpurple", p.name),
                ("fg:grey", f"/{path.relative_to(p)}"),
            ]
    home = Path.home()
    try:
        return [("fg:grey", f"~/{path.relative_to(home)}")]
    except ValueError:
        return [("fg:grey", directory)]


class AgentMonitor:
    def __init__(self) -> None:
        self._agents: list[AgentInfo] = []
        self._selected: int | None = None
        self._app: Application[None] = self._build_app()

    def _sorted_agents(self, agents: list[AgentInfo]) -> list[AgentInfo]:
        return sorted(agents, key=lambda a: a.directory)

    def _render_table(self) -> StyleAndTextTuples:
        result: StyleAndTextTuples = []
        if not self._agents:
            result.append(("fg:grey", "  no agents\n"))
            return result

        # Pre-compute row data to measure column widths
        rows = []
        for agent in self._agents:
            status_str = (
                f"{agent.status} ({agent.tool})" if agent.tool else agent.status
            )
            dir_parts = _format_directory(agent.directory)
            dir_text = "".join(t for _, t in dir_parts)
            rows.append((agent, dir_parts, dir_text, status_str))

        dir_w = max(len(r[2]) for r in rows)
        status_w = max(len(r[3]) for r in rows)

        for i, (agent, dir_parts, dir_text, status_str) in enumerate(rows):
            sel = self._selected is not None and i == self._selected
            row = "reverse " if sel else ""

            result.append((row + "bold", f"  {agent.session_id[:8]}  "))

            for style, text in dir_parts:
                result.append((row + style, text))
            result.append((row, " " * (dir_w - len(dir_text) + 2)))

            result.append(
                (row + _STATUS_STYLE.get(agent.status, ""), status_str)
            )
            result.append((row, " " * (status_w - len(status_str) + 2)))

            result.append((row + "fg:grey", format_age(agent.updated_at)))
            result.append(("", "\n"))

        return result

    def _render_status_bar(self) -> StyleAndTextTuples:
        return [
            (
                "class:status-bar",
                "  ↑↓ navigate   Enter focus   p pause/unpause   Del clear   q quit",
            )
        ]

    def _build_app(self) -> "Application[None]":
        kb = KeyBindings()

        @kb.add("up")
        def _up(event: KeyPressEvent) -> None:
            if not self._agents:
                return
            if self._selected is None:
                self._selected = len(self._agents) - 1
            else:
                self._selected = max(0, self._selected - 1)

        @kb.add("down")
        def _down(event: KeyPressEvent) -> None:
            if not self._agents:
                return
            if self._selected is None:
                self._selected = 0
            else:
                self._selected = min(len(self._agents) - 1, self._selected + 1)

        @kb.add("escape")
        def _deselect(event: KeyPressEvent) -> None:
            self._selected = None

        @kb.add("enter")
        def _enter(event: KeyPressEvent) -> None:
            self._focus_selected()

        @kb.add("p")
        def _pause(event: KeyPressEvent) -> None:
            self._toggle_paused()

        @kb.add("delete")
        def _delete(event: KeyPressEvent) -> None:
            self._clear_selected()

        @kb.add("q")
        @kb.add("c-c")
        def _quit(event: KeyPressEvent) -> None:
            event.app.exit()

        layout = Layout(
            HSplit(
                [
                    Window(
                        content=FormattedTextControl(
                            self._render_table, focusable=True
                        )
                    ),
                    Window(
                        content=FormattedTextControl(self._render_status_bar),
                        height=1,
                    ),
                ]
            )
        )

        return Application(
            layout=layout,
            key_bindings=kb,
            style=Style.from_dict({"status-bar": "reverse"}),
            full_screen=True,
        )

    def _clear_selected(self) -> None:
        if self._selected is None or self._selected >= len(self._agents):
            return
        agent = self._agents[self._selected]
        clear_agent_by_prefix(agent.session_id)

    def _toggle_paused(self) -> None:
        if self._selected is None or self._selected >= len(self._agents):
            return
        agent = self._agents[self._selected]
        new_status = (
            AgentStatus.WAITING_PROMPT
            if agent.status == AgentStatus.PAUSED
            else AgentStatus.PAUSED
        )
        set_agent_status_by_session(agent.session_id, new_status)

    def _focus_selected(self) -> None:
        if self._selected is None or self._selected >= len(self._agents):
            return
        agent = self._agents[self._selected]
        if agent.verp_pid is None:
            return
        tty = pid_to_tty(agent.verp_pid)
        if tty:
            focus_by_tty(tty, terminal=agent.terminal)

    async def _refresh_loop(self) -> None:
        while True:
            agents = self._sorted_agents(get_all_agents())
            self._agents = agents
            if self._selected is not None:
                self._selected = min(self._selected, max(0, len(agents) - 1))
            self._app.invalidate()
            await asyncio.sleep(0.5)

    def run(self) -> None:
        if focus_existing_monitor():
            return
        _write_lock()
        try:

            async def main() -> None:
                task = asyncio.create_task(self._refresh_loop())
                try:
                    await self._app.run_async()
                finally:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            asyncio.run(main())
        finally:
            _clear_lock()
