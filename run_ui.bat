@echo off
REM Launches the Streamlit UI on Windows.
REM
REM Prefers the project's own .venv, then whatever `python` resolves to on PATH.
REM Pass extra Streamlit flags through, e.g.:  run_ui.bat --server.port 8600
setlocal
cd /d "%~dp0"

set PYTHON=%~dp0.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python

"%PYTHON%" -c "import streamlit" 2>nul
if errorlevel 1 (
    echo.
    echo Streamlit is not installed for "%PYTHON%".
    echo Run:  python -m venv .venv ^&^& .venv\Scripts\pip install -e .
    echo.
    exit /b 1
)

"%PYTHON%" -m streamlit run app/ui.py --server.address localhost --server.port 8501 %*
endlocal
