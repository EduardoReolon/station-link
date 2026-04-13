import platform
import logging
import sys
import os

"""Configurações globais, constantes e utilitários de sistema."""

CONFIG_FILE = 'station_config.json'
KEY_FILE = 'station_identity.key'
APP_PORT = 4321
SISTEMA = platform.system()

# Logger setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("StationLink")

def resource_path(relative_path):
    """ Retorna o caminho absoluto para recursos, funcionando tanto em dev quanto no .exe """
    try:
        # Quando roda o .exe, o PyInstaller extrai tudo para a pasta _MEIPASS
        base_path = sys._MEIPASS
        return os.path.join(base_path, relative_path)
    except Exception:
        # Em modo dev, usamos a localização atual do config.py (src/core) 
        # e voltamos um nível (..) para chegar na pasta src/
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        return os.path.join(base_path, relative_path)