@echo off
cd /d "%~dp0"
where py >nul 2>nul && (
  py -3 "程序\start.py"
  goto :end
)
python "程序\start.py"
:end
if errorlevel 1 pause
