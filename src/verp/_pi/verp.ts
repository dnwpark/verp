/**
 * Verp lifecycle tracking extension for pi.
 *
 * Fires `verp _pi hook_*` subcommands on session and agent lifecycle events,
 * keeping the verp agents table up to date for the monitor.
 *
 * Deployed to DATA_DIR/pi-extension.ts by verp's init_pi_dir().
 * Loaded via `pi --extension DATA_DIR/pi-extension.ts` by `verp pi`.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { basename, extname, join } from "node:path";

export default function (pi: ExtensionAPI) {
  const verpPid = process.env.VERP_PID ?? "";

  function hook(subcommand: string, ...args: string[]) {
    try {
      execFileSync("verp", ["_pi", subcommand, ...args, String(Date.now())], {
        env: { ...process.env, VERP_PID: verpPid },
        stdio: "ignore",
      });
    } catch {}
  }

  function sessionId(ctx: {
    sessionManager: { getSessionFile(): string | undefined };
  }): string {
    const file = ctx.sessionManager.getSessionFile();
    if (!file) return "";
    return basename(file, extname(file)); // <uuid>.jsonl → <uuid>
  }

  pi.on("session_start", async (_event, ctx) => {
    const id = sessionId(ctx);
    if (id) hook("hook_session_start", id);
  });

  pi.on("session_shutdown", async (_event, ctx) => {
    const id = sessionId(ctx);
    if (id) hook("hook_session_end", id);
  });

  pi.on("agent_start", async (_event, ctx) => {
    const id = sessionId(ctx);
    if (id) hook("hook_agent_start", id, ctx.cwd);
  });

  pi.on("agent_settled", async (_event, ctx) => {
    const id = sessionId(ctx);
    if (id) hook("hook_agent_settled", id, ctx.cwd);
  });

  pi.on("tool_call", async (event, ctx) => {
    const id = sessionId(ctx);
    if (id) hook("hook_tool_call", id, ctx.cwd, event.toolName);
  });

  pi.on("tool_result", async (event, ctx) => {
    const id = sessionId(ctx);
    if (id) hook("hook_tool_result", id, ctx.cwd, event.toolName);
  });

  pi.on("session_before_compact", async (_event, ctx) => {
    const id = sessionId(ctx);
    if (id) hook("hook_compact_start", id, ctx.cwd);
  });

  pi.on("session_compact", async (event, ctx) => {
    const id = sessionId(ctx);
    if (id) hook("hook_compact_end", id, ctx.cwd, event.reason);
  });

  // Managed skills directory: DATA_DIR/pi_dir/skills/
  // DATA_DIR matches verp's paths.py: $VERP_DATA_DIR or ~/.local/share/verp
  const dataDir =
    process.env.VERP_DATA_DIR ??
    join(process.env.HOME ?? "", ".local", "share", "verp");
  const piManagedDir = join(dataDir, "pi_dir");

  pi.on("resources_discover", async (_event, _ctx) => {
    const skillsPath = join(piManagedDir, "skills");
    if (existsSync(skillsPath)) {
      return { skillPaths: [skillsPath] };
    }
    return {};
  });

  pi.registerShortcut("ctrl+\\", {
    description: "Jump to verp monitor",
    handler: async (ctx) => {
      try {
        // Only registers the agent as waiting_prompt if not already present;
        // leaves status unchanged if already registered.
        const id = sessionId(ctx);
        if (id) hook("hook_jump", id, ctx.cwd);
        execFileSync("verp", ["agent", "monitor"], {
          stdio: "ignore",
        });
      } catch {}
    },
  });
}
