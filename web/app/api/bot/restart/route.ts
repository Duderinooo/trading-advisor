import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

export const dynamic = "force-dynamic";

// Restart via launchctl, not raw spawn. Spawning a second `python main.py`
// while launchd KeepAlive=true respawns its own = two pollers on the same
// Telegram token = `terminated by other getUpdates request` spam.
// launchctl unload+load is atomic against launchd — no race.
const PLIST_PATH = path.join(
  process.env.HOME ?? "/Users/malteollmann",
  "Library/LaunchAgents/com.trading.advisor.plist",
);

function isLocalhost(req: Request): boolean {
  const host = (req.headers.get("host") ?? "").split(":")[0];
  return host === "localhost" || host === "127.0.0.1" || host === "::1";
}

function sh(cmd: string): { ok: boolean; out: string } {
  try {
    const out = execSync(cmd, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
    return { ok: true, out };
  } catch (e) {
    const err = e as { stderr?: Buffer; message?: string };
    return { ok: false, out: err.stderr?.toString() ?? err.message ?? "" };
  }
}

export async function POST(request: Request) {
  if (!isLocalhost(request)) {
    return Response.json({ ok: false, error: "localhost only" }, { status: 403 });
  }
  if (!existsSync(PLIST_PATH)) {
    return Response.json(
      { ok: false, error: `plist missing: ${PLIST_PATH}` },
      { status: 500 },
    );
  }

  // Unload sends SIGTERM to the bot + caffeinate wrappers. Errors silently
  // when not loaded — that's fine, we want it loaded after.
  const unload = sh(`launchctl unload "${PLIST_PATH}"`);

  // launchd ThrottleInterval=10s. Wait long enough so reload doesn't race
  // with launchd's own KeepAlive respawn.
  await new Promise((r) => setTimeout(r, 1500));

  const load = sh(`launchctl load "${PLIST_PATH}"`);
  if (!load.ok) {
    return Response.json(
      { ok: false, error: "launchctl load failed", stderr: load.out },
      { status: 500 },
    );
  }

  return Response.json({
    ok: true,
    method: "launchctl",
    unload_stderr: unload.ok ? null : unload.out,
  });
}
