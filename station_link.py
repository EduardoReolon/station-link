import os
import sys
import json
import threading
import webbrowser
import platform
import base64
import tempfile
import hashlib
import uuid
import logging

# --- CRIPTOGRAFIA ---
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization

# --- FLASK & UI ---
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from PIL import Image, ImageDraw

# ==============================================================================
# 0. CONFIGURAÇÕES GERAIS E FUNÇÕES UTILITÁRIAS
# ==============================================================================

CONFIG_FILE = 'station_config.json'
KEY_FILE = 'station_identity.key'
APP_PORT = 4321
SISTEMA = platform.system()

# Logger setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("StationLink")

# Importações de SO (Windows)
if SISTEMA == "Windows":
    import win32print
    import win32api
    import win32security
    import ntsecuritycon as con

def resource_path(relative_path):
    """ Retorna o caminho absoluto para recursos, funcionando tanto em dev quanto no .exe """
    try:
        # PyInstaller cria uma pasta temporária em _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# ==============================================================================
# 1. INICIALIZAÇÃO DO FLASK (COM CAMINHO CORRIGIDO)
# ==============================================================================

# Aqui está a correção: resource_path já foi definida acima
app = Flask(__name__, template_folder=resource_path('templates'))
CORS(app)

# ==============================================================================
# 2. SECURITY MANAGER (Gerenciador de Identidade)
# ==============================================================================
class StationSecurity:
    def __init__(self):
        self.private_key = None
        self.public_key_pem = None
        self.machine_fingerprint = self._get_machine_fingerprint()
        self.security_source = "UNKNOWN"
        self._load_or_generate_keys()

    def _get_machine_fingerprint(self):
        try:
            mac = uuid.getnode()
            info = f"{SISTEMA}-{platform.node()}-{mac}"
            return hashlib.sha256(info.encode()).hexdigest()
        except Exception:
            return "unknown"

    def _load_or_generate_keys(self):
        # Tenta carregar do arquivo local
        if os.path.exists(KEY_FILE):
            if self._load_from_file():
                self.security_source = "SOFTWARE_FILE"
        else:
            self._generate_new_keys()
            self.security_source = "SOFTWARE_FILE"

    def _generate_new_keys(self):
        self.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        self._save_to_file()
        self._load_public_key()

    def _save_to_file(self):
        pem = self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        with open(KEY_FILE, 'wb') as f:
            f.write(pem)
        self._secure_file_permissions(KEY_FILE)

    def _load_from_file(self):
        try:
            with open(KEY_FILE, "rb") as key_file:
                self.private_key = serialization.load_pem_private_key(
                    key_file.read(),
                    password=None,
                )
            self._load_public_key()
            return True
        except Exception as e:
            logger.error(f"Erro ao ler chave: {e}")
            return False

    def _load_public_key(self):
        public_key = self.private_key.public_key()
        self.public_key_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode('utf-8')

    def _secure_file_permissions(self, filepath):
        try:
            if SISTEMA == "Windows":
                user, domain, type = win32security.LookupAccountName("", win32api.GetUserName())
                sd = win32security.GetFileSecurity(filepath, win32security.DACL_SECURITY_INFORMATION)
                dacl = win32security.ACL()
                dacl.AddAccessAllowedAce(win32security.ACL_REVISION, con.FILE_GENERIC_READ | con.FILE_GENERIC_WRITE, user)
                sd.SetSecurityDescriptorDacl(1, dacl, 0)
                win32security.SetFileSecurity(filepath, win32security.DACL_SECURITY_INFORMATION, sd)
            else:
                os.chmod(filepath, 0o600)
        except Exception as e:
            logger.warning(f"Erro permissões: {e}")

    def sign_payload(self, payload_string):
        data = payload_string.encode('utf-8')
        signature = self.private_key.sign(
            data,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256()
        )
        return signature.hex()

# ==============================================================================
# 3. PRINTER MANAGER
# ==============================================================================
class PrinterManager:
    @staticmethod
    def get_config():
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f: return json.load(f)
        return {"printer_name": ""}

    @staticmethod
    def save_config(data):
        with open(CONFIG_FILE, 'w') as f: json.dump(data, f)

    @staticmethod
    def list_printers():
        if SISTEMA == "Windows":
            try:
                return [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
            except: return []
        return ["Linux Printer"]

    @staticmethod
    def print_raw(content, printer_name):
        if SISTEMA == "Windows":
            try:
                hPrinter = win32print.OpenPrinter(printer_name)
                try:
                    hJob = win32print.StartDocPrinter(hPrinter, 1, ("StationLink RAW", None, "RAW"))
                    try:
                        win32print.StartPagePrinter(hPrinter)
                        dados = content.encode('latin1', 'ignore') if isinstance(content, str) else content
                        win32print.WritePrinter(hPrinter, dados)
                        win32print.EndPagePrinter(hPrinter)
                    finally:
                        win32print.EndDocPrinter(hPrinter)
                finally:
                    win32print.ClosePrinter(hPrinter)
                return True, "Enviado para spooler"
            except Exception as e:
                return False, str(e)
        return False, "Linux não implementado"

    @staticmethod
    def print_file(base64_data, printer_name):
        if SISTEMA == "Windows":
            try:
                filename = tempfile.mktemp(suffix=".pdf")
                with open(filename, "wb") as f:
                    f.write(base64.b64decode(base64_data))
                win32api.ShellExecute(0, "printto", filename, f'"{printer_name}"', ".", 0)
                return True, "Enviado para driver"
            except Exception as e:
                return False, str(e)
        return False, "Linux não implementado"

# Instancia os gerenciadores
security_mgr = StationSecurity()
printer_mgr = PrinterManager()

# ==============================================================================
# 4. ROTAS DA API
# ==============================================================================

@app.route('/identity', methods=['GET'])
def get_identity():
    return jsonify({
        "public_key": security_mgr.public_key_pem,
        "fingerprint": security_mgr.machine_fingerprint,
        "agent_version": "2.1.0",
        "platform": SISTEMA,
        "security_source": security_mgr.security_source
    })

@app.route('/sign', methods=['POST'])
def sign_data():
    data = request.json
    payload = data.get('payload')
    if not payload: return jsonify({"error": "Payload required"}), 400
    try:
        signature_hex = security_mgr.sign_payload(str(payload))
        return jsonify({
            "signature": signature_hex,
            "algorithm": "RSA-SHA256",
            "fingerprint": security_mgr.machine_fingerprint
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/printers', methods=['GET'])
def list_printers():
    conf = printer_mgr.get_config()
    return jsonify({"available": printer_mgr.list_printers(), "selected": conf.get("printer_name")})

@app.route('/api/config', methods=['POST'])
def config_printer():
    printer_mgr.save_config(request.json)
    return jsonify({"status": "ok"})

@app.route('/print', methods=['POST'])
def print_job():
    data = request.json
    ptype = data.get('type', 'raw')
    content = data.get('content', '')
    printer = data.get('printer', printer_mgr.get_config().get("printer_name"))
    
    if not printer: return jsonify({"status": "error", "error": "Impressora não configurada"}), 400
        
    if ptype == 'file':
        ok, msg = printer_mgr.print_file(content, printer)
    else:
        ok, msg = printer_mgr.print_raw(content, printer)
        
    return jsonify({"status": "ok" if ok else "error", "error": msg if not ok else None}), 200 if ok else 500

@app.route('/')
def home():
    # Passa variáveis para o HTML (index.html na pasta templates)
    return render_template('index.html', 
                           fp=security_mgr.machine_fingerprint,
                           sec_source=security_mgr.security_source,
                           public_key=security_mgr.public_key_pem)

# ==============================================================================
# 5. TRAY ICON E EXECUÇÃO
# ==============================================================================

def open_settings(icon, item):
    webbrowser.open(f'http://localhost:{APP_PORT}')

def exit_app(icon, item):
    icon.stop()
    os._exit(0)

def create_icon():
    # Cria um ícone simples roxo
    img = Image.new('RGB', (64, 64), color = (75, 0, 130))
    d = ImageDraw.Draw(img)
    d.ellipse([16, 16, 48, 48], fill=(255, 255, 255))
    return img

if __name__ == '__main__':
    # Roda o Flask em thread separada
    t = threading.Thread(target=lambda: app.run(port=APP_PORT, use_reloader=False))
    t.daemon = True
    t.start()

    # Roda o ícone de bandeja
    import pystray
    icon = pystray.Icon("StationLink")
    icon.icon = create_icon()
    icon.title = "Station Link (Ativo)"
    icon.menu = pystray.Menu(
        pystray.MenuItem("Configurar", open_settings),
        pystray.MenuItem("Sair", exit_app)
    )
    icon.run()