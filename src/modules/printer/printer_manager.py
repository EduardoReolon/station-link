import json
import base64
import tempfile
import os

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