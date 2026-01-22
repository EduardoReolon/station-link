#!/bin/bash
echo "--- INICIANDO BUILD LINUX ---"

# 1. Cria ambiente virtual
if [ ! -d "venv" ]; then
    echo "Criando VENV..."
    python3 -m venv venv
fi

# 2. Ativa e Instala Requirements
echo "Instalando dependencias..."
source venv/bin/activate

# Nota: No Linux, pycups exige bibliotecas do sistema (libcups2-dev)
# Se der erro, instale: sudo apt-get install libcups2-dev
pip install -r requirements.txt

# 3. Gera o Binário
echo "Gerando Binario com PyInstaller..."
pyinstaller --noconsole --onefile --name "LocalPrint-agente" agente.py

echo ""
echo "--- SUCESSO! ---"
echo "O arquivo esta na pasta 'dist/LocalPrint-agente'"