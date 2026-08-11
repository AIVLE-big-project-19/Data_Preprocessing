@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"

echo ========================================
echo  태양광 후보지 전처리 API - Terrain 3x3
echo ========================================

where py > nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=py"
) else (
    set "PYTHON_CMD=python"
)

if not exist ".venv\Scripts\python.exe" (
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto :error
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :error

if not exist ".env" (
    copy ".env.example" ".env" > nul
    echo [안내] .env.example을 .env로 복사했습니다. API 키를 입력하세요.
)

python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

python -c "from api.main import app; print('[확인] FastAPI 앱 로드 성공:', app.title)"
if errorlevel 1 goto :error

echo Swagger: http://127.0.0.1:8001/docs
python -m uvicorn api.main:app --host 127.0.0.1 --port 8001 --reload
set "EXIT_CODE=%errorlevel%"

deactivate > nul 2>&1
endlocal & exit /b %EXIT_CODE%

:error
echo [실패] 실행 준비 중 오류가 발생했습니다.
pause
deactivate > nul 2>&1
endlocal & exit /b 1
