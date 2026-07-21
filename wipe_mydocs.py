import sys
sys.path.insert(0, r"D:\New version\Ai\eta_sync")
import oracledb, config

conn = oracledb.connect(user=config.DB_USER, password=config.DB_PASSWORD, dsn=config.DB_DSN)
cur = conn.cursor()

cur.execute("DELETE FROM APEX_NOTIFICATION_FILES WHERE SOURCE = 'MYDOCS'")
print(f"Deleted MYDOCS files: {cur.rowcount}")

cur.execute("UPDATE APEX_NOTIFICATIONS SET FILES_CHECKED_AT = NULL")
print(f"Reset FILES_CHECKED_AT: {cur.rowcount} notifications")

conn.commit()
conn.close()
print("Done.")
