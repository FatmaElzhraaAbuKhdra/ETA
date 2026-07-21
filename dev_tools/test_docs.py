"""Quick test: download مستنداتي documents for the FIRST client that has docs_count > 0."""
import asyncio, sys, logging
sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)],
)

import config
from db_manager import DBManager
from eta_scraper import ETAScraper
from pathlib import Path
import re, shutil


async def main():
    db = DBManager()
    db.connect()

    # pick first N clients — change MAX_CLIENTS to test more
    MAX_CLIENTS = 3
    clients = db.get_all_clients()[:MAX_CLIENTS]
    if not clients:
        print("No clients found")
        return

    scraper = ETAScraper()
    await scraper.start()

    for client in clients:
        cname = client['client_name']
        cid   = client['client_id']
        print(f"\n{'='*60}")
        print(f"Testing: {cname}  (id={cid})")
        print(f"{'='*60}")

        # create a context WITH downloads enabled
        ctx  = await scraper._new_context(accept_downloads=True)
        page = await ctx.new_page()
        page.set_default_timeout(config.ELEMENT_TIMEOUT)

        try:
            ok = await scraper.login(page, client['sap_username'], client['sap_password'], cname)
            if not ok:
                print(f"  [SKIP] Login failed")
                continue

            known = db.get_client_doc_names(cid)
            docs  = await scraper.get_client_documents(
                page, cid, cname, config.DOCS_DIR, known
            )

            if docs:
                print(f"\n  ✓ Downloaded {len(docs)} file(s):")
                for d in docs:
                    from security import check_file
                    from pathlib import Path
                    dangerous, note = check_file(d['name'])
                    db.save_file(
                        file_path=d['path'],
                        client_id=cid,
                        client_name=cname,
                        file_name=d['name'],
                        file_ext=Path(d['name']).suffix.lower(),
                        file_size=d['size'],
                        is_dangerous=1 if dangerous else 0,
                        security_note=note,
                        source='MYDOCS',
                    )
                    print(f"    • {d['name']}  ({d['size']:,} bytes)  → DB ✓")
            else:
                print(f"  (no documents found for this client)")

        except Exception as e:
            print(f"  [ERROR] {e}")
            import traceback; traceback.print_exc()
        finally:
            await page.close()
            await ctx.close()

    await scraper.stop()
    db.close()
    print(f"\nDone. Check folder: {config.DOCS_DIR}")


if __name__ == '__main__':
    asyncio.run(main())
