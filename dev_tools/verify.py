"""
verify.py — compare live ETA data vs Oracle for one client
Usage: py verify.py "اسم العميل"
"""

import asyncio
import sys
import oracledb
import config
from eta_scraper import ETAScraper


async def verify(client_name_filter: str):
    pool = oracledb.create_pool(
        user=config.DB_USER, password=config.DB_PASSWORD, dsn=config.DB_DSN,
        min=1, max=2, increment=1,
    )

    col_id   = config.COL_CLIENT_ID or "TO_CHAR(ROWNUM)"
    col_name = config.COL_CLIENT_NAME
    col_un   = config.COL_USERNAME
    col_pw   = config.COL_PASSWORD
    table    = config.CREDENTIALS_TABLE
    filt     = f"AND {config.CREDENTIALS_FILTER}" if config.CREDENTIALS_FILTER else ""

    with pool.acquire() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT {col_id}   AS client_id,
                   {col_name} AS client_name,
                   {col_un}   AS username,
                   {col_pw}   AS password
            FROM   {table}
            WHERE  UPPER({col_name}) LIKE UPPER(:1) {filt}
            FETCH FIRST 1 ROWS ONLY
        """, [f"%{client_name_filter}%"])

        row = cur.fetchone()
        if not row:
            print(f"Client not found: {client_name_filter}")
            pool.close()
            return

        client = {
            'client_id':    str(row[0]),
            'client_name':  row[1],
            'sap_username': row[2],
            'sap_password': row[3],
        }

        cur.execute("""
            SELECT SEVERITY, NOTIF_TYPE, SUBJECT, NOTIF_DATE_STR
            FROM   APEX_NOTIFICATIONS
            WHERE  CLIENT_ID = :1
            ORDER  BY SYNC_DATE DESC
        """, [client['client_id']])
        db_notifs = cur.fetchall()

    print(f"\nClient  : {client['client_name']}")
    print(f"CUST ID : {client['client_id']}")
    print(f"In DB   : {len(db_notifs)} notifications")
    print("-" * 60)

    scraper = ETAScraper()
    await scraper.start()
    try:
        result = await scraper.process_client(client)
    finally:
        await scraper.stop()
    pool.close()

    live   = result.get('notifications', [])
    counts = result.get('counts', {})

    print(f"Live    : {len(live)} notifications")
    print(f"Counts  : {counts}\n")

    print("=== LIVE (from ETA) ===")
    for n in live:
        print(f"  [{n.get('date','')}] {n.get('severity','')} | {n.get('notif_type','')} | {n.get('subject','')[:60]}")

    print("\n=== IN DATABASE ===")
    for r in db_notifs:
        print(f"  [{r[3]}] {r[0]} | {r[1]} | {str(r[2] or '')[:60]}")

    live_subjects = {n.get('subject', '').strip() for n in live}
    db_subjects   = {str(r[2] or '').strip() for r in db_notifs}
    missing       = live_subjects - db_subjects

    print()
    if missing:
        print(f"MISSING in DB ({len(missing)}):")
        for s in missing:
            print(f"  - {s[:80]}")
    else:
        print("OK — all live notifications exist in DB")


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if not name:
        print("Usage: py verify.py \"اسم العميل\"")
        sys.exit(1)
    asyncio.run(verify(name))
