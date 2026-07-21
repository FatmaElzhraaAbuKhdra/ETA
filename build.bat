@echo off
chcp 65001 >nul
echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║   ETA Sync — بناء ملف EXE                       ║
echo  ╚══════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

echo [1/4] تثبيت pyinstaller...
pip install pyinstaller --quiet
if %errorlevel% neq 0 ( echo [ERROR] & pause & exit /b 1 )

echo [2/4] بناء الـ exe...

pyinstaller ^
  --onefile ^
  --windowed ^
  --name "ETA_Sync" ^
  --add-data "static;static" ^
  --hidden-import "oracledb" ^
  --hidden-import "oracledb.impl" ^
  --hidden-import "oracledb.impl.thin" ^
  --hidden-import "playwright" ^
  --hidden-import "playwright.async_api" ^
  --hidden-import "fastapi" ^
  --hidden-import "fastapi.staticfiles" ^
  --hidden-import "uvicorn" ^
  --hidden-import "uvicorn.logging" ^
  --hidden-import "uvicorn.loops" ^
  --hidden-import "uvicorn.loops.auto" ^
  --hidden-import "uvicorn.protocols.http.auto" ^
  --hidden-import "uvicorn.lifespan.on" ^
  --hidden-import "starlette.staticfiles" ^
  --hidden-import "anyio._backends._asyncio" ^
  --hidden-import "dotenv" ^
  --collect-submodules "oracledb" ^
  --collect-submodules "playwright" ^
  --noconfirm ^
  launcher.py

if %errorlevel% neq 0 (
  echo [ERROR] فشل البناء
  pause & exit /b 1
)

echo [3/4] تجهيز الباكدج...
set PKG=dist\ETA_Sync_Package
if not exist "%PKG%" mkdir "%PKG%"

copy "dist\ETA_Sync.exe" "%PKG%\" >nul

if exist ".env" (
  copy ".env" "%PKG%\" >nul
  echo تم نسخ .env
) else (
  echo لم يوجد .env — سيتم إنشاء نموذج
)

echo. > "%PKG%\.env"
echo # عدّل البيانات دي قبل التشغيل >> "%PKG%\.env"
echo DB_USER=CRM >> "%PKG%\.env"
echo DB_PASSWORD=CRM >> "%PKG%\.env"
echo DB_DSN=IP_ADDRESS:1521/ORCLPDB >> "%PKG%\.env"
echo CREDENTIALS_TABLE=CUST >> "%PKG%\.env"
echo CREDENTIALS_FILTER=CUST_TYPE=0 >> "%PKG%\.env"
echo COL_USERNAME=USERNM >> "%PKG%\.env"
echo COL_PASSWORD=PW >> "%PKG%\.env"
echo COL_CLIENT_NAME=CUST_NAME >> "%PKG%\.env"
echo COL_CLIENT_ID=CUSTID >> "%PKG%\.env"
echo ORACLE_MODE=thin >> "%PKG%\.env"
echo HEADLESS=true >> "%PKG%\.env"
echo MAX_CONCURRENT=2 >> "%PKG%\.env"
echo PAGE_TIMEOUT=90000 >> "%PKG%\.env"

echo [4/4] تشغيل اختبار سريع...
"%PKG%\ETA_Sync.exe" --help >nul 2>&1

echo.
echo  ╔══════════════════════════════════════════════════════════╗
echo  ║  ✓ تم البناء بنجاح                                      ║
echo  ║                                                          ║
echo  ║  الملفات جاهزة في:  dist\ETA_Sync_Package\              ║
echo  ║                                                          ║
echo  ║  لتركيب عند العميل:                                      ║
echo  ║  1. انسخ فولدر ETA_Sync_Package                         ║
echo  ║  2. افتح .env وعدّل بيانات الـ DB                       ║
echo  ║  3. دبل كليك على ETA_Sync.exe                           ║
echo  ║                                                          ║
echo  ║  في أول تشغيل: سيثبّت Chromium تلقائياً (مرة واحدة)    ║
echo  ╚══════════════════════════════════════════════════════════╝
echo.
pause
