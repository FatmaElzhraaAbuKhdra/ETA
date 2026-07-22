import asyncio
import logging
import sys
import traceback
from collections import defaultdict
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import List, Dict

import config
from db_manager import DBManager
from eta_scraper import ETAScraper, cleanup_old_screenshots
from security import check_file
from pathlib import Path


def setup_logging() -> logging.Logger:
    fmt = '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))

    fh = TimedRotatingFileHandler(
        config.LOG_DIR / 'eta_sync.log', when='midnight',
        backupCount=config.LOG_RETENTION_DAYS, encoding='utf-8',
    )
    fh.setFormatter(logging.Formatter(fmt, '%Y-%m-%d %H:%M:%S'))
    root.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter(fmt, '%Y-%m-%d %H:%M:%S'))
    root.addHandler(ch)
    return logging.getLogger('main')


logger = setup_logging()


# ── Notifications sync ──────────────────────────────────────────

async def process_all_clients(clients: List[Dict], scraper: ETAScraper,
                               db: DBManager, log: List[str]) -> tuple[int, int]:
    sem = asyncio.Semaphore(config.MAX_CONCURRENT)
    success = failure = 0

    async def handle(client: Dict) -> None:
        nonlocal success, failure
        async with sem:
            cname = client.get('client_name') or client.get('sap_username', '')
            try:
                known_hashes = db.get_notification_hashes(client.get('client_id', ''))
                result       = await scraper.process_client(client, known_hashes)
                counts       = result.get('counts', {})
                notifs       = result.get('notifications', [])
                status       = 'SUCCESS' if result['success'] else 'FAILED'

                new_notifs = db.save_notifications(
                    result['client_id'], result['client_name'], notifs
                ) if notifs else 0

                db.save_client_summary(
                    client_id=result['client_id'],
                    client_name=result['client_name'],
                    notifications_count=counts.get('notifications', len(notifs)),
                    new_notifications_count=new_notifs,
                    obligations_count=counts.get('obligations', 0),
                    forms_count=counts.get('forms', 0),
                    docs_count=counts.get('documents', 0),
                    status=status,
                    error_msg=result.get('error', ''),
                )

                if result['success']:
                    success += 1
                    try:
                        db.resolve_sync_error(result['client_id'])
                    except Exception:
                        pass
                    line = (f"OK  [{cname}] notifs={len(notifs)}(+{new_notifs}) "
                            f"obligations={counts.get('obligations',0)} forms={counts.get('forms',0)}")
                else:
                    failure += 1
                    line = f"ERR [{cname}] {result.get('error','')}"
                    if result.get('error_type'):
                        try:
                            db.save_sync_error(
                                client_id=result['client_id'],
                                client_name=result['client_name'],
                                username=client.get('sap_username', ''),
                                step=1,
                                error_type=result['error_type'],
                                error_msg=result.get('error_msg_detail') or result.get('error', ''),
                            )
                        except Exception:
                            pass

                log.append(line)
                logger.info(line)

            except Exception as e:
                failure += 1
                line = f"EXC [{cname}] {traceback.format_exc()}"
                log.append(line)
                logger.error(line)
                try:
                    db.save_client_summary(
                        client_id=client.get('client_id', ''), client_name=cname,
                        notifications_count=0, new_notifications_count=0,
                        obligations_count=0, forms_count=0, docs_count=0,
                        status='FAILED', error_msg=str(e)[:4000],
                    )
                except Exception:
                    pass

    await asyncio.gather(*[handle(c) for c in clients])
    return success, failure


async def run_sync(db: DBManager, scraper: ETAScraper) -> tuple[int, int, int, List[str]]:
    clients = db.get_all_clients()
    if not clients:
        logger.warning("No clients found")
        return 0, 0, 0, []

    logger.info(f"Sync: {len(clients)} clients (concurrency={config.MAX_CONCURRENT})")
    log: List[str] = []
    ok, fail = await process_all_clients(clients, scraper, db, log)
    return len(clients), ok, fail, log


# ── File fetch ──────────────────────────────────────────────────

async def fetch_client_files(client: dict, notifications: list,
                              scraper: ETAScraper, db: DBManager) -> int:
    saved = 0
    cname = client['client_name']

    ctx  = await scraper._new_context()
    page = await ctx.new_page()
    page.set_default_timeout(config.ELEMENT_TIMEOUT)

    logger.info(f"[{cname}] file fetch: checking {len(notifications)} notifications...")
    try:
        if not await scraper.login(page, client['sap_username'], client['sap_password'], cname):
            logger.error(f"[{cname}] file fetch: login failed")
            return 0

        for notif in notifications:
            try:
                files = await scraper.get_notification_attachments(
                    page, cname,
                    notif.get('subject', ''),
                    notif.get('notif_date_str', ''),
                )
                for f in files:
                    dangerous, note = check_file(f['name'], f['content'])
                    db.save_notification_file(
                        notification_id=notif['id'],
                        client_id=notif['client_id'],
                        client_name=notif['client_name'],
                        file_name=f['name'],
                        file_ext=Path(f['name']).suffix.lower(),
                        file_size=len(f['content']),
                        file_content=f['content'],
                        is_dangerous=1 if dangerous else 0,
                        security_note=note,
                    )
                    saved += 1
                    logger.info(f"[{cname}] file saved: {f['name']} {'⚠ FLAGGED' if dangerous else ''}")
            except Exception as e:
                logger.debug(f"[{cname}] notif {notif.get('id')} file error: {e}")
    finally:
        try:
            await page.close()
        except Exception:
            pass
        try:
            await ctx.close()
        except Exception:
            pass

    return saved


async def run_docs_fetch(db: DBManager, scraper: ETAScraper) -> int:
    """Download documents from مستنداتي for all clients."""
    clients = db.get_all_clients()
    if not clients:
        return 0

    sem   = asyncio.Semaphore(config.MAX_CONCURRENT)
    total = 0

    async def handle(client: dict):
        nonlocal total
        async with sem:
            cname = client.get('client_name', '')
            ctx  = await scraper._new_context(accept_downloads=True)
            page = await ctx.new_page()
            page.set_default_timeout(config.ELEMENT_TIMEOUT)
            try:
                if not await scraper.login(page, client['sap_username'], client['sap_password'], cname):
                    logger.warning(f"[{cname}] docs fetch: login failed")
                    return

                known = db.get_client_doc_names(client['client_id'])
                docs  = await scraper.get_client_documents(
                    page, client['client_id'], cname, config.DOCS_DIR, known
                )
                for doc in docs:
                    from security import check_file
                    dangerous, note = check_file(doc['name'])
                    db.save_file(
                        file_path=doc['path'],
                        client_id=client['client_id'],
                        client_name=cname,
                        file_name=doc['name'],
                        file_ext=Path(doc['name']).suffix.lower(),
                        file_size=doc['size'],
                        is_dangerous=1 if dangerous else 0,
                        security_note=note,
                        source='MYDOCS',
                        doc_date=doc.get('doc_date'),
                        expiry_date=doc.get('expiry_date'),
                    )
                total += len(docs)
                if docs:
                    logger.info(f"[{cname}] {len(docs)} docs saved")
            except Exception as e:
                logger.error(f"[{cname}] docs error: {e}")
            finally:
                try: await page.close()
                except Exception: pass
                try: await ctx.close()
                except Exception: pass

    await asyncio.gather(*[handle(c) for c in clients])
    logger.info(f"Docs fetch done — {total} files total")
    return total


async def run_file_fetch(db: DBManager, scraper: ETAScraper) -> int:
    notifications = db.get_notifications_without_files(limit=500)
    if not notifications:
        logger.info("File fetch: no new notifications to process")
        return 0

    logger.info(f"File fetch: {len(notifications)} notifications to check")

    by_client: dict[str, list] = defaultdict(list)
    for n in notifications:
        by_client[n['client_id']].append(n)

    all_clients = {c['client_id']: c for c in db.get_all_clients()}
    sem   = asyncio.Semaphore(config.MAX_CONCURRENT)
    total = 0

    async def handle(cid: str, notifs: list):
        nonlocal total
        async with sem:
            client = all_clients.get(cid)
            if not client:
                return
            n = await fetch_client_files(client, notifs, scraper, db)
            total += n
            db.mark_notifications_checked([notif['id'] for notif in notifs])

    await asyncio.gather(*[handle(cid, n) for cid, n in by_client.items()])
    logger.info(f"File fetch done — {total} files saved")
    return total


# ── Main entry point ────────────────────────────────────────────

async def run_all() -> bool:
    start = datetime.now()
    logger.info(f"{'='*55}")
    logger.info(f"ETA Sync started — {start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"{'='*55}")

    db      = DBManager()
    scraper = ETAScraper()

    try:
        cleanup_old_screenshots()   # مسح screenshots الأقدم من LOG_RETENTION_DAYS
        db.connect()
        db.create_tables()
        await scraper.start()

        # Step 1: sync notifications
        logger.info("Step 1/3 — Syncing notifications...")
        total, ok, fail, log = await run_sync(db, scraper)

        # Step 2: download notification attachments
        logger.info("Step 2/3 — Fetching notification attachments...")
        files_saved = await run_file_fetch(db, scraper)

        # Step 3: download documents from مستنداتي for every client
        logger.info("Step 3/3 — Downloading مستنداتي documents...")
        files_saved += await run_docs_fetch(db, scraper)

        end      = datetime.now()
        duration = (end - start).total_seconds()
        logger.info(f"{'='*55}")
        logger.info(f"Done in {duration:.0f}s — {ok}/{total} clients OK | {fail} failed | {files_saved} files saved")
        logger.info(f"{'='*55}")

        db.save_sync_log(
            sync_start=start, sync_end=end,
            total_clients=total, success_count=ok, failure_count=fail,
            log_details='\n'.join(log),
        )
        return True

    except Exception as e:
        logger.critical(f"Sync crashed: {e}", exc_info=True)
        try:
            db.save_sync_log(start, datetime.now(), 0, 0, 1, f"CRASH: {e}")
        except Exception:
            pass
        return False

    finally:
        try:
            await scraper.stop()
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass


def main() -> int:
    logger.info(f"Python {sys.version.split()[0]} | {config.BASE_DIR}")
    try:
        return 0 if asyncio.run(run_all()) else 1
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        logger.critical(f"Fatal: {e}", exc_info=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
