"""Debug: navigate to مستنداتي and screenshot + list all interactive elements."""
import asyncio, sys, logging
sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s', datefmt='%H:%M:%S')

import config
from db_manager import DBManager
from eta_scraper import ETAScraper


async def main():
    db = DBManager()
    db.connect()
    clients = db.get_all_clients()

    # pick first client — change index or name to try different ones
    TARGET = 0   # ← change to 1, 2, 3... to try different clients
    client = clients[TARGET]
    cname  = client['client_name']
    cid    = client['client_id']
    print(f"\nTesting client: {cname}  (id={cid})\n")

    scraper = ETAScraper()
    await scraper.start()

    ctx  = await scraper._new_context(accept_downloads=True)
    page = await ctx.new_page()
    page.set_default_timeout(config.ELEMENT_TIMEOUT)

    ok = await scraper.login(page, client['sap_username'], client['sap_password'], cname)
    if not ok:
        print("Login failed"); return

    # Go home
    await page.goto(config.ETA_HOME_URL, timeout=config.PAGE_TIMEOUT)
    await asyncio.sleep(3)

    # Screenshot: home page
    ss1 = config.SCREENSHOTS_DIR / 'debug_home.png'
    await page.screenshot(path=str(ss1), full_page=True)
    print(f"[HOME] screenshot → {ss1}")

    # List tiles
    tiles = await page.evaluate("""
    () => Array.from(document.querySelectorAll('.sapMGT, [role="option"]')).map(t => ({
        text: (t.innerText || '').trim().replace(/\\n+/g,' ').slice(0,80),
        cls:  t.className.slice(0,50),
    }))
    """)
    print(f"\n[HOME] {len(tiles)} tiles found:")
    for t in tiles:
        print(f"  • {t['text']}")

    # Click مستنداتي
    print("\nClicking مستنداتي tile...")
    clicked = await scraper._click_card(page, config.CARD_LABELS['documents'], cname)
    print(f"  Tile clicked: {clicked}")
    await asyncio.sleep(3)

    # Screenshot: مستنداتي page
    ss2 = config.SCREENSHOTS_DIR / 'debug_mydocs.png'
    await page.screenshot(path=str(ss2), full_page=True)
    print(f"[MYDOCS] screenshot → {ss2}")
    print(f"[MYDOCS] URL: {page.url}")

    # List all rows
    rows = await page.query_selector_all('.sapMLIB')
    print(f"\n[MYDOCS] .sapMLIB rows: {len(rows)}")
    for i, row in enumerate(rows[:5]):
        txt = (await row.inner_text()).strip().replace('\n', ' ')[:100]
        print(f"  row[{i}]: {txt}")

    # List ALL buttons on page
    buttons = await page.evaluate("""
    () => Array.from(document.querySelectorAll('button, a[href]')).map(b => ({
        tag:   b.tagName,
        id:    b.id || '',
        title: b.getAttribute('title') || '',
        label: b.getAttribute('aria-label') || '',
        text:  (b.innerText || '').trim().slice(0,40),
        cls:   b.className.slice(0,50),
    })).filter(b => b.title || b.label || b.text)
    """)
    print(f"\n[MYDOCS] {len(buttons)} buttons/links:")
    for b in buttons[:30]:
        print(f"  [{b['tag']}] title='{b['title']}' label='{b['label']}' text='{b['text']}'")
        print(f"         id='{b['id']}' cls='{b['cls'][:40]}'")

    # Try clicking first row and screenshot detail
    if rows:
        print("\nClicking first row...")
        await rows[0].click()
        await asyncio.sleep(2)
        ss3 = config.SCREENSHOTS_DIR / 'debug_mydocs_detail.png'
        await page.screenshot(path=str(ss3), full_page=True)
        print(f"[DETAIL] screenshot → {ss3}")

        detail_btns = await page.evaluate("""
        () => Array.from(document.querySelectorAll('button, a[href]')).map(b => ({
            tag:   b.tagName,
            title: b.getAttribute('title') || '',
            label: b.getAttribute('aria-label') || '',
            text:  (b.innerText || '').trim().slice(0,40),
            id:    b.id || '',
        })).filter(b => b.title || b.label || b.text)
        """)
        print(f"\n[DETAIL] {len(detail_btns)} buttons/links:")
        for b in detail_btns[:20]:
            print(f"  [{b['tag']}] title='{b['title']}' label='{b['label']}' text='{b['text']}' id='{b['id']}'")

    await page.close()
    await ctx.close()
    await scraper.stop()
    db.close()
    print(f"\nScreenshots saved in: {config.SCREENSHOTS_DIR}")


if __name__ == '__main__':
    asyncio.run(main())
