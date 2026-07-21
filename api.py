import sys
try:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

import asyncio
import logging
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import oracledb
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

import config

logger = logging.getLogger(__name__)

# ── Endpoints exempt from API-Key auth ──────────────────────────
_AUTH_EXEMPT = {'/', '/health', '/docs', '/redoc', '/openapi.json'}

_sync_running = False

_pool: Optional[oracledb.ConnectionPool] = None


def get_pool() -> oracledb.ConnectionPool:
    if _pool is None:
        raise HTTPException(503, "Database unavailable")
    return _pool


def query(sql: str, params: dict = None) -> list[dict]:
    with get_pool().acquire() as conn:
        cur = conn.cursor()
        cur.execute(sql, params or {})
        cols = [c[0].lower() for c in cur.description]
        rows = []
        for row in cur.fetchall():
            clean = []
            for v in row:
                if isinstance(v, datetime):
                    clean.append(v.strftime("%Y-%m-%d %H:%M:%S"))
                elif hasattr(v, 'read'):
                    clean.append(v.read())
                else:
                    clean.append(v)
            rows.append(dict(zip(cols, clean)))
        return rows


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    if config.ORACLE_MODE == 'thick':
        try:
            oracledb.init_oracle_client(lib_dir=config.ORACLE_CLIENT_PATH)
        except Exception:
            pass
    _pool = oracledb.create_pool(
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        dsn=config.DB_DSN,
        min=1, max=5, increment=1,
    )
    logger.info(f"Oracle connected → {config.DB_DSN}")
    # Migration 1: fix SOURCE for مستنداتي files saved before this fix
    try:
        with _pool.acquire() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE APEX_NOTIFICATION_FILES
                SET SOURCE = 'MYDOCS'
                WHERE SOURCE = 'NOTIFICATION' AND NOTIFICATION_ID IS NULL
            """)
            rows_fixed = cur.rowcount
            conn.commit()
        if rows_fixed:
            logger.info(f"Migration: fixed SOURCE for {rows_fixed} file(s)")
    except Exception as _e:
        logger.warning(f"Migration 1 skipped: {_e}")
    # Migration 2: add new columns if they don't exist yet
    for _col, _type in [('DOC_DATE', 'DATE'), ('EXPIRY_DATE', 'DATE')]:
        try:
            with _pool.acquire() as conn:
                conn.cursor().execute(
                    f"ALTER TABLE APEX_NOTIFICATION_FILES ADD ({_col} {_type})"
                )
                conn.commit()
            logger.info(f"Migration: added column {_col}")
        except Exception:
            pass  # column already exists — ignore ORA-01430
    # Migration 3: add FILES_CHECKED_AT to APEX_NOTIFICATIONS
    try:
        with _pool.acquire() as conn:
            conn.cursor().execute(
                "ALTER TABLE APEX_NOTIFICATIONS ADD (FILES_CHECKED_AT TIMESTAMP)"
            )
            conn.commit()
        logger.info("Migration: added FILES_CHECKED_AT to APEX_NOTIFICATIONS")
    except Exception:
        pass
    yield
    _pool.close()


app = FastAPI(title="ETA Sync API", version="1.0.0", lifespan=lifespan)


class _APIKeyMiddleware(BaseHTTPMiddleware):
    """API-Key authentication — skip if API_KEY not configured (backward-compatible)."""
    async def dispatch(self, request: Request, call_next):
        if not config.API_KEY:
            return await call_next(request)
        if request.url.path in _AUTH_EXEMPT:
            return await call_next(request)
        if request.headers.get('X-API-Key', '') != config.API_KEY:
            return JSONResponse({'detail': 'Unauthorized — X-API-Key header required'}, status_code=401)
        return await call_next(request)


if not config.API_KEY:
    logger.warning("API_KEY غير مضبوط — الـ API يعمل بدون حماية (اضبط API_KEY في .env)")

if config.ALLOWED_ORIGINS == ['*']:
    logger.warning("ALLOWED_ORIGINS غير مضبوط — CORS مفتوح لكل الأصول (اضبط في .env للإنتاج)")

app.add_middleware(_APIKeyMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/documents", StaticFiles(directory=str(config.DOCS_DIR)), name="documents")


@app.get("/ui", include_in_schema=False)
def ui():
    path = config.BASE_DIR / 'static' / 'index.html'
    with open(path, encoding='utf-8') as f:
        return HTMLResponse(content=f.read())

@app.get("/", tags=["health"])
def root():
    return {"status": "ok", "api": "ETA Sync", "version": "1.0.0", "ui": "/ui"}


@app.get("/health", tags=["health"])
def health():
    try:
        n = query("SELECT COUNT(*) AS cnt FROM APEX_NOTIFICATIONS")[0]["cnt"]
        return {"status": "ok", "db": "connected", "total_notifications": n}
    except Exception as e:
        raise HTTPException(503, str(e))


@app.get("/clients", tags=["clients"])
def list_clients(
    search: Optional[str] = Query(None),
    limit:  int = Query(100, ge=1, le=1000),
    offset: int = Query(0,   ge=0),
):
    col_name = config.COL_CLIENT_NAME
    col_un   = config.COL_USERNAME
    table    = config.CREDENTIALS_TABLE
    where    = f"WHERE {config.CREDENTIALS_FILTER}" if config.CREDENTIALS_FILTER else "WHERE 1=1"
    params   = {}

    if search:
        where += f" AND UPPER({col_name}) LIKE UPPER(:search)"
        params["search"] = f"%{search}%"

    params["offset"] = offset
    params["limit"]  = limit

    rows = query(f"""
        SELECT {col_name} AS client_name, {col_un} AS username
        FROM   {table} {where}
        ORDER BY {col_name}
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
    """, params)
    return {"count": len(rows), "offset": offset, "data": rows}


@app.get("/notifications", tags=["notifications"])
def list_notifications(
    client_name: Optional[str] = Query(None),
    severity:    Optional[str] = Query(None),
    date_from:   Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to:     Optional[str] = Query(None, description="YYYY-MM-DD"),
    limit:  int = Query(50,  ge=1, le=500),
    offset: int = Query(0,   ge=0),
):
    where  = "WHERE 1=1"
    params = {}

    if client_name:
        where += " AND UPPER(CLIENT_NAME) LIKE UPPER(:cname)"
        params["cname"] = f"%{client_name}%"
    if severity:
        where += " AND SEVERITY = :severity"
        params["severity"] = severity
    if date_from:
        where += " AND SYNC_DATE >= TO_DATE(:dfrom,'YYYY-MM-DD')"
        params["dfrom"] = date_from
    if date_to:
        where += " AND SYNC_DATE <= TO_DATE(:dto,'YYYY-MM-DD') + 1"
        params["dto"] = date_to

    total = query(f"SELECT COUNT(*) AS cnt FROM APEX_NOTIFICATIONS {where}", params)[0]["cnt"]

    params["offset"] = offset
    params["limit"]  = limit
    rows = query(f"""
        SELECT ID, CLIENT_NAME, SEVERITY, NOTIF_TYPE,
               SUBJECT, MESSAGE_BODY, NOTIF_DATE_STR, READ_STATUS,
               IS_NEW, FIRST_SYNC_DATE, LAST_SYNC_DATE
        FROM   APEX_NOTIFICATIONS {where}
        ORDER BY FIRST_SYNC_DATE DESC, ID DESC
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
    """, params)

    return {"total": total, "count": len(rows), "offset": offset, "limit": limit, "data": rows}


@app.get("/notifications/{client_name}", tags=["notifications"])
def client_notifications(
    client_name: str,
    limit:  int = Query(50, ge=1, le=500),
    offset: int = Query(0,  ge=0),
):
    total = query(
        "SELECT COUNT(*) AS cnt FROM APEX_NOTIFICATIONS WHERE CLIENT_NAME = :cn",
        {"cn": client_name}
    )[0]["cnt"]

    if total == 0:
        raise HTTPException(404, f"No notifications for: {client_name}")

    rows = query("""
        SELECT ID, SEVERITY, NOTIF_TYPE, SUBJECT, MESSAGE_BODY, NOTIF_DATE_STR, SYNC_DATE
        FROM   APEX_NOTIFICATIONS
        WHERE  CLIENT_NAME = :cn
        ORDER BY SYNC_DATE DESC, ID DESC
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
    """, {"cn": client_name, "offset": offset, "limit": limit})

    return {"client_name": client_name, "total": total, "count": len(rows), "data": rows}


@app.get("/summary", tags=["summary"])
def list_summary(
    status:      Optional[str] = Query(None, description="SUCCESS / FAILED"),
    client_name: Optional[str] = Query(None),
    limit:  int = Query(100, ge=1, le=1000),
    offset: int = Query(0,   ge=0),
):
    where  = "WHERE 1=1"
    params = {}

    if status:
        where += " AND STATUS = :status"
        params["status"] = status.upper()
    if client_name:
        where += " AND UPPER(CLIENT_NAME) LIKE UPPER(:cname)"
        params["cname"] = f"%{client_name}%"

    params["offset"] = offset
    params["limit"]  = limit

    rows = query(f"""
        SELECT CLIENT_NAME, NOTIFICATIONS_COUNT, NEW_NOTIFICATIONS_COUNT,
               OBLIGATIONS_COUNT, FORMS_COUNT, DOCS_COUNT, STATUS, ERROR_MSG, SYNC_DATE
        FROM (
            SELECT s.*, ROW_NUMBER() OVER (PARTITION BY CLIENT_NAME ORDER BY SYNC_DATE DESC) AS rn
            FROM APEX_CLIENT_SUMMARY s {where}
        )
        WHERE rn = 1
        ORDER BY CLIENT_NAME
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
    """, params)

    return {"count": len(rows), "data": rows}


@app.get("/summary/{client_name}", tags=["summary"])
def client_summary(client_name: str):
    rows = query("""
        SELECT NOTIFICATIONS_COUNT, OBLIGATIONS_COUNT, FORMS_COUNT,
               DOCS_COUNT, STATUS, ERROR_MSG, SYNC_DATE
        FROM   APEX_CLIENT_SUMMARY
        WHERE  CLIENT_NAME = :cn
        ORDER BY SYNC_DATE DESC
        FETCH FIRST 10 ROWS ONLY
    """, {"cn": client_name})

    if not rows:
        raise HTTPException(404, f"No summary for: {client_name}")

    return {"client_name": client_name, "history": rows}


@app.get("/sync/logs", tags=["sync"])
def sync_logs(limit: int = Query(10, ge=1, le=100)):
    rows = query("""
        SELECT SYNC_START, SYNC_END, TOTAL_CLIENTS, SUCCESS_COUNT, FAILURE_COUNT
        FROM   APEX_SYNC_LOG
        ORDER BY SYNC_START DESC
        FETCH FIRST :limit ROWS ONLY
    """, {"limit": limit})
    return {"count": len(rows), "data": rows}


@app.get("/sync/logs/latest", tags=["sync"])
def latest_sync():
    rows = query("""
        SELECT SYNC_START, SYNC_END, TOTAL_CLIENTS, SUCCESS_COUNT, FAILURE_COUNT, LOG_DETAILS
        FROM   APEX_SYNC_LOG
        ORDER BY SYNC_START DESC
        FETCH FIRST 1 ROWS ONLY
    """)
    if not rows:
        raise HTTPException(404, "No sync log yet")
    return rows[0]


@app.get("/stats", tags=["stats"])
def stats():
    r = query("""
        SELECT
            (SELECT COUNT(*)                       FROM APEX_NOTIFICATIONS)       AS total_notifications,
            (SELECT COUNT(DISTINCT CLIENT_NAME)    FROM APEX_NOTIFICATIONS)       AS clients_with_notifs,
            (SELECT COUNT(*)                       FROM APEX_NOTIFICATIONS
             WHERE  IS_NEW = 1)                                                   AS new_notifications,
            (SELECT COUNT(*)                       FROM APEX_SYNC_LOG)            AS total_syncs,
            (SELECT MAX(FIRST_SYNC_DATE)           FROM APEX_NOTIFICATIONS)       AS last_notification_date,
            (SELECT MAX(SYNC_START)                FROM APEX_SYNC_LOG)            AS last_sync_date
        FROM DUAL
    """)[0]

    logs = query("""
        SELECT SUCCESS_COUNT, FAILURE_COUNT, TOTAL_CLIENTS
        FROM APEX_SYNC_LOG ORDER BY SYNC_START DESC FETCH FIRST 1 ROWS ONLY
    """)
    if logs:
        r["last_sync_success"] = logs[0]["success_count"]
        r["last_sync_failed"]  = logs[0]["failure_count"]
        r["last_sync_total"]   = logs[0]["total_clients"]

    return r


@app.get("/files", tags=["files"])
def list_files(
    client_name:  Optional[str] = Query(None),
    is_dangerous: Optional[int] = Query(None, description="0 or 1"),
    limit:  int = Query(50, ge=1, le=500),
    offset: int = Query(0,  ge=0),
):
    where  = "WHERE 1=1"
    params = {}
    if client_name:
        where += " AND UPPER(CLIENT_NAME) LIKE UPPER(:cname)"
        params["cname"] = f"%{client_name}%"
    if is_dangerous is not None:
        where += " AND IS_DANGEROUS = :danger"
        params["danger"] = is_dangerous

    total = query(f"SELECT COUNT(*) AS cnt FROM APEX_NOTIFICATION_FILES {where}", params)[0]["cnt"]
    params["offset"] = offset
    params["limit"]  = limit

    rows = query(f"""
        SELECT ID, NOTIFICATION_ID, CLIENT_NAME, FILE_NAME, FILE_EXT,
               FILE_SIZE, FILE_PATH, IS_DANGEROUS, SECURITY_NOTE,
               SOURCE, DOC_DATE, EXPIRY_DATE, DOWNLOAD_DATE
        FROM   APEX_NOTIFICATION_FILES {where}
        ORDER BY DOWNLOAD_DATE DESC
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
    """, params)
    return {"total": total, "count": len(rows), "data": rows}


def _serve_file(file_id: int, inline: bool = False):
    rows = query("""
        SELECT FILE_NAME, FILE_EXT, FILE_PATH
        FROM   APEX_NOTIFICATION_FILES
        WHERE  ID = :1
    """, {"1": file_id})

    if not rows:
        raise HTTPException(404, f"File {file_id} not found")

    row      = rows[0]
    rel_path = row.get("file_path") or ""
    filename = row["file_name"] or f"file_{file_id}"

    if not rel_path:
        raise HTTPException(404, "File path not recorded")

    full_path = config.DOCS_DIR / rel_path
    if not full_path.exists():
        raise HTTPException(404, f"File not found on disk: {rel_path}")

    mime_map = {
        '.pdf':  'application/pdf',
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        '.xls':  'application/vnd.ms-excel',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.doc':  'application/msword',
        '.png':  'image/png',
        '.jpg':  'image/jpeg',
        '.jpeg': 'image/jpeg',
    }
    ext  = (row["file_ext"] or "").lower()
    mime = mime_map.get(ext, 'application/octet-stream')

    if inline:
        # open in browser tab — no Content-Disposition: attachment
        return FileResponse(path=str(full_path), media_type=mime)
    else:
        return FileResponse(path=str(full_path), media_type=mime, filename=filename)


@app.get("/files/{file_id}/view", tags=["files"])
def view_file(file_id: int):
    """Open file inline in browser (for 'فتح' button)."""
    return _serve_file(file_id, inline=True)


@app.get("/files/{file_id}/download", tags=["files"])
def download_file(file_id: int):
    return _serve_file(file_id, inline=False)


@app.post("/sync/trigger", tags=["sync"])
def trigger_sync(background_tasks: BackgroundTasks):
    """Start a manual sync run in the background (called from APEX or Postman)"""
    global _sync_running
    if _sync_running:
        raise HTTPException(409, "Sync already running")

    def run():
        global _sync_running
        _sync_running = True
        try:
            subprocess.run(
                [sys.executable, str(config.BASE_DIR / "main.py")],
                cwd=str(config.BASE_DIR),
            )
        finally:
            _sync_running = False

    background_tasks.add_task(run)
    return {"status": "started", "message": "Sync triggered — check /sync/logs/latest for progress"}


@app.get("/sync/status", tags=["sync"])
def sync_status():
    """Check if a sync is currently running"""
    return {"running": _sync_running}


if __name__ == "__main__":
    import uvicorn
    print(f"\nETA Sync API — http://localhost:8000  |  Docs: http://localhost:8000/docs\n")
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)
