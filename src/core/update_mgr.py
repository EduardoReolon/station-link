import os
import time
import threading
import requests

class UpdateManager:
    def __init__(self):
        self.update_url = None
        self.thread_ativa = False
        self.local_version_file = "version.txt"
        
        # Nomes que o Updater.exe vai procurar
        self.update_exe_name = "update_StationLink.exe"
        self.update_txt_name = "update_version.txt"

    def iniciar_verificacao(self, url):
        self.update_url = url.rstrip('/') # Remove a barra no final, se vier
        
        if not self.thread_ativa:
            self.thread_ativa = True
            t = threading.Thread(target=self._loop_verificacao, daemon=True)
            t.start()

    def _obter_versao_local(self):
        if os.path.exists(self.local_version_file):
            with open(self.local_version_file, 'r') as f:
                return f.read().strip()
        return "0.0.0"

    def _loop_verificacao(self):
        while True:
            if self.update_url:
                try:
                    self._checar_e_baixar()
                except Exception as e:
                    # Falha silenciosa: se a internet cair ou o servidor der erro,
                    # ele não trava o sistema, só tenta de novo daqui a 4 horas.
                    pass
            
            # Dorme por 4 horas (14400 segundos) antes da próxima checagem
            time.sleep(14400)
    
    def _obter_versao_pendente(self):
        if os.path.exists(self.update_txt_name):
            try:
                with open(self.update_txt_name, 'r') as f:
                    return f.read().strip()
            except: pass
        return None

    def _checar_e_baixar(self):
        # 1. Puxa a versão da nuvem
        url_versao = f"{self.update_url}/version.txt"
        resp_versao = requests.get(url_versao, timeout=10)
        
        if resp_versao.status_code != 200:
            return
            
        versao_nuvem = resp_versao.text.strip()
        versao_local = self._obter_versao_local()
        versao_pendente = self._obter_versao_pendente()

        # LOGICA DE PROTEÇÃO:
        # Se a versão da nuvem for igual à que já roda OU igual à que já está baixada
        if versao_nuvem == versao_local or versao_nuvem == versao_pendente:
            return

        # 2. É versão nova! Baixa o executável
        url_exe = f"{self.update_url}/StationLink.exe"
        resp_exe = requests.get(url_exe, stream=True, timeout=15)
        
        if resp_exe.status_code == 200:
            # Salva o arquivo aos poucos para não estourar a memória RAM
            with open(self.update_exe_name, 'wb') as f:
                for chunk in resp_exe.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            # 3. Só cria o update_version.txt DEPOIS que o EXE baixou inteiro com sucesso
            with open(self.update_txt_name, 'w') as f:
                f.write(versao_nuvem)

# Instância global para ser importada no routes.py
update_manager = UpdateManager()