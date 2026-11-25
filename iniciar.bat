@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>nul
title FC27 Clubs - Clube Demo (Porta 8000)
cd /d "%~dp0"

echo.
echo ==========================================================
echo    FC27 CLUBS - EXTRATOR DE AMISTOSOS
echo    Clube: Clube Demo  ^|  ID: 682077
echo ==========================================================
echo.

REM ---------------------------------------------------------
REM 1. Verifica Python
REM ---------------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH.
    echo        Instale o Python 3.12+ em https://python.org
    echo        e marque a opcao "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% encontrado.

REM ---------------------------------------------------------
REM 2. Cria o ambiente virtual se nao existir
REM ---------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [SETUP] Primeira execucao detectada. Criando ambiente virtual...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERRO] Falha ao criar o ambiente virtual.
        pause
        exit /b 1
    )

    echo [SETUP] Instalando dependencias ^(pode levar alguns minutos^)...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
    ".venv\Scripts\python.exe" -m pip install -e ".[dev]" --quiet
    if errorlevel 1 (
        echo.
        echo [AVISO] Falha ao instalar com ".[dev]". Tentando modo simples...
        ".venv\Scripts\python.exe" -m pip install fastapi "uvicorn[standard]" sqlalchemy alembic jinja2 pydantic-settings structlog python-multipart fc-clubs-api httpx pytest pytest-asyncio
        if errorlevel 1 (
            echo [ERRO] Falha ao instalar as dependencias.
            pause
            exit /b 1
        )
    )
    echo [OK] Ambiente virtual pronto.
) else (
    echo [OK] Ambiente virtual encontrado.
)

set PY=".venv\Scripts\python.exe"

REM ---------------------------------------------------------
REM 3. Garante o arquivo .env
REM ---------------------------------------------------------
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo [OK] Arquivo .env criado a partir de .env.example
    ) else (
        echo [ERRO] Nem .env nem .env.example encontrados.
        pause
        exit /b 1
    )
) else (
    echo [OK] Arquivo .env encontrado.
)

REM ---------------------------------------------------------
REM 4. Garante a pasta data
REM ---------------------------------------------------------
if not exist "data" mkdir "data"

REM ---------------------------------------------------------
REM 5. Aplica as migracoes do banco
REM ---------------------------------------------------------
echo.
echo [DB] Verificando o banco de dados...
%PY% -m alembic upgrade head >nul 2>&1
if errorlevel 1 (
    echo [AVISO] Alembic falhou. O banco sera criado pelo proprio app.
) else (
    echo [OK] Banco de dados atualizado.
)

REM ---------------------------------------------------------
REM 6. Verifica se a porta 8000 ja esta em uso
REM ---------------------------------------------------------
netstat -ano | findstr /R /C:"LISTENING.*:8000 " >nul 2>nul
if not errorlevel 1 (
    echo.
    echo [AVISO] A porta 8000 ja esta em uso.
    echo         Feche o outro processo ou altere SERVER_PORT no .env
    echo.
    choice /C SN /M "Continuar mesmo assim"
    if errorlevel 2 exit /b 1
)

REM ---------------------------------------------------------
REM 7. Inicia o navegador alguns segundos depois
REM ---------------------------------------------------------
start "" powershell -WindowStyle Hidden -Command "Start-Sleep -Seconds 5; Start-Process 'http://127.0.0.1:8000'"

REM ---------------------------------------------------------
REM 8. Inicia o worker + API + Dashboard
REM ---------------------------------------------------------
echo.
echo ==========================================================
echo    INICIANDO WORKER + API + DASHBOARD
echo.
echo    Dashboard : http://127.0.0.1:8000
echo    API docs  : http://127.0.0.1:8000/docs
echo    Status    : http://127.0.0.1:8000/status
echo.
echo    Feche esta janela ou pressione Ctrl+C para parar.
echo ==========================================================
echo.

%PY% __main__.py both

echo.
echo [INFO] Aplicacao encerrada.
pause
