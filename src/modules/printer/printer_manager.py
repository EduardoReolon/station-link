import json
import base64
import tempfile
import os
import platform
from escpos.printer import Win32Raw, Network, File

from core.config import CONFIG_FILE, SISTEMA

# Importações de SO (Windows)
if SISTEMA == "Windows":
    import win32print
    import win32api

"""Interação nativa com o spooler do sistema operacional para listagem e envio de impressões."""

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
                import win32print
                return [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
            except:
                return []
        
        elif SISTEMA == "Linux":
            try:
                import subprocess
                # O comando 'lpstat -a' lista todas as impressoras instaladas no Linux
                result = subprocess.run(["lpstat", "-a"], capture_output=True, text=True)
                printers = []
                for line in result.stdout.split('\n'):
                    if line:
                        # A primeira palavra da linha é o nome da impressora
                        printers.append(line.split()[0]) 
                
                # Se não achar nenhuma configurada, retorna as portas USB genéricas (fallback para ESC/POS)
                if not printers:
                    return ["/dev/usb/lp0", "/dev/usb/lp1", "/dev/ttyS0"]
                return printers
            except:
                # Fallback de emergência no Linux
                return ["/dev/usb/lp0", "/dev/usb/lp1"]
        
        return []

    @staticmethod
    def print_raw(texto_puro, qr_code_url=None, printer_name=None, is_network=False, ip_address=None):
        """
        Recebe o texto cru, imprime e corta. Usa o hardware da impressora para o QR Code.
        """
        try:
            impressora = None
            sistema = platform.system()

            # ==========================================
            # 1. CONEXÃO MULTIPLATAFORMA
            # ==========================================
            if is_network and ip_address:
                # Impressoras Ethernet/Wi-Fi funcionam igual em qualquer S.O.
                impressora = Network(ip_address)
                
            elif sistema == "Windows":
                # No Windows, usamos o nome do compartilhamento nativo
                if not printer_name:
                    return False, "Nome da impressora não fornecido para Windows."
                impressora = Win32Raw(printer_name)
                
            elif sistema == "Linux":
                # No Linux, escrevemos direto na porta USB/Serial da impressora
                # Se o front-end não mandar a porta, assumimos o padrão /dev/usb/lp0
                device_path = printer_name if printer_name else "/dev/usb/lp0"
                impressora = File(device_path)
                
            else:
                return False, f"Sistema operacional {sistema} não suportado."

            if not impressora:
                 return False, "Falha ao inicializar a impressora."

            # ==========================================
            # 2. IMPRESSÃO DO CONTEÚDO
            # ==========================================
            impressora.set(align='left')
            impressora.text(texto_puro)
            
            # ==========================================
            # 3. IMPRESSÃO DO QR CODE (Nativo do Chip)
            # ==========================================
            if qr_code_url:
                # Se a sua URL do Front chegar aqui, centralizamos e deixamos a impressora desenhar
                impressora.set(align='center')
                impressora.qr(qr_code_url, size=4) # size 4 é o tamanho ideal para bobinas de 80mm
            
            # ==========================================
            # 4. FINALIZAÇÃO E GUILHOTINA
            # ==========================================
            # Avança 4 linhas para o QR code ou texto passarem da lâmina de corte
            impressora.control("LF")
            impressora.control("LF")
            impressora.control("LF")
            impressora.control("LF")
            
            impressora.cut()
            impressora.close()
            
            return True, "Enviado e cortado com sucesso."

        except Exception as e:
            import traceback
            traceback.print_exc()
            return False, f"Erro na impressão: {str(e)}"

    @staticmethod
    def print_file(base64_data, printer_name):
        try:
            # Cria o arquivo temporário válido para ambos os S.O.
            filename = tempfile.mktemp(suffix=".pdf")
            with open(filename, "wb") as f:
                f.write(base64.b64decode(base64_data))

            if SISTEMA == "Windows":
                import win32api
                # Manda o Windows abrir o leitor de PDF padrão e imprimir silenciosamente
                win32api.ShellExecute(0, "printto", filename, f'"{printer_name}"', ".", 0)
                return True, "Enviado para o spooler do Windows"
            
            elif SISTEMA == "Linux":
                import subprocess
                # 'lp' é o comando universal do Linux/CUPS para imprimir arquivos
                subprocess.run(["lp", "-d", printer_name, filename], check=True)
                return True, "Enviado para o spooler do Linux"
            
            else:
                return False, f"Sistema {SISTEMA} não suportado para PDF"

        except Exception as e:
            import traceback
            traceback.print_exc()
            return False, f"Erro ao imprimir arquivo: {str(e)}"