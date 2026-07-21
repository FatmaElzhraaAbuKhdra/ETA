@echo off
chcp 65001 >nul

:: ============================================================
:: run.bat — مشغّل نظام مزامنة هيئة الضرائب المصرية
:: ملف التشغيل الذي يُستدعى من Windows Task Scheduler
:: ============================================================

:: تحديد مسار البرنامج (يتم تلقائياً من موقع الملف)
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

:: تحديد Python (عدّل المسار إذا لزم)
set "PYTHON_EXE=python"
:: مثال إذا كان Python في مكان محدد:
:: set "PYTHON_EXE=C:\Python311\python.exe"

:: اسم ملف السجل (يومي)
for /f "tokens=1-3 delims=/ " %%a in ("%DATE%") do (
    set "LOG_DATE=%%c%%b%%a"
)
set "LOG_FILE=%SCRIPT_DIR%logs\run_%LOG_DATE%.log"

echo ========================================== >> "%LOG_FILE%"
echo بدء التشغيل: %DATE% %TIME%              >> "%LOG_FILE%"
echo ========================================== >> "%LOG_FILE%"

:: تشغيل السكريبت مع توجيه المخرجات للملف
"%PYTHON_EXE%" main.py >> "%LOG_FILE%" 2>&1
set EXIT_CODE=%ERRORLEVEL%

echo ========================================== >> "%LOG_FILE%"
echo انتهى التشغيل: %DATE% %TIME% (كود: %EXIT_CODE%) >> "%LOG_FILE%"
echo ========================================== >> "%LOG_FILE%"

exit /b %EXIT_CODE%
