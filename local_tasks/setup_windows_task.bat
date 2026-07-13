@echo off
:: ────────────────────────────────────────────────────────────────────────────
:: setup_windows_task.bat
:: ตั้งค่า Windows Task Scheduler รันสคริปต์ 10:30 น. ทุกวัน
:: รันไฟล์นี้ครั้งเดียว "Run as Administrator"
:: ────────────────────────────────────────────────────────────────────────────

:: ─── แก้ paths ด้านล่างให้ตรงกับเครื่องของคุณ ─────────────────────────────
set PYTHON_EXE=python
set SCRIPT_PATH=D:\Watercourse\Department\HR\line-hr-bot\local_tasks\save_workplan_status.py
set BOT_URL=https://xxxx.up.railway.app
set TASK_NAME=HR_WorkPlan_Check_1030

:: ────────────────────────────────────────────────────────────────────────────
echo [INFO] สร้าง Scheduled Task: %TASK_NAME%

schtasks /Create /TN "%TASK_NAME%" ^
  /TR "\"%PYTHON_EXE%\" \"%SCRIPT_PATH%\" --url %BOT_URL%" ^
  /SC DAILY ^
  /ST 10:30 ^
  /RU "%USERNAME%" ^
  /F

if %ERRORLEVEL% EQU 0 (
    echo [OK]  Task สร้างสำเร็จ: %TASK_NAME%
    echo       รันทุกวัน 10:30 น. อัตโนมัติ
) else (
    echo [ERR] สร้าง Task ไม่สำเร็จ - ลอง Run as Administrator
)

echo.
echo [INFO] ตรวจสอบ Task:
schtasks /Query /TN "%TASK_NAME%" /FO LIST

pause
