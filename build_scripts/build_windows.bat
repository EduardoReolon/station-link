@echo off
echo --- INICIANDO BUILD STATION LINK (WINDOWS) ---
cd ..

if not exist "venv" (
    echo Criando VENV...
    python -m venv venv
)

echo Atualizando PIP e instalando dependencias...
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt

echo Limpando builds anteriores...
if exist "build" rmdir /s /q "build"
if exist "dist\StationLink.exe" del /q "dist\StationLink.exe"

echo Gerando EXE com PyInstaller...
venv\Scripts\pyinstaller.exe --clean --noconsole --onefile ^
            --add-data "src\templates;templates" ^
            --name "StationLink" ^
            src\main.py

:: Dica: Se voce tiver um icone (.ico) para o sistema no futuro,
:: basta adicionar esta linha no comando acima:
:: --icon="caminho\para\seu\icone.ico" ^

echo.
echo --- SUCESSO! ---
echo O executavel esta na pasta 'dist\StationLink.exe' na raiz do projeto.
pause