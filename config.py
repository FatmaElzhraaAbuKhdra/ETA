import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# When packaged as .exe (PyInstaller), use exe directory; otherwise use script directory
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

load_dotenv(BASE_DIR / '.env')

_log = logging.getLogger(__name__)

# Oracle
DB_USER            = os.getenv('DB_USER', '')
DB_PASSWORD        = os.getenv('DB_PASSWORD', '')
DB_DSN             = os.getenv('DB_DSN', 'localhost:1521/ORCL')
ORACLE_MODE        = os.getenv('ORACLE_MODE', 'thin')
ORACLE_CLIENT_PATH = os.getenv('ORACLE_CLIENT_PATH', r'C:\oracle\instantclient_21_3')

# Credentials table
CREDENTIALS_TABLE  = os.getenv('CREDENTIALS_TABLE', 'CUST')
CREDENTIALS_FILTER = os.getenv('CREDENTIALS_FILTER', '')
COL_USERNAME       = os.getenv('COL_USERNAME',    'USERNM')
COL_PASSWORD       = os.getenv('COL_PASSWORD',    'PW')
COL_CLIENT_NAME    = os.getenv('COL_CLIENT_NAME', 'CUST_NAME')
COL_CLIENT_ID      = os.getenv('COL_CLIENT_ID',   '')

# ETA URLs
ETA_AUTH_URL = (
    "https://auth.eta.gov.eg:8080/auth/realms/e-tax/protocol/openid-connect/auth"
    "?client_id=etax-sso&redirect_uri=https://workspace.eta.gov.eg/"
)
_APP = "https://fpascs.eta.gov.eg:44300/sap/bc/ui5_ui5/sap/ZETA_MCFAPP/index.html?sap-client=300&sap-language=ar"
ETA_HOME_URL  = f"{_APP}#/home"
ETA_NOTIF_URL = f"{_APP}#/messages/AL/%201"

CARD_LABELS = {
    'notifications': ['التنبيهات', 'التنبيه'],
    'obligations':   ['التزامات التقديم', 'التزامات'],
    'forms':         ['النماذج'],
    'documents':     ['مستنداتي', 'وثائقي'],
}

# Browser
HEADLESS          = os.getenv('HEADLESS', 'true').lower() == 'true'
MAX_CONCURRENT    = int(os.getenv('MAX_CONCURRENT', '2'))
PAGE_TIMEOUT      = int(os.getenv('PAGE_TIMEOUT',   '90000'))
ELEMENT_TIMEOUT   = int(os.getenv('ELEMENT_TIMEOUT','30000'))
MAX_RETRIES       = int(os.getenv('MAX_RETRIES',    '3'))
RETRY_DELAY       = float(os.getenv('RETRY_DELAY',  '5'))
MAX_NOTIFICATIONS = int(os.getenv('MAX_NOTIFICATIONS', '500'))

# Logging
LOG_DIR            = BASE_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)
LOG_LEVEL          = os.getenv('LOG_LEVEL', 'INFO')
LOG_RETENTION_DAYS = int(os.getenv('LOG_RETENTION_DAYS', '30'))

SCREENSHOTS_DIR = BASE_DIR / 'logs' / 'screenshots'
SCREENSHOTS_DIR.mkdir(exist_ok=True)
SAVE_SCREENSHOTS = os.getenv('SAVE_ERROR_SCREENSHOTS', 'true').lower() == 'true'

# Local document storage
DOCS_DIR = BASE_DIR / os.getenv('DOCS_DIR', 'documents')
DOCS_DIR.mkdir(exist_ok=True)

# API security
API_KEY = os.getenv('API_KEY', '').strip()
_raw_origins = os.getenv('ALLOWED_ORIGINS', '').strip()
if _raw_origins:
    ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(',') if o.strip()]
else:
    ALLOWED_ORIGINS = ['*']
    # تحذير: CORS مفتوح لكل الأصول — اضبط ALLOWED_ORIGINS في .env للإنتاج

# Password encryption (see crypto_utils.py)
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', '').strip()
