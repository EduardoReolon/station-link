@echo off
echo --- INICIANDO BUILD STATION LINK (WINDOWS) ---

if not exist "venv" (
    echo Criando VENV...
    python -m venv venv
)

echo Instalando dependencias...
call venv\Scripts\activate
pip install -r requirements.txt

echo Gerando EXE com PyInstaller...

:: AQUI ESTÁ A MUDANÇA: --add-data "origem;destino"
:: No Windows usa-se ponto-e-vírgula (;)
pyinstaller --noconsole --onefile ^
            --add-data "templates;templates" ^
            --name "StationLink" ^
            station_link.py

echo.
echo --- SUCESSO! ---
echo O arquivo esta na pasta 'dist/StationLink.exe'
pause