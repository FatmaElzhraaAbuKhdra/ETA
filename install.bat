@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title ETA Sync - تثبيت البرنامج

echo.
echo  +------------------------------------------+
echo  ^|      ETA Sync - تثبيت البرنامج          ^|
echo  +------------------------------------------+
echo.

:: ── الخطوة 0: التحقق من Python ─────────────────────────────────
py -3 --version >nul 2>&1
if errorlevel 1 (
    echo  [خطأ] Python غير مثبت على هذا الجهاز.
    echo.
    echo  قم بتثبيته من الرابط التالي ثم شغّل install.bat مرة أخرى:
    echo  https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('py -3 --version 2^>^&1') do (
    echo  [1/4] Python موجود: %%v
)

:: ── الخطوة 1: تثبيت المكتبات ───────────────────────────────────
echo.
echo  [2/4] جاري تثبيت المكتبات...
py -3 -m pip install -r "%~dp0requirements.txt" -q --no-warn-script-location
if errorlevel 1 (
    echo  [خطأ] فشل تثبيت المكتبات. تحقق من اتصال الإنترنت.
    pause & exit /b 1
)
echo        المكتبات جاهزة

:: ── الخطوة 2: تثبيت متصفح Playwright ──────────────────────────
echo.
echo  [3/4] جاري تثبيت متصفح Playwright (قد يستغرق بضع دقائق)...
py -3 -m playwright install chromium
if errorlevel 1 (
    echo  [خطأ] فشل تثبيت Playwright.
    pause & exit /b 1
)
echo        المتصفح جاهز

:: ── الخطوة 3: إنشاء الأيقونة والاختصار ────────────────────────
echo.
echo  [4/4] جاري إنشاء الأيقونة والاختصار على سطح المكتب...
py -3 "%~dp0_setup_desktop.py"
if errorlevel 1 (
    echo  [خطأ] فشل إنشاء الاختصار.
    pause & exit /b 1
)

:: ── الخطوة 4: إعداد ملف .env ────────────────────────────────────
if not exist "%~dp0.env" (
    echo.
    echo  [تنبيه] ملف .env غير موجود.
    echo  سيتم إنشاء ملف .env.example - يرجى نسخه وتعديله:
    echo    1. انسخ .env.example وأعد تسميته إلى .env
    echo    2. أدخل بيانات قاعدة البيانات الصحيحة
)

echo.
echo  +------------------------------------------+
echo  ^|         تم التثبيت بنجاح!               ^|
echo  ^|   الأيقونة موجودة على سطح المكتب        ^|
echo  +------------------------------------------+
echo.
pause
