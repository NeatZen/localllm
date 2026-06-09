"""Playwright smoke test: NeatAi boot, APIs, and chat response."""
import asyncio
import json
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
SESSIONS = ROOT / "data" / "sessions.json"
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:7000"
CHAT_TIMEOUT_MS = 120_000


def _session_token() -> str | None:
    if not SESSIONS.exists():
        return None
    data = json.loads(SESSIONS.read_text(encoding="utf-8"))
    return next(iter(data)) if data else None


async def wait_for_server(url: str, timeout_s: float = 90.0) -> bool:
    import urllib.request

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/version", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        await asyncio.sleep(1.5)
    return False


async def main() -> int:
    if not await wait_for_server(BASE):
        print("FAIL: server not reachable at", BASE)
        return 1

    token = _session_token()
    if not token:
        print("FAIL: no session token in data/sessions.json — log in once first")
        return 1

    failures: list[str] = []
    page_errors: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies([
            {
                "name": "neatai_session",
                "value": token,
                "domain": "127.0.0.1",
                "path": "/",
                "httpOnly": True,
                "sameSite": "Lax",
            }
        ])
        page = await context.new_page()
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        print("== Boot ==")
        await page.goto(f"{BASE}/?nocache={time.time()}", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        boot = await page.evaluate("""async () => {
          const out = { started: !!window.__neataiAppStarted, modules: {} };
          for (const m of ['/static/app.js', '/static/js/chat.js']) {
            try { await import(m + '?t=' + Date.now()); out.modules[m] = 'OK'; }
            catch (e) { out.modules[m] = e.message || String(e); }
          }
          return out;
        }""")
        print("boot", boot)
        if not boot.get("started"):
            failures.append("app did not set __neataiAppStarted")
        for path, status in (boot.get("modules") or {}).items():
            if status != "OK":
                failures.append(f"module load failed: {path}: {status}")

        print("== APIs ==")
        api_checks = await page.evaluate("""async (base) => {
          const checks = {};
          async function get(path) {
            const r = await fetch(base + path, { credentials: 'same-origin' });
            checks[path] = r.status;
            return r;
          }
          await get('/api/auth/status');
          await get('/api/model-endpoints');
          await get('/api/sessions');
          const models = await get('/api/models?refresh=true');
          if (models.ok) {
            const j = await models.json();
            const items = j.items || j.hosts || [];
            checks.model_count = items.reduce((n, it) => n + (it.models || []).length, 0);
          }
          const bundled = await get('/api/bundled-llm/status');
          if (bundled.ok) {
            const j = await bundled.json();
            checks.bundled_healthy = !!(j.healthy || j.state === 'running');
          }
          return checks;
        }""", BASE)
        print("apis", api_checks)
        for path in ("/api/auth/status", "/api/model-endpoints", "/api/sessions"):
            if api_checks.get(path) != 200:
                failures.append(f"{path} returned {api_checks.get(path)}")
        if api_checks.get("/api/auth/status") == 200 and api_checks.get("model_count", 0) == 0:
            failures.append("no models returned from /api/models")

        print("== Chat ==")
        await page.goto(f"{BASE}/?nocache={time.time()}", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)

        msg = page.locator("#message").first
        await msg.wait_for(state="visible", timeout=15000)
        before_ai = await page.locator(".msg-ai").count()
        await msg.fill("Reply with exactly: pong")
        await page.evaluate("""() => {
          const form = document.getElementById('chat-form');
          if (form) form.requestSubmit();
        }""")

        try:
            await page.wait_for_function(
                """([before]) => {
                  const blocked = /processing request|checking model endpoint|still waiting|initializing|endpoint offline/i;
                  const msgs = document.querySelectorAll('.msg-ai .body');
                  if (msgs.length <= before) return false;
                  const last = msgs[msgs.length - 1];
                  const text = (last.textContent || '').trim();
                  if (!text || text.length < 2) return false;
                  if (blocked.test(text)) return false;
                  if (last.querySelector('.spinner, .wave-spinner')) return false;
                  return true;
                }""",
                arg=[before_ai],
                timeout=CHAT_TIMEOUT_MS,
            )
            reply = await page.evaluate("""() => {
              const msgs = document.querySelectorAll('.msg-ai .body');
              const last = msgs[msgs.length - 1];
              return (last.textContent || '').trim().slice(0, 200);
            }""")
            print("chat_reply", reply.encode("ascii", "replace").decode())
            if any(s in reply.lower() for s in ("processing request", "checking model endpoint", "still waiting")):
                failures.append(f"chat stuck on status text: {reply!r}")
            elif len(reply) < 2:
                failures.append(f"empty chat reply: {reply!r}")
        except Exception as e:
            stuck = await page.evaluate("""() => {
              const msgs = document.querySelectorAll('.msg-ai .body');
              const last = msgs.length ? msgs[msgs.length - 1].textContent : '';
              return (last || '').trim().slice(0, 120);
            }""")
            failures.append(f"chat timed out; last AI body: {stuck!r} ({e})")

        if page_errors:
            print("page_errors", page_errors[:5])
            failures.append(f"{len(page_errors)} page error(s): {page_errors[0][:120]}")

        await browser.close()

    if failures:
        print("\nRESULT: FAIL")
        for f in failures:
            print(" -", f.encode("ascii", "replace").decode())
        return 1

    print("\nRESULT: PASS — boot, APIs, and chat OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
