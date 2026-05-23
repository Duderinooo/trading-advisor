import { execSync, spawn } from "node:child_process";
import { openSync } from "node:fs";
import path from "node:path";

export const dynamic = "force-dynamic";

function isLocalhost(req: Request): boolean {
  const host = (req.headers.get("host") ?? "").split(":")[0];
  return host === "localhost" || host === "127.0.0.1" || host === "::1";
}

function findBotPids(): number[] {
  const out = execSync("ps -ax -o pid=,command=", { encoding: "utf8" });
  const pids: number[] = [];
  for (const line of out.split("\n")) {
    if (/caffeinate.*main\.py/.test(line) || /[Pp]ython.*main\.py/.test(line)) {
      const m = line.trim().match(/^(\d+)/);
      if (m) pids.push(Number(m[1]));
    }
  }
  return pids;
}

async function waitGone(pids: number[], maxMs: number): Promise<number[]> {
  const start = Date.now();
  let remaining = [...pids];
  while (remaining.length && Date.now() - start < maxMs) {
    await new Promise((r) => setTimeout(r, 200));
    remaining = remaining.filter((pid) => {
      try {
        process.kill(pid, 0);
        return true;
      } catch {
        return false;
      }
    });
  }
  return remaining;
}

export async function POST(request: Request) {
  if (!isLocalhost(request)) {
    return Response.json({ ok: false, error: "localhost only" }, { status: 403 });
  }

  // 2026-05-23 bot/web split: code lives in <repo>/bot, venv stays at <repo>/venv.
  const repoRoot = path.resolve(process.cwd(), "..");
  const botDir = path.join(repoRoot, "bot");
  const logPath = path.join(botDir, "bot.log");

  const initialPids = findBotPids();
  const termed: number[] = [];
  for (const pid of initialPids) {
    try {
      process.kill(pid, "SIGTERM");
      termed.push(pid);
    } catch {}
  }

  let stragglers = await waitGone(termed, 5000);
  if (stragglers.length) {
    for (const pid of stragglers) {
      try {
        process.kill(pid, "SIGKILL");
      } catch {}
    }
    stragglers = await waitGone(stragglers, 2000);
  }

  // double-check nothing matches anymore before spawning
  const lingering = findBotPids();
  if (lingering.length) {
    return Response.json(
      { ok: false, error: "could not terminate", lingering },
      { status: 500 },
    );
  }

  const fd = openSync(logPath, "a");
  const child = spawn(
    "caffeinate",
    ["-is", path.join(repoRoot, "venv", "bin", "python"), "main.py"],
    {
      cwd: botDir,
      detached: true,
      stdio: ["ignore", fd, fd],
      env: process.env,
    },
  );
  child.unref();

  return Response.json({ ok: true, killed: termed, pid: child.pid });
}
