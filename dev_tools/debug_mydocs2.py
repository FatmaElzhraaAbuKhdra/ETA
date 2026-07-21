"""Debug v2: click the < arrow inside each row and capture network requests."""
import asyncio, sys, logging
sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s', datefmt='%H:%M:%S')

import config
from db_manager import DBManager
from eta_scraper import ETAScraper

DOWNLOADS = []

async def main():
    db = DBManager()
    db.connect()
    clients = db.get_all_clients()
    client = clients[0]
    cname  = client['client_name']
    print(f"\nClient: {cname}\n")

    scraper = ETAScraper()
    await scraper.start()

    ctx  = await scraper._new_context(accept_downloads=True)
    page = await ctx.new_page()
    page.set_default_timeout(config.ELEMENT_TIMEOUT)

    # Intercept all network requests to spot file download URLs
    download_urls = []
    def on_response(response):
        ct = response.headers.get('content-type', '')
        if any(t in ct for t in ['pdf', 'octet', 'excel', 'word', 'zip']):
            download_urls.append(response.url)
            print(f"  [NET] file response: {response.status} | {ct[:60]} | {response.url[:100]}")

    page.on('response', on_response)

    ok = await scraper.login(page, client['sap_username'], client['sap_password'], cname)
    if not ok: print("Login failed"); return

    await page.goto(config.ETA_HOME_URL, timeout=config.PAGE_TIMEOUT)
    await asyncio.sleep(3)
    await scraper._click_card(page, config.CARD_LABELS['documents'], cname)
    await asyncio.sleep(3)

    rows = await page.query_selector_all('.sapMLIB')
    print(f"Rows found: {len(rows)}")

    for i, row in enumerate(rows):
        txt = (await row.inner_text()).strip().replace('\n',' ')[:80]
        print(f"\n--- Row {i}: {txt}")

        # Get ALL elements inside this row
        inner = await row.evaluate("""el => {
            const items = [];
            el.querySelectorAll('*').forEach(child => {
                const tag = child.tagName;
                const cls = child.className || '';
                const role = child.getAttribute('role') || '';
                const txt = (child.innerText||'').trim().slice(0,30);
                if (tag === 'BUTTON' || tag === 'A' || role === 'button' ||
                    cls.includes('Nav') || cls.includes('Arrow') ||
                    cls.includes('Detail') || cls.includes('Icon')) {
                    items.push({tag, cls: cls.slice(0,60), role, txt});
                }
            });
            return items;
        }""")
        print(f"  Inner clickables: {len(inner)}")
        for el in inner:
            print(f"    {el['tag']} cls='{el['cls']}' role='{el['role']}' txt='{el['txt']}'")

        # Try clicking navigation arrow (< icon) — SAP Fiori patterns
        arrow_clicked = False
        for sel in [
            '.sapMLIBIconNavCol', '.sapMLIBIcon', '.sapUiIcon',
            '[class*="NavCol"]', '[class*="Arrow"]',
            'span[class*="Icon"]', 'div[class*="Icon"]',
        ]:
            try:
                arrow = await row.query_selector(sel)
                if arrow:
                    print(f"  Found arrow: {sel}")
                    # Intercept download if it triggers one
                    try:
                        async with page.expect_download(timeout=5000) as dl_info:
                            await arrow.click()
                        dl = await dl_info.value
                        tmp = await dl.path()
                        print(f"  *** DOWNLOAD triggered! file={dl.suggested_filename} path={tmp}")
                        arrow_clicked = True
                        break
                    except Exception:
                        # No download — check if page changed
                        await asyncio.sleep(2)
                        ss = config.SCREENSHOTS_DIR / f'debug_row{i}_after_arrow.png'
                        await page.screenshot(path=str(ss))
                        print(f"  Screenshot after arrow click → {ss}")
                        arrow_clicked = True
                        break
            except Exception as e:
                print(f"  Arrow click {sel} failed: {e}")

        if not arrow_clicked:
            # Try clicking the whole row and check for download
            print("  No arrow found — trying row click + download intercept")
            try:
                async with page.expect_download(timeout=5000) as dl_info:
                    await row.click()
                dl = await dl_info.value
                tmp = await dl.path()
                print(f"  *** DOWNLOAD from row click! file={dl.suggested_filename}")
            except Exception:
                await row.click()
                await asyncio.sleep(2)
                ss = config.SCREENSHOTS_DIR / f'debug_row{i}_rowclick.png'
                await page.screenshot(path=str(ss))
                print(f"  Screenshot after row click → {ss}")

        # Check new buttons after click
        new_btns = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('button, a')).map(b => ({
                title: b.getAttribute('title')||'',
                text: (b.innerText||'').trim().slice(0,30),
            })).filter(b => b.title || b.text)
        """)
        print(f"  Buttons on page now ({len(new_btns)}):")
        for b in new_btns:
            if b['title'] or b['text']:
                print(f"    title='{b['title']}' text='{b['text']}'")

        # Go back to list for next row
        for bsel in ['[title="التنقل للخلف"]', '.sapMNavButton', 'button[title*="Back"]']:
            try:
                btn = await page.query_selector(bsel)
                if btn:
                    await btn.click()
                    await asyncio.sleep(1)
                    await page.wait_for_selector('.sapMLIB', timeout=5000)
                    break
            except Exception:
                pass

        if i >= 1:  # test first 2 rows only
            break

    print(f"\nNetwork file URLs captured: {download_urls}")
    await page.close()
    await ctx.close()
    await scraper.stop()
    db.close()


if __name__ == '__main__':
    asyncio.run(main())
