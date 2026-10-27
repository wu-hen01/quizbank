@echo off
rem QuizBank launcher for Windows.
rem
rem Two hard rules for this file:
rem   1) ASCII only + CRLF. cmd.exe decodes UTF-8 as GBK and mangles Chinese
rem      text, which can silently break parsing.
rem   2) Never put ( or ) inside an echo line, and avoid parenthesised
rem      if-blocks. cmd treats them as command-group delimiters: the block
rem      closes early and the script dies with "was unexpected at this time",
rem      so the window flashes and closes before pause. Use goto labels.
setlocal
cd /d "%~dp0"

set "PY=python"
where %PY% >nul 2>nul
if errorlevel 1 goto no_python

if exist ".env" goto have_env
if not exist ".env.example" goto have_env
copy /y ".env.example" ".env" >nul
echo [QuizBank] Created .env from .env.example
echo [QuizBank] Fill LLM_API_KEY in .env to enable AI structuring - optional.
:have_env

%PY% -c "import fastapi, uvicorn, httpx" >nul 2>nul
if not errorlevel 1 goto deps_ok
echo [QuizBank] First run: installing dependencies, please wait...
%PY% -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto pip_failed
:deps_ok

%PY% run.py

echo.
echo [QuizBank] Server stopped. You can close this window.
pause
exit /b 0

:no_python
echo [QuizBank] Python not found in PATH.
echo            Install Python 3.9+ from https://www.python.org/downloads/windows/
echo            and tick "Add Python to PATH" during setup, then run this again.
pause
exit /b 1

:pip_failed
echo.
echo [QuizBank] Install failed. Run it yourself:
echo            %PY% -m pip install -r requirements.txt
pause
exit /b 1
