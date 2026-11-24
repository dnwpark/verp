# verp — agent notes

## After any code change

```bash
uv run black src/ && uv run mypy src/ && uv tool install -e .
```

Run tests with `uv run pytest`. The Claude permission dialog tests (`tests/test_permission_dialogs.py`) drive a real Claude session in tmux and take 30+ minutes; they are skipped unless `VERP_CLAUDE_TESTS=1` is set.

When adding a new source file, update the file list in both `CLAUDE.md` and `README.md`.

When adding a new agent status value, update the status table in both `CLAUDE.md` and `README.md`.

When adding or removing anything stored in `DATA_DIR`, update the Data section in both `CLAUDE.md` and `README.md`.

## Project structure

- `_claude/` — bundled managed Claude config (skills, CLAUDE.md); symlinked into package as `src/verp/_claude`
- `src/verp/_versions/` — versioned `track.sh` and `claude_settings.json` per schema version
- `src/verp/agent.py` — shared agent display utilities (`format_age`, `directory_parts`)
- `src/verp/agent_launch.py` — shared launch helpers for `verp claude` / `verp pi` (`agent_env`, `get_project_system_prompt`)
- `src/verp/cli.py` — entry point: `main()`, argparse setup, and dispatch
- `src/verp/claude_dir.py` — managed `CLAUDE_DIR` content versioning and sync
- `src/verp/pi_dir.py` — managed `pi-extension.ts` versioning and deployment
- `src/verp/claude_hooks.py` — Claude lifecycle hook handlers (`cmd_internal_hook_*`)
- `src/verp/pi_hooks.py` — pi lifecycle hook handlers (`cmd_internal_hook_pi_*`)
- `src/verp/claude_permission_hook.py` — permission dialog and socket communication
- `src/verp/commands.py` — business logic command implementations (`cmd_*`)
- `src/verp/db.py` — SQLite layer and schema migrations
- `src/verp/debug.py` — permission dialog debug snapshot capture
- `src/verp/focus/` — terminal window focus module
  - `__init__.py` — public API: `focus_by_tty(tty)`, `pid_to_tty(pid)`
  - `_base.py` — `TerminalFocuser` protocol
  - `_proc.py` — shared constants (`TERMINAL_EMULATORS`), `pid_to_tty`, and focuser dispatch
  - `_focusers/_ghostty.py` — Ghostty (1.4+) AppleScript focuser, matching terminals by TTY
  - `_focusers/_iterm2.py` — iTerm2 AppleScript focuser
  - `_focusers/_kitty.py` — kitty remote control focuser
  - `_focusers/_linux_x11.py` — Linux X11 focuser via ewmh/xdotool
  - `_focusers/_macos.py` — macOS focuser via pyobjc/osascript
  - `_focusers/_tmux.py` — tmux pane focuser
  - `_focusers/_wezterm.py` — WezTerm CLI focuser
- `src/verp/git.py` — git subprocess wrappers
- `src/verp/issues/` — issue tracker (self-contained; own DB)
  - `cli.py` — `register(sub)` argparse setup and `dispatch(args)`
  - `commands.py` — `cmd_issue_*` implementations
  - `db.py` — `issues.db` SQLite layer and migrations (`SCHEMA_VERSION` here is independent of `verp.db`)
  - `identity.py` — caller identity (`VERP_PROJECT`, cwd fallback) and ownership rules
  - `model.py` — `Issue`, `Event`, `IssueStatus`, `EventKind`
  - `orphans.py` — releasing open issues of deleted projects (`verp delete`, and a guarded repair before every `verp issue` command)
- `src/verp/monitor.py` — interactive agent monitor TUI (prompt_toolkit)
- `src/verp/paths.py` — all path constants (`DATA_DIR`, `CLAUDE_DIR`, `PI_DIR`, `CONFIG_DIR`, `USER_CLAUDE_DIR`) and socket path helpers
- `src/verp/project.py` — project migration logic
- `src/verp/status.py` — rich-formatted git status display
- `src/verp/claude_terminal.py` — PTY machinery and `cmd_claude`
- `src/verp/pi_terminal.py` — subprocess launcher and `cmd_pi`
- `src/verp/time.py` — time utilities (`now_ms()`)

## Data

All persistent state lives in `DATA_DIR` (`~/.local/share/verp/`):
- `verp.db` — SQLite database (schema version defined by `SCHEMA_VERSION` in `db.py`)
- `issues.db` — issue tracker database (`issues`, `issue_deps`, `issue_events`, plus `issues_fts`, an FTS5 index over title, body, and log/outcome/reason messages, kept in sync by triggers); WAL mode; migrations in `issues/db.py`, run lazily on first `verp issue` use
- `repos/` — central repo store (`REPO_DIR` in `git.py`); not bare — bare clones do not set up `refs/remotes/origin/HEAD`, which `primary_branch()` relies on. Treated as mirrors: `verp pull` fetches then force-checks-out `origin/<primary>` and runs `git clean -fd`, discarding any local state
- `track.sh` — hook handler deployed by migrations
- `claude-settings.json` — Claude hook registration config
- `monitor.pid` — singleton lock file for the agent monitor (JSON `MonitorLock`)
- `debug/` — permission dialog snapshots (`permission-<timestamp>.json`) for alignment debugging
- `claude_dir/` — isolated directory passed to `verp claude` via `--add-dir`; contains `.claude/` with managed skills and CLAUDE.md (`CLAUDE_DIR` in `paths.py`)
- `pi_dir/` — isolated directory passed to `verp pi` via `resources_discover`; contains `skills/` with managed skills (`PI_DIR` in `paths.py`)

User-customizable Claude config lives in `CONFIG_DIR` (`~/.config/verp/`):
- `.claude/` — user-authored skills and CLAUDE.md (`USER_CLAUDE_DIR` in `paths.py`); passed via `--add-dir` if it exists

DB schema migrations run automatically on startup via `init_db()`. Each migration version has a corresponding entry in `_MIGRATIONS` in `db.py`. When adding a new migration, increment `SCHEMA_VERSION` and add an entry to `_MIGRATIONS`.

`claude_dir/` content is versioned separately via `CLAUDE_DIR_VERSION` in `claude_dir.py` and tracked in the `config` table. When updating bundled content in `_claude/`, increment `CLAUDE_DIR_VERSION` and add an entry to `_MIGRATIONS` in `claude_dir.py`.

`pi-extension.ts` and `pi_dir/` are versioned together via `PI_DIR_VERSION` in `pi_dir.py` and tracked in the `config` table. When updating `src/verp/_pi/verp.ts` or any content in `src/verp/_pi/skills/`, increment `PI_DIR_VERSION` and add an entry to `_MIGRATIONS` in `pi_dir.py`.

## Issue tracker

Each issue is owned by one project (`project` column) or is in the backlog (`NULL`). Ownership rules live in `issues/identity.py`:
- The caller is an `Actor` (`kind`, `project`). `kind` is `worker` if `VERP_WORKER` is set, `agent` if `VERP_PID` / `VERP_SOCKET` is set, otherwise `cli` (the human operator).
- Agents and workers are identified by `VERP_PROJECT`, set at launch (empty if launched outside a project); `cli` falls back to the cwd.
- Only the owning project may modify an issue; closed issues are frozen. Only the operator can `reopen`; agents open a follow-up issue instead.
- The operator (not an agent) may bypass ownership when outside a project or with `--force`. Agents can never use `--force`.
- `assign` rejects bodies containing the source project's path or branch (handoff must be self-contained).

Open issues are always owned by an existing project or in the backlog. `verp delete` releases the project's open issues; before every `verp issue` command, `reconcile_orphans` also releases open issues whose project is missing from `verp.db` (skipped if `verp.db` is unreadable or has no projects). Closed issues keep their project name as history.

All changes are recorded in the append-only `issue_events` table, with `actor_kind` and `actor_project` (no session IDs; they are not stable).

## Hook integration

`verp claude` wraps the Claude CLI with a PTY and registers `track.sh` as a hook handler via `claude-settings.json`. `track.sh` calls `verp _claude hook_<event>` for each Claude lifecycle event, which updates the `agents` table.

`PermissionRequest` and `UserPromptSubmit` are synchronous — `verp` blocks and communicates with the PTY wrapper via a Unix socket at `/tmp/verp-<pid>.sock`.

## Agent status values

- `working` — actively using a tool
- `waiting_prompt` — waiting for user input
- `waiting_permission` — waiting for a permission decision
- `asking_question` — Claude is asking the user a question via AskUserQuestion
- `compacting` — the agent is compacting its conversation (pi only)
- `paused` — manually set via the monitor (`p`) to de-emphasize idle agents

Agents can be **pinned** via the monitor (`P`) to sort them to the top. Pinned agents are visually distinguished by bold text on their session ID, directory, and status. Pinning does not affect agent status or `updated_at`.

## Agent kind values

The `agent_type` column on the `agents` table identifies which AI agent is running:

- `claude` — Anthropic Claude (default)
- `pi` — pi coding agent

Agents can be cleared via `verp agent clear <id>` or `Delete` in the monitor. Cleared agents re-appear if they send another status update; clearing is mainly useful for agents that did not properly terminate.
