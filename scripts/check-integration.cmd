@echo off
setlocal
python "%~dp0run_integration_tests.py" %*
exit /b %errorlevel%
