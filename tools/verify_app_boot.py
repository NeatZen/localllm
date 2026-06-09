import asyncio, sys
from playwright.async_api import async_playwright
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:7001"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(BASE + "/?nocache=" + str(__import__('time').time()), wait_until="networkidle")
        result = await page.evaluate("""async () => {
          const out = {};
          try { await import('/static/js/chat.js?t=' + Date.now()); out.chat = 'OK'; } catch(e) { out.chat = e.message; }
          try { await import('/static/app.js?t=' + Date.now()); out.app = 'OK'; } catch(e) { out.app = e.message; }
          out.started = window.__neataiAppStarted;
          return out;
        }""")
        print(result)
        # test hub click
        if result.get('app') == 'OK':
            await page.wait_for_timeout(2000)
            brain = page.locator('#tool-memory, [data-tool="memory"], .sidebar-tool[data-id="memory"]').first
            if await brain.count():
                await brain.click()
                await page.wait_for_timeout(500)
                modal = await page.locator('#memory-modal.open, #memory-modal.active, #memory-modal:not(.hidden)').count()
                print('memory modal visible:', modal > 0)
        await browser.close()
asyncio.run(main())
