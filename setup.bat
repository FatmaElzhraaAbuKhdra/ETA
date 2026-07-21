@echo off
chcp 65001 >nul
echo.
echo ============================================================
echo  إعداد نظام مزامنة هيئة الضرائب المصرية
echo ============================================================
echo.

:: التحقق من Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [خطأ] Python غير مثبّت أو غير موجود في PATH
    echo يرجى تثبيت Python 3.9+ من https://python.org
    pause
    exit /b 1
)

echo [1/4] تثبيت المتطلبات...
pip install -r requirements.txt
if errorlevel 1 (
    echo [خطأ] فشل تثبيت المتطلبات
    pause
    exit /b 1
)

echo.
echo [2/4] تثبيت متصفح Playwright (Chromium)...
playwright install chromium
if errorlevel 1 (
    echo [خطأ] فشل تثبيت Playwright
    pause
    exit /b 1
)

echo.
echo [3/4] إنشاء ملف البيئة...
if not exist .env (
    copy .env.example .env
    echo تم إنشاء ملف .env — يرجى تعديله ببيانات الاتصال الفعلية
) else (
    echo ملف .env موجود مسبقاً
)

echo.
echo [4/4] إنشاء مجلد السجلات...
if not exist logs mkdir logs
if not exist logs\screenshots mkdir logs\screenshots

echo.
echo ============================================================
echo  اكتمل الإعداد بنجاح!
echo.
echo  الخطوات التالية:
echo  1. عدّل ملف .env ببيانات Oracle الفعلية
echo  2. شغّل: python main.py  (للاختبار)
echo  3. لجدولة التشغيل كل 6 ساعات: شغّل schedule_task.bat
echo ============================================================
pause
