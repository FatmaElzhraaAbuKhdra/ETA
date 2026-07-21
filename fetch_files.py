"""
fetch_files.py — download form attachments for existing notifications in DB
Usage: py fetch_files.py [--limit 50]
"""

import asyncio
import logging
import sys
from pathlib import Path
from collections import defaultdict

import oracledb

import config
from db_manager import DBManager
from eta_scraper import ETAScraper
from security import check_file

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger('fetch_files')


async def fetch_files_for_client(
    client: dict,
    notifications: list,
    scraper: ETAScraper,
    db: DBManager,
) -> tuple[int, int]:
    """Login to client, navigate to each notification, download attachments."""
    saved = failed = 0
    cname = client['client_name']

    ctx  = await scraper._new_context()
    page = await ctx.new_page()
    page.set_default_timeout(config.ELEMENT_TIMEOUT)

    try:
        if not await scraper.login(page, client['sap_username'], client['sap_password'], cname):
            logger.error(f"[{cname}] login failed")
            return 0, len(notifications)

        for notif in notifications:
            try:
                files = await scraper.get_notification_attachments(
                    page, cname, notif['subject'], notif['notif_date_str']
                )
                for f in files:
                    is_dangerous, note = check_file(f['name'], f['content'])
                    db.save_notification_file(
                        notification_id=notif['id'],
                        client_id=notif['client_id'],
                        client_name=notif['client_name'],
                        file_name=f['name'],
                        file_ext=Path(f['name']).suffix.lower(),
                        file_size=len(f['content']),
                        file_content=f['content'],
                        is_dangerous=1 if is_dangerous else 0,
                        security_note=note,
                    )
                    saved += 1
            except Exception as e:
                logger.error(f"[{cname}] notification {notif['id']} failed: {e}")
                failed += 1

    finally:
        try:
            await page.close()
        except Exception:
            pass
        try:
            await ctx.close()
        except Exception:
            pass

    return saved, failed


async def run(limit: int = 200):
    db = DBManager()
    db.connect()
    db.create_tables()

    notifications = db.get_notifications_without_files(limit)
    if not notifications:
        logger.info("No notifications without files — nothing to do")
        db.close()
        return

    logger.info(f"Found {len(notifications)} notifications to process")

    # group by client_id
    by_client: dict[str, list] = defaultdict(list)
    for n in notifications:
        by_client[n['client_id']].append(n)

    # get client credentials
    all_clients = {c['client_id']: c for c in db.get_all_clients()}

    scraper = ETAScraper()
    await scraper.start()

    total_saved = total_failed = 0
    sem = asyncio.Semaphore(config.MAX_CONCURRENT)

    async def handle_client(cid: str, notifs: list):
        nonlocal total_saved, total_failed
        async with sem:
            client = all_clients.get(cid)
            if not client:
                logger.warning(f"Client {cid} not found in credentials")
                return
            s, f = await fetch_files_for_client(client, notifs, scraper, db)
            total_saved  += s
            total_failed += f
            logger.info(f"[{client['client_name']}] files: {s} saved, {f} failed")

    await asyncio.gather(*[handle_client(cid, notifs) for cid, notifs in by_client.items()])

    await scraper.stop()
    db.close()
    logger.info(f"Done — {total_saved} files saved, {total_failed} failed")


if __name__ == '__main__':
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    asyncio.run(run(limit))
