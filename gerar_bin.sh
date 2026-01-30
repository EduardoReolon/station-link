#!/bin/bash
echo "--- INICIANDO BUILD STATION LINK (LINUX) ---"

# 1. Cria ambiente virtual se não existir
if [ ! -d "venv" ]; then
    echo "Criando VENV..."
    python3 -m venv venv
fi

# 2. Ativa e Instala Requirements
echo "Instalando dependencias..."
source venv/bin/activate

# DICA: Em alguns Linux (Ubuntu/Debian), para compilar criptografia e CUPS, 
# talvez você precise rodar antes: 
# sudo apt-get install build-essential libssl-dev libffi-dev python3-dev
pip install -r requirements.txt

# 3. Gera o Binário
echo "Gerando Binario com PyInstaller..."

# AQUI ESTÁ A MUDANÇA: Usa ':' em vez de ';' no --add-data
pyinstaller --noconsole --onefile \
            --add-data "templates:templates" \
            --name "StationLink" \
            station_link.py

echo ""
echo "--- SUCESSO! ---"
echo "O arquivo esta na pasta 'dist/StationLink'"
echo "Para rodar, use: ./dist/StationLink"