@echo off
setlocal
cd /d "%~dp0"

set "PLAN_OBFUSCATOR_PYTHON="
where py >nul 2>nul
if errorlevel 1 goto find_python
for %%V in (3.13 3.12 3.11) do (
  if not defined PLAN_OBFUSCATOR_PYTHON (
    py -%%V -c "import sys" >nul 2>nul && set "PLAN_OBFUSCATOR_PYTHON=py -%%V"
  )
)
if defined PLAN_OBFUSCATOR_PYTHON goto run

:find_python
where python >nul 2>nul
if errorlevel 1 goto no_python
set "PLAN_OBFUSCATOR_PYTHON=python"

:run
%PLAN_OBFUSCATOR_PYTHON% -m plan_obfuscator.bootstrap %*
exit /b %errorlevel%

:no_python
echo Plan Obfuscator requires Python 3.11, 3.12 or 3.13.
echo Install Python and run this file again.
exit /b 2
