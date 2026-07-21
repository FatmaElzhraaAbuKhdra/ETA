import hashlib
import logging
import re
from datetime import datetime, date
from contextlib import contextmanager
from pathlib import Path
from typing import List, Dict, Any, Optional


def _parse_date(s) -> Optional[date]:
    if not s:
        return None
    if isinstance(s, date):
        return s
    for fmt in ('%d/%m/%Y', '%d/%m/%y', '%Y/%m/%d', '%d-%m-%Y', '%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    return None

import oracledb
import config
from crypto_utils import decrypt_password

logger = logging.getLogger(__name__)

DDL_CLIENT_SUMMARY = """
CREATE TABLE APEX_CLIENT_SUMMARY (
    ID                      NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    CLIENT_ID               VARCHAR2(100 CHAR),
    CLIENT_NAME             VARCHAR2(500 CHAR),
    SYNC_DATE               TIMESTAMP DEFAULT SYSTIMESTAMP,
    NOTIFICATIONS_COUNT     NUMBER DEFAULT 0,
    NEW_NOTIFICATIONS_COUNT NUMBER DEFAULT 0,
    OBLIGATIONS_COUNT       NUMBER DEFAULT 0,
    FORMS_COUNT             NUMBER DEFAULT 0,
    DOCS_COUNT              NUMBER DEFAULT 0,
    STATUS                  VARCHAR2(20),
    ERROR_MSG               VARCHAR2(4000 CHAR)
)
"""

DDL_NOTIFICATIONS = """
CREATE TABLE APEX_NOTIFICATIONS (
    ID                NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    CLIENT_ID         VARCHAR2(100 CHAR),
    CLIENT_NAME       VARCHAR2(500 CHAR),
    SEVERITY          VARCHAR2(100 CHAR),
    NOTIF_TYPE        VARCHAR2(500 CHAR),
    SUBJECT           VARCHAR2(2000 CHAR),
    MESSAGE_BODY      CLOB,
    NOTIF_DATE_STR    VARCHAR2(100),
    READ_STATUS       VARCHAR2(50 CHAR),
    FIRST_SYNC_DATE   TIMESTAMP DEFAULT SYSTIMESTAMP,
    LAST_SYNC_DATE    TIMESTAMP DEFAULT SYSTIMESTAMP,
    IS_NEW              NUMBER(1) DEFAULT 1,
    NOTIFICATION_HASH   VARCHAR2(64),
    FILES_CHECKED_AT    TIMESTAMP,
    CONSTRAINT UQ_NOTIF_HASH UNIQUE (NOTIFICATION_HASH)
)
"""

DDL_NOTIFICATION_FILES = """
CREATE TABLE APEX_NOTIFICATION_FILES (
    ID              NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    NOTIFICATION_ID NUMBER,
    CLIENT_ID       VARCHAR2(100 CHAR),
    CLIENT_NAME     VARCHAR2(500 CHAR),
    FILE_NAME       VARCHAR2(500 CHAR),
    FILE_EXT        VARCHAR2(20),
    FILE_SIZE       NUMBER DEFAULT 0,
    FILE_PATH       VARCHAR2(2000 CHAR),
    IS_DANGEROUS    NUMBER(1) DEFAULT 0,
    SECURITY_NOTE   VARCHAR2(1000 CHAR),
    SOURCE          VARCHAR2(50) DEFAULT 'NOTIFICATION',
    DOC_DATE        DATE,
    EXPIRY_DATE     DATE,
    DOWNLOAD_DATE   TIMESTAMP DEFAULT SYSTIMESTAMP
)
"""

DDL_SYNC_LOG = """
CREATE TABLE APEX_SYNC_LOG (
    ID            NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    SYNC_START    TIMESTAMP,
    SYNC_END      TIMESTAMP,
    TOTAL_CLIENTS NUMBER DEFAULT 0,
    SUCCESS_COUNT NUMBER DEFAULT 0,
    FAILURE_COUNT NUMBER DEFAULT 0,
    LOG_DETAILS   CLOB
)
"""

DDL_INDEXES = [
    "CREATE INDEX IDX_NOTIF_CLIENT ON APEX_NOTIFICATIONS(CLIENT_ID)",
    "CREATE INDEX IDX_NOTIF_DATE   ON APEX_NOTIFICATIONS(FIRST_SYNC_DATE)",
    "CREATE INDEX IDX_NOTIF_NEW    ON APEX_NOTIFICATIONS(IS_NEW)",
    "CREATE INDEX IDX_SUMM_CLIENT  ON APEX_CLIENT_SUMMARY(CLIENT_ID, SYNC_DATE)",
]


class DBManager:

    def __init__(self):
        self._pool: Optional[oracledb.ConnectionPool] = None

    def connect(self) -> None:
        if config.ORACLE_MODE == 'thick':
            try:
                oracledb.init_oracle_client(lib_dir=config.ORACLE_CLIENT_PATH)
            except Exception as e:
                logger.warning(f"Oracle thick mode failed, using thin: {e}")

        self._pool = oracledb.create_pool(
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            dsn=config.DB_DSN,
            min=1,
            max=max(config.MAX_CONCURRENT + 2, 5),
            increment=1,
        )
        logger.info(f"Oracle pool created — {config.DB_DSN}")

    def close(self) -> None:
        if self._pool:
            self._pool.close()

    @contextmanager
    def get_conn(self):
        if not self._pool:
            raise RuntimeError("Call connect() first")
        conn = self._pool.acquire()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.release(conn)

    def create_tables(self) -> None:
        tables = {
            'APEX_CLIENT_SUMMARY':    DDL_CLIENT_SUMMARY,
            'APEX_NOTIFICATIONS':     DDL_NOTIFICATIONS,
            'APEX_NOTIFICATION_FILES': DDL_NOTIFICATION_FILES,
            'APEX_SYNC_LOG':          DDL_SYNC_LOG,
        }
        with self.get_conn() as conn:
            cur = conn.cursor()
            for name, ddl in tables.items():
                if not self._table_exists(cur, name):
                    cur.execute(ddl)
                    logger.info(f"Created table: {name}")
                    for idx in DDL_INDEXES:
                        tbl = idx.split(' ON ')[1].split('(')[0].strip()
                        if tbl == name:
                            try:
                                cur.execute(idx)
                            except oracledb.DatabaseError:
                                pass

    def _table_exists(self, cursor, name: str) -> bool:
        cursor.execute(
            "SELECT COUNT(*) FROM user_tables WHERE table_name = :1",
            [name.upper()]
        )
        return cursor.fetchone()[0] > 0

    def get_all_clients(self) -> List[Dict[str, str]]:
        col_un   = config.COL_USERNAME
        col_pw   = config.COL_PASSWORD
        col_name = config.COL_CLIENT_NAME
        table    = config.CREDENTIALS_TABLE
        id_expr  = config.COL_CLIENT_ID or "TO_CHAR(ROWNUM)"

        conditions = [
            f"{col_un} IS NOT NULL",
            f"{col_pw} IS NOT NULL",
            f"TRIM(TO_CHAR({col_un})) != ' '",
        ]
        if config.CREDENTIALS_FILTER:
            conditions.insert(0, config.CREDENTIALS_FILTER)

        sql = f"""
            SELECT {id_expr}  AS client_id,
                   {col_name} AS client_name,
                   {col_un}   AS sap_username,
                   {col_pw}   AS sap_password
            FROM   {table}
            WHERE  {' AND '.join(conditions)}
            ORDER  BY {col_name}
        """

        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            cols = [c[0].lower() for c in cur.description]
            raw_rows = cur.fetchall()
            rows = []
            for row in raw_rows:
                d = dict(zip(cols, [str(v) if v is not None else '' for v in row]))
                # فك تشفير الباسورد لو كان مشفراً (backward-compatible مع plaintext)
                d['sap_password'] = decrypt_password(d['sap_password'])
                rows.append(d)
            logger.info(f"Loaded {len(rows)} clients from {table}")
            return rows

    def save_client_summary(
        self,
        client_id: str,
        client_name: str,
        notifications_count: int,
        new_notifications_count: int,
        obligations_count: int,
        forms_count: int,
        docs_count: int,
        status: str,
        error_msg: str = '',
    ) -> None:
        with self.get_conn() as conn:
            conn.cursor().execute("""
                INSERT INTO APEX_CLIENT_SUMMARY
                    (CLIENT_ID, CLIENT_NAME, NOTIFICATIONS_COUNT, NEW_NOTIFICATIONS_COUNT,
                     OBLIGATIONS_COUNT, FORMS_COUNT, DOCS_COUNT, STATUS, ERROR_MSG)
                VALUES (:1,:2,:3,:4,:5,:6,:7,:8,:9)
            """, [
                client_id, client_name,
                notifications_count, new_notifications_count,
                obligations_count, forms_count, docs_count,
                status, error_msg[:4000] if error_msg else None
            ])

    def save_notifications(
        self,
        client_id: str,
        client_name: str,
        notifications: List[Dict[str, Any]],
    ) -> int:
        new_count = 0
        now = datetime.now()

        with self.get_conn() as conn:
            cur = conn.cursor()
            for notif in notifications:
                h = self._hash_notification(client_id, notif)
                cur.execute(
                    "SELECT COUNT(*) FROM APEX_NOTIFICATIONS WHERE NOTIFICATION_HASH=:1",
                    [h]
                )
                if cur.fetchone()[0] > 0:
                    # already exists — update LAST_SYNC_DATE and clear IS_NEW flag
                    cur.execute("""
                        UPDATE APEX_NOTIFICATIONS
                        SET LAST_SYNC_DATE = :1, IS_NEW = 0
                        WHERE NOTIFICATION_HASH = :2
                    """, [now, h])
                    continue

                cur.execute("""
                    INSERT INTO APEX_NOTIFICATIONS
                        (CLIENT_ID, CLIENT_NAME, SEVERITY, NOTIF_TYPE,
                         SUBJECT, MESSAGE_BODY, NOTIF_DATE_STR, READ_STATUS,
                         FIRST_SYNC_DATE, LAST_SYNC_DATE, IS_NEW, NOTIFICATION_HASH)
                    VALUES (:1,:2,:3,:4,:5,:6,:7,:8,:9,:10,1,:11)
                """, [
                    client_id,
                    client_name,
                    notif.get('severity', ''),
                    notif.get('notif_type', ''),
                    (notif.get('subject', '') or '')[:2000],
                    notif.get('message_body', '') or '',
                    (notif.get('date', '') or '')[:100],
                    notif.get('read_status', ''),
                    now, now,
                    h,
                ])
                new_count += 1

        logger.info(f"[{client_name}] notifications: {len(notifications)} total, {new_count} new")
        return new_count

    def get_notifications_without_files(self, limit: int = 200) -> List[Dict]:
        """Return notifications not yet checked for attachments."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT n.ID, n.CLIENT_ID, n.CLIENT_NAME, n.SUBJECT, n.NOTIF_DATE_STR
                FROM   APEX_NOTIFICATIONS n
                WHERE  n.FILES_CHECKED_AT IS NULL
                ORDER BY n.FIRST_SYNC_DATE DESC
                FETCH FIRST :1 ROWS ONLY
            """, [limit])
            cols = [c[0].lower() for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def mark_notifications_checked(self, notification_ids: list) -> None:
        if not notification_ids:
            return
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.executemany(
                "UPDATE APEX_NOTIFICATIONS SET FILES_CHECKED_AT = SYSTIMESTAMP WHERE ID = :1",
                [[nid] for nid in notification_ids]
            )
            conn.commit()

    def save_file(
        self,
        file_path: str,
        client_id: str,
        client_name: str,
        file_name: str,
        file_ext: str,
        file_size: int,
        is_dangerous: int,
        security_note: str,
        source: str = 'NOTIFICATION',
        notification_id: int = None,
        doc_date=None,
        expiry_date=None,
    ) -> None:
        with self.get_conn() as conn:
            conn.cursor().execute("""
                INSERT INTO APEX_NOTIFICATION_FILES
                    (NOTIFICATION_ID, CLIENT_ID, CLIENT_NAME, FILE_NAME,
                     FILE_EXT, FILE_SIZE, FILE_PATH, IS_DANGEROUS, SECURITY_NOTE,
                     SOURCE, DOC_DATE, EXPIRY_DATE)
                VALUES (:1,:2,:3,:4,:5,:6,:7,:8,:9,:10,:11,:12)
            """, [
                notification_id, client_id, client_name,
                file_name, file_ext, file_size, file_path,
                is_dangerous, security_note or None, source,
                _parse_date(doc_date), _parse_date(expiry_date),
            ])
        logger.info(f"Saved file record: {file_name} ({source}) {'FLAGGED' if is_dangerous else 'OK'}")

    def save_notification_file(
        self,
        notification_id: int,
        client_id: str,
        client_name: str,
        file_name: str,
        file_ext: str,
        file_size: int,
        file_content: bytes,
        is_dangerous: int,
        security_note: str,
    ) -> None:
        """حفظ ملف مرفق بالتنبيه على الديسك ثم تسجيل السطر في قاعدة البيانات."""
        client_dir = config.DOCS_DIR / str(client_id)
        client_dir.mkdir(parents=True, exist_ok=True)

        safe_name = re.sub(r'[^\w؀-ۿ.\-]', '_', file_name)[:200] or 'document'
        dest = client_dir / safe_name
        n = 1
        while dest.exists():
            dest = client_dir / f"{Path(safe_name).stem}_{n}{Path(safe_name).suffix}"
            n += 1

        dest.write_bytes(file_content)
        rel_path = f"{client_id}/{dest.name}"

        self.save_file(
            file_path=rel_path,
            client_id=client_id,
            client_name=client_name,
            file_name=dest.name,
            file_ext=file_ext,
            file_size=len(file_content),
            is_dangerous=is_dangerous,
            security_note=security_note,
            source='NOTIFICATION',
            notification_id=notification_id,
        )

    def file_exists(self, client_id: str, file_name: str, source: str = 'MYDOCS') -> bool:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM APEX_NOTIFICATION_FILES WHERE CLIENT_ID=:1 AND FILE_NAME=:2 AND SOURCE=:3",
                [client_id, file_name, source]
            )
            return cur.fetchone()[0] > 0

    def get_client_doc_names(self, client_id: str) -> set:
        """Return file names already downloaded for a client (MYDOCS source)."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT FILE_NAME FROM APEX_NOTIFICATION_FILES WHERE CLIENT_ID=:1 AND SOURCE='MYDOCS'",
                [client_id]
            )
            return {row[0] for row in cur.fetchall()}

    def get_notification_hashes(self, client_id: str) -> set:
        """Return all known hashes for a client — used to skip already-fetched notifications."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT NOTIFICATION_HASH FROM APEX_NOTIFICATIONS WHERE CLIENT_ID = :1",
                [client_id]
            )
            return {row[0] for row in cur.fetchall()}

    def _hash_notification(self, client_id: str, notif: Dict) -> str:
        subj  = (notif.get('subject') or '').strip()
        date  = (notif.get('date') or '').strip()
        ntype = (notif.get('notif_type') or '').strip()
        raw   = f"{client_id}|{subj}|{date}|{ntype}"
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def save_sync_error(
        self,
        client_id: str,
        client_name: str,
        username: str,
        step: int,
        error_type: str,
        error_msg: str,
    ) -> None:
        with self.get_conn() as conn:
            conn.cursor().execute("""
                INSERT INTO APEX_SYNC_ERRORS
                    (CLIENT_ID, CLIENT_NAME, USERNAME, STEP, ERROR_TYPE, ERROR_MSG)
                VALUES (:1,:2,:3,:4,:5,:6)
            """, [
                client_id, client_name, username, step,
                (error_type or 'UNKNOWN')[:50],
                (error_msg or '')[:4000],
            ])
        logger.info(f"Sync error saved: [{client_name}] {error_type}")

    def save_sync_log(
        self,
        sync_start: datetime,
        sync_end: datetime,
        total_clients: int,
        success_count: int,
        failure_count: int,
        log_details: str,
    ) -> None:
        with self.get_conn() as conn:
            conn.cursor().execute("""
                INSERT INTO APEX_SYNC_LOG
                    (SYNC_START, SYNC_END, TOTAL_CLIENTS,
                     SUCCESS_COUNT, FAILURE_COUNT, LOG_DETAILS)
                VALUES (:1,:2,:3,:4,:5,:6)
            """, [sync_start, sync_end, total_clients, success_count, failure_count, log_details])
        logger.info(f"Sync log saved — {success_count}/{total_clients} succeeded")
