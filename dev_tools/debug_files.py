"""
Run: py debug_files.py
Tests the exact navigation path that works during sync,
then tries to get attachment elements from a notification detail.
"""
import asyncio
import sys
sys.stdout.reconfigure(encoding='utf-8')

import config
from db_manager import DBManager
from eta_scraper import ETAScraper, _wait_for_ui5
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])


async def dump_page(page, label: str):
    url = page.url
    rows = await page.evaluate("() => document.querySelectorAll('.sapMLIB').length")
    listitems = await page.evaluate("() => document.querySelectorAll('[role=\"listitem\"]').length")
    buttons   = await page.evaluate("""() => [...document.querySelectorAll('button,[role="button"]')]
        .map(e => (e.innerText||e.title||e.getAttribute('aria-label')||'').trim().slice(0,40))
        .filter(Boolean)""")
    print(f"\n--- {label} ---")
    print(f"URL       : {url}")
    print(f".sapMLIB  : {rows}")
    print(f"listitem  : {listitems}")
    print(f"buttons   : {buttons[:15]}")


async def main():
    db = DBManager()
    db.connect()

    notifs  = db.get_notifications_without_files(limit=3)
    clients = {c['client_id']: c for c in db.get_all_clients()}

    if not notifs:
        print("لا توجد إشعارات بدون ملفات"); return

    notif  = notifs[0]
    client = clients.get(notif['client_id'])
    if not client:
        print("عميل مش موجود"); return

    print(f"العميل: {client['client_name']}")
    print(f"الإشعار: {notif['subject']}")

    scraper = ETAScraper()
    await scraper.start()
    ctx  = await scraper._new_context()
    page = await ctx.new_page()
    page.set_default_timeout(config.ELEMENT_TIMEOUT)

    try:
        if not await scraper.login(page, client['sap_username'], client['sap_password'], client['client_name']):
            print("فشل تسجيل الدخول"); return
        print("Login OK")

        # Step 1: make sure we're on home
        await page.goto(config.ETA_HOME_URL, timeout=config.PAGE_TIMEOUT)
        await _wait_for_ui5(page, timeout=20000)
        await asyncio.sleep(2)
        await dump_page(page, "Home page")
        await page.screenshot(path='dbg_1_home.png', full_page=False)

        # Step 2: check home tiles
        tiles = await page.evaluate("""() => {
            const t = [...document.querySelectorAll('.sapMGT, [class*="GenericTile"]')];
            return t.map(e => ({ class: e.className.slice(0,60), text: (e.innerText||'').slice(0,60) }));
        }""")
        print(f"\nHome tiles ({len(tiles)}):")
        for t in tiles: print(" ", t)

        # Step 3: click notifications tile exactly like get_notifications() does
        clicked_tile = False
        for label in config.CARD_LABELS['notifications']:
            for strategy in [
                f'text="{label}"',
                f'[role="option"]:has-text("{label}")',
                f'.sapMGT:has-text("{label}")',
                f'div:has-text("{label}"):not(:has(div:has-text("{label}")))',
            ]:
                try:
                    el = await page.wait_for_selector(strategy, timeout=3000, state='visible')
                    if el:
                        print(f"\nClicking tile via: {strategy}")
                        await el.click()
                        await asyncio.sleep(3)
                        await _wait_for_ui5(page, timeout=15000)
                        clicked_tile = True
                        break
                except Exception:
                    continue
            if clicked_tile:
                break

        if not clicked_tile:
            print("\nTile not found — using hash navigation")
            await page.evaluate("() => { window.location.hash = '/messages/AL/%201'; }")
            await asyncio.sleep(4)
            await _wait_for_ui5(page, timeout=15000)

        await dump_page(page, "After tile click")
        await page.screenshot(path='dbg_2_after_tile.png', full_page=False)

        # Step 4: wait for rows with a longer timeout
        print("\nWaiting for .sapMLIB rows (30s)...")
        try:
            await page.wait_for_selector('.sapMLIB', timeout=30000)
            print("Rows appeared!")
        except Exception:
            print("Rows never appeared")

        await dump_page(page, "After waiting for rows")
        await page.screenshot(path='dbg_3_rows.png', full_page=False)

        rows = await page.evaluate("""() => {
            return [...document.querySelectorAll('.sapMLIB')].slice(0,5).map(r => ({
                text: (r.innerText||'').slice(0,100)
            }));
        }""")
        print(f"\nFirst 5 rows: {rows}")

        # Step 5: click first row and dump detail
        if rows:
            await page.evaluate("() => document.querySelectorAll('.sapMLIB')[0].click()")
            await asyncio.sleep(2)
            await _wait_for_ui5(page, timeout=10000)
            await page.screenshot(path='dbg_4_detail.png', full_page=False)

            links = await page.evaluate("""() => {
                return [...document.querySelectorAll('a,button,[role="button"]')]
                    .map(e => ({
                        tag: e.tagName,
                        href: (e.href||e.getAttribute('href')||'').slice(0,100),
                        text: (e.innerText||e.title||'').slice(0,60).trim(),
                        cls:  e.className.slice(0,60)
                    }))
                    .filter(e => e.text || e.href);
            }""")
            print(f"\nDetail page elements ({len(links)}):")
            for l in links: print(" ", l)

            # save HTML
            html = await page.content()
            with open('dbg_detail.html', 'w', encoding='utf-8') as f:
                f.write(html)
            print("\nFull HTML saved: dbg_detail.html")

    finally:
        await page.close()
        await ctx.close()
        await scraper.stop()
        db.close()


asyncio.run(main())
