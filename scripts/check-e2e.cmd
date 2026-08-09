@echo off
setlocal
cd /d "%~dp0.."
python scripts\run_e2e_tests.py
exit /b %errorlevel%
