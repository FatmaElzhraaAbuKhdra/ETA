import sys, subprocess
sys.path.insert(0, r"D:\New version\Ai\eta_sync")
import oracledb, config

conn = oracledb.connect(user=config.DB_USER, password=config.DB_PASSWORD, dsn=config.DB_DSN)
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM APEX_NOTIFICATION_FILES WHERE SOURCE='MYDOCS'")
total = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM APEX_NOTIFICATION_FILES WHERE SOURCE='MYDOCS' AND DOC_DATE IS NOT NULL")
with_date = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM APEX_NOTIFICATIONS WHERE FILES_CHECKED_AT IS NULL")
unchecked = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM APEX_NOTIFICATIONS WHERE FILES_CHECKED_AT IS NOT NULL")
checked = cur.fetchone()[0]
cur.execute("SELECT FILE_NAME, DOC_DATE, EXPIRY_DATE FROM APEX_NOTIFICATION_FILES WHERE SOURCE='MYDOCS' AND DOC_DATE IS NOT NULL FETCH FIRST 5 ROWS ONLY")
samples = cur.fetchall()
conn.close()

print(f"MYDOCS total: {total}")
print(f"MYDOCS with DOC_DATE: {with_date}")
print(f"MYDOCS missing DOC_DATE: {total - with_date}")
print(f"Notifications checked: {checked} / unchecked: {unchecked}")
if samples:
    print("Sample rows with dates:")
    for r in samples:
        print(f"  {r[0]} | doc={r[1]} | exp={r[2]}")
