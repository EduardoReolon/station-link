import os
import sys
import subprocess
import time

UPDATE_EXE = "update_StationLink.exe"
UPDATE_VERSAO = "update_version.txt"

APP_EXE = "StationLink.exe"
APP_VERSAO = "version.txt"

def aplicar_atualizacao():
    # Só atualiza se ambos os arquivos foram baixados com sucesso
    if os.path.exists(UPDATE_EXE) and os.path.exists(UPDATE_VERSAO):
        # Pequeno delay para garantir que o StationLink (se estivesse aberto) morreu completamente
        time.sleep(1)
        
        try:
            # Substitui o executável
            if os.path.exists(APP_EXE):
                os.remove(APP_EXE)
            os.rename(UPDATE_EXE, APP_EXE)
            
            # Substitui a versão
            if os.path.exists(APP_VERSAO):
                os.remove(APP_VERSAO)
            os.rename(UPDATE_VERSAO, APP_VERSAO)
        except Exception:
            # Se der erro de permissão, o Updater ignora e tenta abrir a versão que estiver lá
            pass

if __name__ == '__main__':
    aplicar_atualizacao()
    
    # Inicia o sistema principal de forma independente e morre
    if os.path.exists(APP_EXE):
        subprocess.Popen(APP_EXE)
        
    sys.exit(0)