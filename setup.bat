@echo off
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python not found. Opening download page...
    start https://www.python.org/downloads/
    echo Install Python 3.10+ with "Add Python to PATH" checked, then run this script again.
    pause
    exit /b 1
)

python -m venv venv
call venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt

if not exist .env (
    copy .env.example .env
    echo A .env file was created. Open it and fill in your keys.
)

echo Setup complete.
pause