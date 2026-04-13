import threading
import webbrowser
import os

from core.config import APP_PORT, resource_path
from api.routes import api_bp
from api.fiscal_routes import fiscal_bp

# --- FLASK & UI ---
from flask import Flask
from flask_cors import CORS
from PIL import Image, ImageDraw


# ==============================================================================
# 1. INICIALIZAÇÃO DO FLASK (COM CAMINHO CORRIGIDO)
# ==============================================================================

# Aqui está a correção: resource_path já foi definida acima
app = Flask(__name__, template_folder=resource_path('templates'))
CORS(app)

# Registra as rotas no app principal
app.register_blueprint(api_bp)
app.register_blueprint(fiscal_bp)

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