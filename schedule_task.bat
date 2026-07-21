@echo off
chcp 65001 >nul
echo.
echo ============================================================
echo  إعداد مهمة Windows Task Scheduler (كل 6 ساعات)
echo  يجب تشغيل هذا الملف كـ "مسؤول" (Run as Administrator)
echo ============================================================
echo.

set "SCRIPT_DIR=%~dp0"
set "TASK_NAME=ETA_Tax_Sync"
set "RUN_BAT=%SCRIPT_DIR%run.bat"

:: حذف المهمة القديمة إن وُجدت
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: إنشاء المهمة الجديدة
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%RUN_BAT%\"" ^
  /sc HOURLY ^
  /mo 6 ^
  /st 00:00 ^
  /ru SYSTEM ^
  /rl HIGHEST ^
  /f

if errorlevel 1 (
    echo [خطأ] فشل إنشاء المهمة المجدولة
    echo تأكد من تشغيل الملف بصلاحيات المسؤول
    pause
    exit /b 1
)

echo.
echo [نجاح] تم إنشاء المهمة: %TASK_NAME%
echo التشغيل: كل 6 ساعات بدءاً من منتصف الليل
echo.

:: عرض تفاصيل المهمة
schtasks /query /tn "%TASK_NAME%" /fo LIST

echo.
echo للتشغيل الفوري للاختبار:
echo   schtasks /run /tn "%TASK_NAME%"
echo.
echo لحذف المهمة:
echo   schtasks /delete /tn "%TASK_NAME%" /f
echo.
pause
