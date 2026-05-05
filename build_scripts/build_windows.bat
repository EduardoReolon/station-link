@echo off
echo --- INICIANDO BUILD STATION LINK E UPDATER (WINDOWS) ---
cd ..

if not exist "venv" (
    echo Criando VENV...
    python -m venv venv
)

echo Atualizando dependencias...
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt

echo Atualizando versao...
:: Este script em Python lê o version.txt da raiz (se não existir, cria como 1.0.0), incrementa e salva.
venv\Scripts\python.exe -c "import os; f='version.txt'; v=open(f).read().strip() if os.path.exists(f) else '1.0.0'; p=v.split('.'); p[-1]=str(int(p[-1])+1); nv='.'.join(p); open(f,'w').write(nv); print(f'>>> Versao atual: {nv}')"

echo Limpando builds anteriores...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
mkdir dist

echo Gerando UPDATER...
venv\Scripts\pyinstaller.exe --clean --noconsole --onefile ^
            --name "Updater" ^
            src\updater.py

echo Gerando STATIONLINK...
venv\Scripts\pyinstaller.exe --clean --noconsole --onefile ^
            --add-data "src\templates;templates" ^
            --name "StationLink" ^
            src\main.py

echo Copiando arquivo de versao para dist...
copy version.txt dist\version.txt

echo Verificando Inno Setup...
:: Se você tiver o Inno Setup instalado no padrão, e o seu script chamar 'instalador.iss' na raiz, ele já gera o instalador no final.
set INNO_PATH="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist %INNO_PATH% (
    if exist "build_scripts\instalador.iss" (
        echo Compilando instalador...
        %INNO_PATH% "build_scripts\instalador.iss"
    ) else (
        echo Aviso: 'build_scripts\instalador.iss' nao encontrado na raiz. Pulando criacao do instalador.
    )
)

echo.
echo --- SUCESSO! ---
echo Executaveis e versao estao na pasta 'dist\'.
pause