@echo off
setlocal
cd /d "%~dp0"
python -m plan_obfuscator.bootstrap %*
exit /b %errorlevel%
