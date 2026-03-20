# verp — agent notes

## After any code change

```bash
uv run black src/ && uv run mypy src/ && uv tool install -e .
```

When adding a new source file, update the file list in both `CLAUDE.md` and `README.md`.

When adding a new agent status value, update the status table in both `CLAUDE.md` and `README.md`.

When adding or removing anything stored in `DATA_DIR`, update the Data section in both `CLAUDE.md` and `README.md`.

## Project structure

- `src/verp/cli.py` — all CLI commands and argument parsing
- `src/verp/monitor.py` — interactive agent monitor TUI (prompt_toolkit)
- `src/verp/db.py` — SQLite layer and schema migrations
- `src/verp/git.py` — git subprocess wrappers
- `src/verp/paths.py` — all path constants (`DATA_DIR`, `CLAUDE_DIR`, `CONFIG_DIR`, `USER_CLAUDE_DIR`)
- `src/verp/claude_dir.py` — managed `CLAUDE_DIR` content versioning and sync
- `src/verp/claude_permission_hook.py` — permission dialog and socket communication
- `src/verp/status.py` — rich-formatted git status display
- `src/verp/project.py` — project migration logic
- `src/verp/_versions/` — versioned `track.sh` and `claude_settings.json` per schema version
- `_claude/` — bundled managed Claude config (skills, CLAUDE.md); symlinked into package as `src/verp/_claude`
- `src/verp/focus/` — terminal window focus module
  - `__init__.py` — public API: `focus_by_tty(tty)`, `pid_to_tty(pid)`
  - `_base.py` — `TerminalFocuser` protocol
  - `_proc.py` — shared constants (`TERMINAL_EMULATORS`), `pid_to_tty`, and focuser dispatch
  - `_focusers/_macos.py` — macOS focuser via pyobjc/osascript
  - `_focusers/_linux_x11.py` — Linux X11 focuser via ewmh/xdotool
  - `_focusers/_wezterm.py` — WezTerm CLI focuser
  - `_focusers/_kitty.py` — kitty remote control focuser
  - `_focusers/_tmux.py` — tmux pane focuser

## Data

All persistent state lives in `DATA_DIR` (`~/.local/share/verp/`):
- `verp.db` — SQLite database (schema version defined by `SCHEMA_VERSION` in `db.py`)
- `repos/` — central repo store (`REPO_DIR` in `git.py`); not bare — bare clones do not set up `refs/remotes/origin/HEAD`, which `primary_branch()` relies on
- `track.sh` — hook handler deployed by migrations
- `claude-settings.json` — Claude hook registration config
- `monitor.pid` — singleton lock file for the agent monitor (`pid:tty` format)
- `claude_dir/` — isolated directory passed to `verp claude` via `--add-dir`; contains `.claude/` with managed skills and CLAUDE.md (`CLAUDE_DIR` in `paths.py`)

User-customizable Claude config lives in `CONFIG_DIR` (`~/.config/verp/`):
- `.claude/` — user-authored skills and CLAUDE.md (`USER_CLAUDE_DIR` in `paths.py`); passed via `--add-dir` if it exists

DB schema migrations run automatically on startup via `init_db()`. Each migration version has a corresponding entry in `_MIGRATIONS` in `db.py`. When adding a new migration, increment `SCHEMA_VERSION` and add an entry to `_MIGRATIONS`.

`claude_dir/` content is versioned separately via `CLAUDE_DIR_VERSION` in `claude_dir.py` and tracked in the `config` table. When updating bundled content in `_claude/`, increment `CLAUDE_DIR_VERSION` and add an entry to `_MIGRATIONS` in `claude_dir.py`.

## Hook integration

`verp claude` wraps the Claude CLI with a PTY and registers `track.sh` as a hook handler via `claude-settings.json`. `track.sh` calls `verp _claude hook_<event>` for each Claude lifecycle event, which updates the `agents` table.

`PermissionRequest` and `UserPromptSubmit` are synchronous — `verp` blocks and communicates with the PTY wrapper via a Unix socket at `/tmp/verp-<pid>.sock`.

## Agent status values

- `working` — actively using a tool
- `waiting_prompt` — waiting for user input
- `waiting_permission` — waiting for a permission decision
- `asking_question` — Claude is asking the user a question via AskUserQuestion
- `paused` — manually set via the monitor (`p`) to de-emphasize idle agents

Agents can be cleared via `verp agent clear <id>` or `Delete` in the monitor. Cleared agents re-appear if they send another status update; clearing is mainly useful for agents that did not properly terminate.
