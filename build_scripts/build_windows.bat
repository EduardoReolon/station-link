@echo off
echo --- INICIANDO BUILD STATION LINK (WINDOWS) ---
cd ..

if not exist "venv" (
    echo Criando VENV...
    python -m venv venv
)

echo Instalando dependencias...
venv\Scripts\python.exe -m pip install -r requirements.txt

echo Gerando EXE com PyInstaller...
venv\Scripts\pyinstaller.exe --noconsole --onefile ^
            --add-data "src\templates;templates" ^
            --name "StationLink" ^
            src\main.py

echo.
echo --- SUCESSO! ---
echo O executavel esta na pasta 'dist\StationLink.exe' na raiz do projeto.
pause