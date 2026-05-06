import os
import sys
from flask import Blueprint, jsonify, request, render_template
from core.security import StationSecurity
from modules.printer.printer_manager import PrinterManager
from core.config import SISTEMA
from core.update_mgr import update_manager

# 1. As instâncias começam nulas (nada é executado no import)
_security_mgr = None
_printer_mgr = None

# 2. Funções (Getters) que garantem a criação apenas na primeira vez que forem usadas
def get_security_mgr():
    global _security_mgr
    if _security_mgr is None:
        _security_mgr = StationSecurity()
    return _security_mgr

def get_printer_mgr():
    global _printer_mgr
    if _printer_mgr is None:
        _printer_mgr = PrinterManager()
    return _printer_mgr

# Cria o Blueprint (funciona igual ao 'app')
api_bp = Blueprint('api_bp', __name__)

"""Controladores (Controllers) da API Flask para comunicação com o front-end e servidor na nuvem."""

def get_current_version():
    """Descobre onde está o version.txt dependendo se é dev ou prod"""
    if getattr(sys, 'frozen', False):
        # Modo Compilado: pega a pasta onde o .exe está rodando
        base_dir = os.path.dirname(sys.executable)
    else:
        # Modo Dev: assume a pasta raiz de onde você rodou 'python src/main.py'
        base_dir = os.getcwd() 
        
    caminho_versao = os.path.join(base_dir, "version.txt")
    
    try:
        if os.path.exists(caminho_versao):
            with open(caminho_versao, 'r') as f:
                return f.read().strip()
    except Exception:
        pass
        
    return "dev"

@api_bp.route('/identity', methods=['GET'])
def get_identity():
    sec_mgr = get_security_mgr()
    return jsonify({
        "public_key": sec_mgr.public_key_pem,
        "fingerprint": sec_mgr.machine_fingerprint,
        "agent_version": get_current_version(),
        "platform": SISTEMA,
        "security_source": sec_mgr.security_source
    })

@api_bp.route('/sign', methods=['POST'])
def sign_data():
    sec_mgr = get_security_mgr()
    data = request.json
    payload = data.get('payload')
    if not payload: return jsonify({"error": "Payload required"}), 400
    try:
        signature_hex = sec_mgr.sign_payload(str(payload))
        return jsonify({
            "signature": signature_hex,
            "algorithm": "RSA-SHA256",
            "fingerprint": sec_mgr.machine_fingerprint
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route('/api/printers', methods=['GET'])
def list_printers():
    prt_mgr = get_printer_mgr()
    conf = prt_mgr.get_config()
    return jsonify({"available": prt_mgr.list_printers(), "selected": conf.get("printer_name")})

@api_bp.route('/api/config', methods=['POST'])
def config_printer():
    prt_mgr = get_printer_mgr()
    prt_mgr.save_config(request.json)
    return jsonify({"status": "ok"})

@api_bp.route('/print', methods=['POST'])
def print_job():
    prt_mgr = get_printer_mgr()
    data = request.json
    ptype = data.get('type', 'raw')
    qr_code_url = data.get('qr_code_url', '')
    content = data.get('content', '')
    printer = data.get('printer', prt_mgr.get_config().get("printer_name"))
    
    if not printer: return jsonify({"status": "error", "error": "Impressora não configurada"}), 400
        
    if ptype == 'file':
        ok, msg = prt_mgr.print_file(content, printer)
    else:
        ok, msg = prt_mgr.print_raw(content, qr_code_url, printer)
        
    return jsonify({"status": "ok" if ok else "error", "error": msg if not ok else None}), 200 if ok else 500

@api_bp.route('/configurar-update', methods=['POST'])
def configurar_update():
    data = request.json
    update_url = data.get('update_url', '') # Ex: https://meusite.com/downloads
    
    if not update_url:
        return jsonify({"status": "error", "error": "URL de atualização não informada"}), 400
        
    # Salva a URL e acorda a thread de atualização
    update_manager.iniciar_verificacao(update_url)
    
    return jsonify({"status": "ok"}), 200

@api_bp.route('/')
def home():
    sec_mgr = get_security_mgr()
    # Passa variáveis para o HTML (index.html na pasta templates)
    return render_template('index.html', 
                           fp=sec_mgr.machine_fingerprint,
                           sec_source=sec_mgr.security_source,
                           public_key=sec_mgr.public_key_pem,
                           version=get_current_version())