from flask import Blueprint, jsonify, request, render_template
from core.security import StationSecurity
from modules.printer.printer_manager import PrinterManager
from core.config import SISTEMA

# Instancia os gerenciadores aqui (ou importe as instâncias se preferir criar nos módulos)
security_mgr = StationSecurity()
printer_mgr = PrinterManager()

# Cria o Blueprint (funciona igual ao 'app')
api_bp = Blueprint('api_bp', __name__)

"""Controladores (Controllers) da API Flask para comunicação com o front-end e servidor na nuvem."""

@api_bp.route('/identity', methods=['GET'])
def get_identity():
    return jsonify({
        "public_key": security_mgr.public_key_pem,
        "fingerprint": security_mgr.machine_fingerprint,
        "agent_version": "2.1.0",
        "platform": SISTEMA,
        "security_source": security_mgr.security_source
    })

@api_bp.route('/sign', methods=['POST'])
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

@api_bp.route('/api/printers', methods=['GET'])
def list_printers():
    conf = printer_mgr.get_config()
    return jsonify({"available": printer_mgr.list_printers(), "selected": conf.get("printer_name")})

@api_bp.route('/api/config', methods=['POST'])
def config_printer():
    printer_mgr.save_config(request.json)
    return jsonify({"status": "ok"})

@api_bp.route('/print', methods=['POST'])
def print_job():
    data = request.json
    ptype = data.get('type', 'raw')
    qr_code_url = data.get('qr_code_url', '')
    content = data.get('content', '')
    printer = data.get('printer', printer_mgr.get_config().get("printer_name"))
    
    if not printer: return jsonify({"status": "error", "error": "Impressora não configurada"}), 400
        
    if ptype == 'file':
        ok, msg = printer_mgr.print_file(content, printer)
    else:
        ok, msg = printer_mgr.print_raw(content, qr_code_url, printer)
        
    return jsonify({"status": "ok" if ok else "error", "error": msg if not ok else None}), 200 if ok else 500

@api_bp.route('/')
def home():
    # Passa variáveis para o HTML (index.html na pasta templates)
    return render_template('index.html', 
                           fp=security_mgr.machine_fingerprint,
                           sec_source=security_mgr.security_source,
                           public_key=security_mgr.public_key_pem)