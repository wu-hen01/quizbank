@echo off
rem QuizBank launcher.
rem Keep this file ASCII-only and CRLF -- cmd.exe garbles UTF-8 here.
cd /d "%~dp0"

set PY=python
where %PY% >nul 2>nul
if errorlevel 1 (
  echo [QuizBank] Python not found in PATH.
  echo            Install Python 3.9+ from https://www.python.org/downloads/windows/
  echo            and tick "Add Python to PATH" during setup, then run this again.
  pause
  exit /b 1
)

if not exist ".env" (
  if exist ".env.example" (
    copy /y ".env.example" ".env" >nul
    echo [QuizBank] Created .env from .env.example
    echo [QuizBank] Fill LLM_API_KEY in .env to enable AI structuring (optional).
  )
)

%PY% -c "import fastapi, uvicorn, httpx" >nul 2>nul
if errorlevel 1 (
  echo [QuizBank] First run: installing dependencies, please wait...
  %PY% -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo.
    echo [QuizBank] Install failed. Run it yourself:
    echo             %PY% -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)

%PY% run.py

echo.
echo [QuizBank] Server stopped. You can close this window.
pause
