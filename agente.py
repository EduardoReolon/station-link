import os
import sys
import json
import threading
import webbrowser
import platform
import base64
import tempfile
import time
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from PIL import Image, ImageDraw

# Configuração Inicial
CONFIG_FILE = 'config.json'
APP_PORT = 4321

# Detecta sistema
SISTEMA = platform.system()

# --- IMPORTAÇÕES ESPECÍFICAS (DRIVER) ---
if SISTEMA == "Windows":
    import win32print
    import win32api
else:
    # [TAG PARA IA]: No futuro, peça para gerar o import do CUPS aqui.
    # Ex: import cups
    pass

app = Flask(__name__)
CORS(app) 

# --- LÓGICA DE PERSISTÊNCIA ---
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {"printer_name": ""}

def save_config(data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f)

# --- LÓGICA DE IMPRESSÃO ---

def listar_impressoras():
    if SISTEMA == "Windows":
        try:
            # Lista impressoras locais e de rede instaladas no Windows
            printers = [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
            return printers
        except:
            return []
    else:
        # [TAG PARA IA]: Implementar listagem de impressoras Linux (CUPS)
        # return conn.getPrinters().keys()
        return ["Linux Printer (Placeholder)"]

def imprimir_raw(conteudo, printer_name):
    """
    Imprime texto puro ou comandos ESC/POS (Guilhotina, Gaveta, Matricial).
    Ideal para Cupom Fiscal e Matriciais.
    """
    if SISTEMA == "Windows":
        try:
            hPrinter = win32print.OpenPrinter(printer_name)
            try:
                hJob = win32print.StartDocPrinter(hPrinter, 1, ("Job ERP RAW", None, "RAW"))
                try:
                    win32print.StartPagePrinter(hPrinter)
                    # Se vier string, converte. Se vier bytes (base64 decodificado), mantém.
                    dados = conteudo.encode('latin1', 'ignore') if isinstance(conteudo, str) else conteudo
                    win32print.WritePrinter(hPrinter, dados) 
                    win32print.EndPagePrinter(hPrinter)
                finally:
                    win32print.EndDocPrinter(hPrinter)
            finally:
                win32print.ClosePrinter(hPrinter)
            return True, "Enviado para spooler RAW"
        except Exception as e:
            return False, str(e)
    else:
        # [TAG PARA IA]: Implementar impressão RAW no Linux (CUPS)
        # conn.printFile(printer_name, file_path, "Job", {})
        return False, "Linux não implementado"

def imprimir_arquivo(base64_data, printer_name):
    """
    Recebe PDF ou Imagem em Base64, salva em temp e manda o Windows imprimir.
    Ideal para A4, Relatórios Gráficos.
    """
    if SISTEMA == "Windows":
        try:
            # 1. Decodifica o Base64 para um arquivo temporário
            # Detecta extensão simples (assumindo PDF por padrão se não informado)
            filename = tempfile.mktemp(suffix=".pdf") 
            
            with open(filename, "wb") as f:
                f.write(base64.b64decode(base64_data))
            
            # 2. Usa o ShellExecute para imprimir via aplicação padrão (Acrobat/Edge)
            # Nota: O comando "printto" permite escolher a impressora específica no Windows
            # Parâmetros: hwnd, operation, file, params (printer), dir, show_cmd
            
            # Tenta usar o comando 'printto' que aceita nome da impressora
            win32api.ShellExecute(0, "printto", filename, f'"{printer_name}"', ".", 0)
            
            # Pequeno delay para garantir que o spooler pegou o arquivo antes de deletar (opcional)
            # time.sleep(5) 
            # os.remove(filename) # Limpeza (cuidado ao remover muito rápido)
            
            return True, "Enviado para o driver da impressora"
        except Exception as e:
            return False, f"Erro ao imprimir arquivo: {str(e)}"
    else:
        # [TAG PARA IA]: Implementar impressão de arquivo no Linux
        # Salvar temp e usar 'lpr -P printer_name filename'
        return False, "Linux não implementado"

# --- ROTAS E INTERFACE ---

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Agente de Impressão</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; max-width: 500px; margin: 30px auto; padding: 20px; background: #f0f2f5; }
        .card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { margin-top: 0; color: #333; font-size: 1.5rem; }
        select, button { width: 100%; padding: 10px; margin-top: 10px; border: 1px solid #ddd; border-radius: 4px; }
        button { background: #007bff; color: white; border: none; cursor: pointer; font-weight: bold; }
        button:hover { background: #0056b3; }
        .status { margin-top: 15px; padding: 10px; border-radius: 4px; display: none; text-align: center; }
        .success { background: #d4edda; color: #155724; }
        .error { background: #f8d7da; color: #721c24; }
    </style>
</head>
<body>
    <div class="card">
        <h1>🖨️ Configuração Local</h1>
        <p>Impressora Padrão:</p>
        <select id="impressoras"><option>Carregando...</option></select>
        <button onclick="salvar()">Salvar Preferência</button>
        <hr style="margin: 20px 0; border: 0; border-top: 1px solid #eee;">
        <button onclick="teste('raw')" style="background: #6c757d;">Teste RAW (Cupom)</button>
        <div id="msg" class="status"></div>
    </div>
    <script>
        const API = 'http://localhost:4321';
        
        async function init() {
            try {
                const res = await fetch(`${API}/api/printers`);
                const data = await res.json();
                const sel = document.getElementById('impressoras');
                sel.innerHTML = '';
                data.available.forEach(p => {
                    let opt = document.createElement('option');
                    opt.value = p; opt.text = p;
                    if(p === data.selected) opt.selected = true;
                    sel.appendChild(opt);
                });
            } catch(e) { alert('Erro ao conectar com o Agente.'); }
        }

        async function salvar() {
            const printer = document.getElementById('impressoras').value;
            await fetch(`${API}/api/config`, {
                method: 'POST', 
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({printer_name: printer})
            });
            aviso('Impressora Salva!', 'success');
        }

        async function teste(tipo) {
            const raw = "TESTE DE IMPRESSAO\\n------------------\\nFuncionou!\\n\\n\\n" + String.fromCharCode(27, 109);
            try {
                const res = await fetch(`${API}/print`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({type: 'raw', content: raw})
                });
                const d = await res.json();
                if(d.status === 'ok') aviso('Enviado com sucesso!', 'success');
                else aviso('Erro: ' + d.error, 'error');
            } catch(e) { aviso('Erro de conexão', 'error'); }
        }

        function aviso(txt, cls) {
            const div = document.getElementById('msg');
            div.innerText = txt; div.className = 'status ' + cls; div.style.display = 'block';
            setTimeout(() => div.style.display = 'none', 3000);
        }
        init();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/printers')
def get_printers():
    config = load_config()
    return jsonify({"available": listar_impressoras(), "selected": config.get("printer_name")})

@app.route('/api/config', methods=['POST'])
def set_config():
    save_config(request.json)
    return jsonify({"status": "ok"})

@app.route('/print', methods=['POST'])
def print_endpoint():
    data = request.json
    tipo = data.get('type', 'raw') # 'raw' ou 'file'
    conteudo = data.get('content', '') # Texto ou Base64
    
    # Define qual impressora usar (a do JSON ou a padrão do config)
    printer_target = data.get('printer', load_config().get("printer_name"))
    
    if not printer_target:
        return jsonify({"status": "error", "error": "Nenhuma impressora configurada"}), 400

    if tipo == 'file':
        # Modo Arquivo (PDF, Imagem)
        ok, msg = imprimir_arquivo(conteudo, printer_target)
    else:
        # Modo RAW (Cupom, Matricial)
        ok, msg = imprimir_raw(conteudo, printer_target)
    
    return jsonify({"status": "ok" if ok else "error", "error": msg if not ok else None}), 200 if ok else 500

# --- SYSTEM TRAY ---
def open_browser(icon, item):
    webbrowser.open(f'http://localhost:{APP_PORT}')

def exit_app(icon, item):
    icon.stop()
    os._exit(0)

def create_icon():
    img = Image.new('RGB', (64, 64), color = (0, 123, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 44, 44], fill=(255, 255, 255))
    return img

if __name__ == '__main__':
    # Thread do Flask
    t = threading.Thread(target=lambda: app.run(port=APP_PORT, use_reloader=False))
    t.daemon = True
    t.start()

    # System Tray
    import pystray
    icon = pystray.Icon("AgenteERP")
    icon.icon = create_icon()
    icon.title = "Agente ERP (Rodando)"
    icon.menu = pystray.Menu(
        pystray.MenuItem("Configurar", open_browser),
        pystray.MenuItem("Sair", exit_app)
    )
    icon.run()