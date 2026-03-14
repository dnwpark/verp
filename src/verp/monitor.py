import asyncio
from pathlib import Path

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

from verp.agent import format_age
from verp.db import AgentInfo, get_all_agents, is_project_dir

_STATUS_ORDER = {
    "waiting_permission": 0,
    "asking_question": 0,
    "waiting_prompt": 1,
    "working": 2,
}

_STATUS_STYLE = {
    "working": "fg:ansigreen",
    "waiting_prompt": "fg:ansiyellow",
    "asking_question": "fg:#ff8700",
    "waiting_permission": "fg:#ff8700",
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
        return sorted(
            agents,
            key=lambda a: (_STATUS_ORDER.get(a.status, 99), -a.updated_at),
        )

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
        return [("class:status-bar", "  ↑↓ navigate   Enter focus   q quit")]

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

    def _focus_selected(self) -> None:
        from verp.focus import focus_by_tty, pid_to_tty

        if self._selected is None or self._selected >= len(self._agents):
            return
        agent = self._agents[self._selected]
        if agent.verp_pid is None:
            return
        tty = pid_to_tty(agent.verp_pid)
        if tty:
            focus_by_tty(tty)

    async def _refresh_loop(self) -> None:
        while True:
            agents = self._sorted_agents(get_all_agents())
            self._agents = agents
            if self._selected is not None:
                self._selected = min(self._selected, max(0, len(agents) - 1))
            self._app.invalidate()
            await asyncio.sleep(0.5)

    def run(self) -> None:
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
