# verp — agent notes

## After any code change

```bash
uv run black src/ && uv run mypy src/ && uv tool install -e .
```

When adding a new source file, update the file list in both `CLAUDE.md` and `README.md`.

When adding a new agent status value, update the status table in both `CLAUDE.md` and `README.md`.

## Project structure

- `src/verp/cli.py` — all CLI commands and argument parsing
- `src/verp/db.py` — SQLite layer and schema migrations
- `src/verp/git.py` — git subprocess wrappers
- `src/verp/claude_permission_hook.py` — permission dialog and socket communication
- `src/verp/status.py` — rich-formatted git status display
- `src/verp/project.py` — project migration logic
- `src/verp/_versions/` — versioned `track.sh` and `claude_settings.json` per schema version
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

All persistent state lives in `~/.local/share/verp/`:
- `verp.db` — SQLite database (schema version defined by `SCHEMA_VERSION` in `db.py`)
- `repos/` — central repo store (`REPO_DIR` in `git.py`)
- `track.sh` — hook handler deployed by migrations
- `claude-settings.json` — Claude hook registration config

Schema migrations run automatically on startup in `init_internal()`. Each migration version has a corresponding entry in `_MIGRATIONS` in `db.py`. When adding a new migration, increment `SCHEMA_VERSION` and add an entry to `_MIGRATIONS`.

## Hook integration

`verp claude` wraps the Claude CLI with a PTY and registers `track.sh` as a hook handler via `claude-settings.json`. `track.sh` calls `verp _claude hook_<event>` for each Claude lifecycle event, which updates the `agents` table.

`PermissionRequest` and `UserPromptSubmit` are synchronous — `verp` blocks and communicates with the PTY wrapper via a Unix socket at `/tmp/verp-<pid>.sock`.

## Agent status values

- `working` — actively using a tool
- `waiting_prompt` — waiting for user input
- `waiting_permission` — waiting for a permission decision
- `asking_question` — Claude is asking the user a question via AskUserQuestion
