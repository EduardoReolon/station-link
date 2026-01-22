@echo off
echo --- INICIANDO BUILD WINDOWS ---

:: 1. Cria ambiente virtual se nao existir
if not exist "venv" (
    echo Criando VENV...
    python -m venv venv
)

:: 2. Ativa e Instala Requirements
echo Instalando dependencias...
call venv\Scripts\activate
pip install -r requirements.txt

:: 3. Gera o Executavel
echo Gerando EXE com PyInstaller...
pyinstaller --noconsole --onefile --name "LocalPrint-agente" agente.py

echo.
echo --- SUCESSO! ---
echo O arquivo esta na pasta 'dist/LocalPrint-agente.exe'
pause