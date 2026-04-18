import base64
import configparser
import ctypes
import datetime
import os
import platform
import sys
from datetime import datetime

class ACBrService:
    def __init__(self):
        self.lib = None
        self._setup_paths()
        self._load_lib()

    def _setup_paths(self):
        # Lógica para PyInstaller (.frozen) vs Ambiente de Desenvolvimento
        if getattr(sys, 'frozen', False):
            # No EXE, sys._MEIPASS é a pasta temporária onde os arquivos são extraídos
            self.base_dir = sys._MEIPASS
        else:
            # Em Dev, sobe 2 níveis a partir de src/core/
            self.base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

        self.log_dir = os.path.join(os.getcwd(), 'logs') # Logs sempre na pasta de execução real
        self.schema_dir = os.path.join(self.base_dir, 'src', 'core', 'acbr', 'Schemas')
        self.acbr_lib_dir = os.path.join(self.base_dir, 'src', 'core', 'acbr')
        
        os.makedirs(self.log_dir, exist_ok=True)

    def _load_lib(self):
        system = platform.system()
        if system == "Windows":
            lib_name = "ACBrNFe64.dll" if "64" in platform.architecture()[0] else "ACBrNFe32.dll"
        elif system == "Linux":
            lib_name = "libacbrnfe64.so"
        else:
            raise Exception(f"Sistema {system} não suportado.")

        lib_path = os.path.join(self.acbr_lib_dir, lib_name)
        self.lib = ctypes.cdll.LoadLibrary(lib_path)

    def inicializar(self):
        config_path = os.path.join(os.getcwd(), 'ACBrLib.ini')
        
        caminho_log_com_barra = self.log_dir.replace('/', '\\')
        if not caminho_log_com_barra.endswith('\\'):
            caminho_log_com_barra += '\\'
        
        # FORÇANDO A QUEBRA DE LINHA DO WINDOWS (\r\n) PARA O DELPHI ENTENDER
        with open(config_path, 'w', encoding='latin-1') as f:
            f.write("[Principal]\r\n")
            f.write("LogNivel=4\r\n")

        res = self.lib.NFE_Inicializar(config_path.encode('utf-8'), "".encode('utf-8'))
        
        if res != 0:
            raise Exception(f"Falha ao inicializar ACBrLib (Erro: {res}).")

        # DESPERTADOR: Pede o Status de Serviço da Sefaz. 
        # Isso obriga a DLL a trabalhar e a gravar o log instantaneamente.
        try:
            buffer = ctypes.create_string_buffer(4096)
            tamanho = ctypes.c_int(4096)
            self.lib.NFE_StatusServico(buffer, ctypes.byref(tamanho))
            print("Ping na Sefaz enviado para forçar geração de log.")
        except Exception as e:
            print(f"Ignorando erro no ping: {e}")
            
        return res

    def _set_config(self, sessao, chave, valor):
        self.lib.NFE_ConfigGravarValor(sessao.encode('utf-8'), chave.encode('utf-8'), valor.encode('utf-8'))
    
    def get_ultimo_retorno(self):
        buffer_len = ctypes.c_int(256)
        buffer = ctypes.create_string_buffer(256)
        
        # Chama a função para ler o erro na memória
        self.lib.NFE_UltimoRetorno(buffer, ctypes.byref(buffer_len))
        
        # O ACBr avisa se o erro for maior que 256 caracteres. Se for, redimensionamos.
        if buffer_len.value > 256:
            buffer = ctypes.create_string_buffer(buffer_len.value)
            self.lib.NFE_UltimoRetorno(buffer, ctypes.byref(buffer_len))
            
        return buffer.value.decode('utf-8', errors='replace').strip()

    def configurar_estacao(self):
        """Configurações globais da DLL (Logs e Schemas)"""
        self._set_config("Principal", "LogPath", self.log_dir)
        self._set_config("Principal", "LogNivel", "4")
        self._set_config("NFe", "PathSchemas", self.schema_dir)
        # Salva XMLs temporários na pasta de logs para debug
        self._set_config("NFe", "PathSalvar", os.path.join(self.log_dir, 'xmls'))

    def preparar_empresa(self, company_info, ambiente):
        """Injeta as credenciais da empresa e o ambiente da nota atual"""
        self._set_config("Certificado", "DadosPFX", company_info['cert_base64'])
        self._set_config("Certificado", "Senha", company_info['senha'])
        self._set_config("Certificado", "CNPJ", company_info['cnpj'])
        self._set_config("NFe", "Ambiente", str(ambiente))
        self._set_config("NFe", "AtualizarXMLCancelado", "1")
    
    def converter_json_para_ini(self, company_info, payload):
        """
        Converte o JSON do NestJS para o formato INI exigido pelo ACBr.
        Garante a inclusão das tags da Reforma Tributária (IBS/CBS).
        """
        crt_empresa = company_info.get('crt', 1)
        # O backend envia 'address', não 'endereco'
        emitente_endereco = company_info.get('address', {})
        
        # Limpeza da data (já estás a fazer corretamente, mas garante que usas a variável dhEmi_acbr)
        data_js = payload.get("issueDate")
        data_limpa = data_js.split('.')[0]
        dt = datetime.strptime(data_limpa, "%Y-%m-%dT%H:%M:%S")
        dhEmi_acbr = dt.strftime("%d/%m/%Y %H:%M:%S")

        ini_lines = []
        ini_lines.append("[infNFe]")
        ini_lines.append("versao=4.00")
        
        ini_lines.append("[Ide]")
        ini_lines.append(f"tpAmb={payload.get('environment', 2)}")
        ini_lines.append(f"mod={payload['model']}")
        ini_lines.append(f"serie={payload['series']}")
        ini_lines.append(f"nNF={payload['number']}")
        ini_lines.append(f"dhEmi={dhEmi_acbr}")
        ini_lines.append(f"cMunFG={emitente_endereco.get('ibgeCode') if emitente_endereco.get('ibgeCode') else '4114302'}")
        ini_lines.append("tpEmis=1")
        ini_lines.append(f"tpImp={'4' if str(payload['model']) == '65' else '1'}") 
        ini_lines.append("finNFe=1")
        
        ind_final = "1" if str(payload.get('model')) == '65' else ("1" if payload.get('customer', {}).get('isFinalConsumer') else "0")
        ini_lines.append(f"indFinal={ind_final}")
        ini_lines.append("indPres=1")

        # --- EMITENTE (Mapeado com as chaves corretas do JSON) ---
        ini_lines.append("[Emitente]")
        ini_lines.append(f"CNPJ={company_info.get('cnpj', '')}") # Pega a chave que já existia
        ini_lines.append(f"IE={company_info.get('ie', '')}") 
        ini_lines.append(f"xNome={company_info.get('name', 'Razao Social Omitida')}")
        ini_lines.append(f"CRT={company_info.get('crt', 1)}") 

        ini_lines.append(f"xLgr={emitente_endereco.get('street', '')}")
        ini_lines.append(f"nro={emitente_endereco.get('number', '')}")
        ini_lines.append(f"xBairro={emitente_endereco.get('district', '')}")
        ibge_code = emitente_endereco.get('ibgeCode')
        ini_lines.append(f"cMun={ibge_code if ibge_code else '4114302'}")
        ini_lines.append(f"xMun={emitente_endereco.get('city', '')}")
        ini_lines.append(f"UF={emitente_endereco.get('state', '')}")
        ini_lines.append(f"CEP={emitente_endereco.get('zipcode', '')}")

        # --- DESTINATÁRIO ---
        customer = payload.get('customer', {})
        if customer and customer.get('document'):
            ini_lines.append("[Destinatario]")
            doc = customer['document']
            if len(doc) == 11:
                ini_lines.append(f"CPF={doc}")
            else:
                ini_lines.append(f"CNPJ={doc}")
            
            ini_lines.append(f"xNome={customer.get('name', 'CONSUMIDOR')}")
            ini_lines.append(f"indIEDest={customer.get('indIEDest', 9)}")
            
            if customer.get('ie'):
                ini_lines.append(f"IE={customer['ie']}")
            if customer.get('email'):
                ini_lines.append(f"email={customer['email']}")
                
            end_dest = customer.get('address', {})
            if end_dest:
                ini_lines.append(f"xLgr={end_dest.get('street', '')}")
                ini_lines.append(f"nro={end_dest.get('number', '')}")
                ini_lines.append(f"xBairro={end_dest.get('district', '')}")
                ini_lines.append(f"cMun={end_dest.get('ibgeCode', '')}")
                ini_lines.append(f"xMun={end_dest.get('city', '')}")
                ini_lines.append(f"UF={end_dest.get('state', '')}")
                ini_lines.append(f"CEP={end_dest.get('zipcode', '')}")

        # --- ITENS E TRIBUTOS ---
        for idx, item in enumerate(payload.get('items', [])):
            str_idx = f"{idx+1:03d}" 
            
            ini_lines.append(f"[Produto{str_idx}]")
            ini_lines.append(f"cProd={item['gtin']}")
            ini_lines.append(f"cEAN={item['gtin']}")
            ini_lines.append(f"xProd={item['description']}")
            ini_lines.append(f"NCM={item['ncm']}")
            ini_lines.append(f"CFOP={item['cfop']}")
            ini_lines.append(f"uCom={item['unit']}")
            ini_lines.append(f"qCom={item['quantity']}")
            ini_lines.append(f"vUnCom={item['unitPrice']}")
            ini_lines.append(f"cEANTrib={item['gtin']}")
            ini_lines.append(f"uTrib={item['unit']}")
            ini_lines.append(f"qTrib={item['quantity']}")
            ini_lines.append(f"vUnTrib={item['unitPrice']}")
            ini_lines.append("indTot=1")
            
            # --- BLOCO ICMS ---
            ini_lines.append(f"[ICMS{str_idx}]")
            ini_lines.append(f"orig={item.get('origem', 0)}")
            
            if str(crt_empresa) == "1":
                csosn = "102" if item.get('cstIcms', '') == '000' else item.get('cstIcms', '102')
                ini_lines.append(f"CSOSN={csosn}")
                
                # Para CSOSN 102, 103, 300, 400 NÃO se envia vBC, pICMS ou vICMS
                if csosn not in ['102', '103', '300', '400']:
                    ini_lines.append(f"vBC={item.get('icmsBase', '0.00')}")
                    ini_lines.append(f"pICMS={item.get('icmsRate', '0.00')}")
                    ini_lines.append(f"vICMS={item.get('icmsValue', '0.00')}")
            else:
                ini_lines.append(f"CST={item.get('cstIcms', '000')}")
                ini_lines.append(f"vBC={item.get('icmsBase', '0.00')}")
                ini_lines.append(f"pICMS={item.get('icmsRate', '0.00')}")
                ini_lines.append(f"vICMS={item.get('icmsValue', '0.00')}")

            # --- BLOCO REFORMA TRIBUTÁRIA (Com a sua hierarquia de subgrupos correta) ---
            if item.get('cstIbsCbs'):
                ini_lines.append(f"[IBSCBS{str_idx}]")
                ini_lines.append(f"CST={item['cstIbsCbs']}")
                
                ini_lines.append(f"[gIBSCBS{str_idx}]")
                ini_lines.append(f"vBC={item.get('ibsBase', '0.00')}") 
                
                ini_lines.append(f"[gIBS{str_idx}]")
                ini_lines.append(f"pIBS={item.get('ibsRate', '0.00')}")
                ini_lines.append(f"vIBS={item.get('ibsValue', '0.00')}")
                
                ini_lines.append(f"[gCBS{str_idx}]")
                ini_lines.append(f"pCBS={item.get('cbsRate', '0.00')}")
                ini_lines.append(f"vCBS={item.get('cbsValue', '0.00')}")

        # --- PAGAMENTOS ---
        for idx, pag in enumerate(payload.get('payments', [])):
            str_idx = f"{idx+1:03d}"
            ini_lines.append(f"[Pag{str_idx}]")
            ini_lines.append(f"tPag={pag['method']}")
            ini_lines.append(f"vPag={pag['value']}")
            if 'cardIndicator' in pag:
                ini_lines.append(f"tpIntegra={pag['cardIndicator']}")

        return "\n".join(ini_lines)
    
    def imprimir_documento(self, xml_string, doc_type, return_pdf=False, printer_name=None):
        """
        PLACEHOLDER: Função para ser implementada no futuro.
        
        Args:
            xml_string (str): O XML completo e autorizado (ou de evento).
            doc_type (str): "NFE", "NFCE", "CCE", "CANCELAMENTO".
            return_pdf (bool): Se True, não envia para spooler, gera PDF em Base64 e retorna.
            printer_name (str): Nome da impressora no SO. Se None, usa padrão.
            
        Notas para a futura implementação:
        1. Carregar o XML em memória com NFE_CarregarXML(xml_string).
        2. Se return_pdf for True: chamar NFE_ImprimirPDF e ler o arquivo gerado para Base64.
        3. Se for NFCe ("65"): Avaliar se a impressora é ESC/POS (Bobina contínua). 
           - Pode ser necessário usar NFE_Imprimir, ou ACBrPosPrinter para impressão raw direta na porta COM/USB.
        4. Se for NFe ("55"): Impressão A4 padrão via NFE_Imprimir.
        """
        pass

    def _obter_retorno_ini(self):
        """Lê o retorno da DLL em formato INI e converte para dicionário"""
        buffer = ctypes.create_string_buffer(4096)
        tamanho = ctypes.c_int(4096)
        
        # Chama a função nativa correta da DLL (sem o índice 0)
        if self.lib.NFE_UltimoRetorno(buffer, ctypes.byref(tamanho)) == 0:
            ini_str = buffer.value.decode('utf-8')
            parser = configparser.ConfigParser(strict=False)
            parser.read_string(ini_str)
            return parser
            
        return None

    def _obter_xml_memoria(self):
        """Extrai o XML assinado/autorizado direto da memória do ACBr"""
        buffer = ctypes.create_string_buffer(8192) # Buffer maior para XML
        tamanho = ctypes.c_int(8192)
        if self.lib.NFE_ObterXml(0, buffer, ctypes.byref(tamanho)) == 0:
            return buffer.value.decode('utf-8')
        return None

    def realizar_backup_xml(self, company_id, access_key, xml_content, suffix=""):
        """Gravação organizada: backups/{company_id}/{ano-mes}/{chave}{suffix}.xml"""
        backup_dir = os.path.join(self.base_dir, 'backups', company_id, datetime.date.today().strftime("%Y-%m"))
        os.makedirs(backup_dir, exist_ok=True)
        
        file_path = os.path.join(backup_dir, f"{access_key}{suffix}.xml")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(xml_content)

    def emitir_nota(self, ini_string, company_id, auto_print=False):
        """Fluxo completo com Base64, Totais, Backup e Contingência."""
        self.lib.NFE_LimparLista()

        # ==========================================
        # FASE 1: VALIDAÇÃO ESTRUTURAL (Não entra em contingência)
        # ==========================================
        if self.lib.NFE_CarregarINI(ini_string.encode('utf-8')) != 0:
            # 1. Busca o erro detalhado da DLL
            buffer = ctypes.create_string_buffer(4096)
            tamanho = ctypes.c_int(4096)
            self.lib.NFE_UltimoRetorno(buffer, ctypes.byref(tamanho))
            erro_real = buffer.value.decode('utf-8', errors='ignore')

            # 2. Salva o arquivo de debug APENAS aqui
            caminho_debug = os.path.join(self.log_dir, f"ERRO_ini_{company_id}.txt")
            with open(caminho_debug, "w", encoding="utf-8") as f:
                f.write(ini_string)

            print(f"Falha no CarregarINI. Log de erro gerado: {caminho_debug}")

            return {
                "status": "error", 
                "message": f"Erro de Estrutura INI: {erro_real}",
                "debug_file": caminho_debug
            }
        
        if self.lib.NFE_Assinar() != 0:
            return {"status": "error", "message": "Erro ao assinar XML. Verifique o certificado."}

        # ==========================================
        # FASE 2: TRANSMISSÃO (AQUI entra a contingência)
        # ==========================================
        try:
            buffer = ctypes.create_string_buffer(4096)
            tamanho = ctypes.c_int(4096)
            
            # Se a SEFAZ estiver fora, essa função vai estourar um erro/retornar diferente de 0
            resultado_envio = self.lib.NFE_Enviar(1, 0, 1, buffer, ctypes.byref(tamanho))
            
            if resultado_envio != 0:
                raise Exception("Falha na comunicação com a SEFAZ.")

            # ... (Toda a sua lógica normal de sucesso de leitura do retorno continua aqui) ...
            dados_retorno = self._obter_retorno_ini()
            xml_puro = self._obter_xml_memoria()
            
            if not dados_retorno or not xml_puro:
                raise Exception("Falha ao ler retorno da SEFAZ na memória.")

            xml_base64 = base64.b64encode(xml_puro.encode('utf-8')).decode('utf-8')
            secao_nfe = [s for s in dados_retorno.sections() if s.startswith('NFe')][0]
            cStat = int(dados_retorno.get(secao_nfe, 'cStat', fallback=0))
            access_key = dados_retorno.get(secao_nfe, 'chDFe', fallback='')
            
            resultado = {
                "accessKey": access_key,
                "protocol": dados_retorno.get(secao_nfe, 'nProt', fallback=''),
                "receiptNumber": dados_retorno.get(secao_nfe, 'nRec', fallback=''),
                "xmlBase64": xml_base64, 
                "sefaz": {
                    "cStat": cStat,
                    "message": dados_retorno.get(secao_nfe, 'xMotivo', fallback='')
                },
                "totals": {
                    "totalProducts": float(dados_retorno.get(secao_nfe, 'vProd', fallback=0.0).replace(',', '.')),
                    "totalDocument": float(dados_retorno.get(secao_nfe, 'vNF', fallback=0.0).replace(',', '.')),
                    "totalIcms": float(dados_retorno.get(secao_nfe, 'vICMS', fallback=0.0).replace(',', '.'))
                }
            }

            if cStat in [100, 150]:
                resultado["status"] = "AUTHORIZED"
                self.realizar_backup_xml(company_id, access_key, xml_puro)
                if auto_print:
                    self.imprimir_documento(xml_puro, "NFE") 
            else:
                resultado["status"] = "REJECTED"
                
            return resultado

        # SÓ CAI AQUI SE DEU PAU DE INTERNET / SEFAZ OFFLINE
        except Exception as e:
            print(f"Ativando contingência: {e}")
            self._set_config("NFe", "FormaEmissao", "9") 
            
            # Reassina o XML que já está carregado na memória (agora com a tag tpEmis=9)
            self.lib.NFE_Assinar() 
            
            xml_contingencia_puro = self._obter_xml_memoria()
            dados_retorno = self._obter_retorno_ini()
            
            # Como a nota FOI gerada na memória com sucesso (passou da Fase 1), 
            # a seção e a chave existirão com certeza.
            secao_nfe = [s for s in dados_retorno.sections() if s.startswith('NFe')][0]
            access_key = dados_retorno.get(secao_nfe, 'chDFe', fallback='')
            
            xml_contingencia_b64 = base64.b64encode(xml_contingencia_puro.encode('utf-8')).decode('utf-8')
            self.realizar_backup_xml(company_id, f"{access_key}_CONT", xml_contingencia_puro)
            
            if auto_print:
                self.imprimir_documento(xml_contingencia_puro, "NFCE")

            return {
                "status": "contingency",
                "accessKey": access_key,
                "protocol": None,
                "receiptNumber": None,
                "xmlBase64": xml_contingencia_b64,
                "sefaz": {"cStat": 0, "message": "Emitido em contingência offline."},
                "totals": {} 
            }
    
    def transmitir_contingencia(self, xml_base64, company_id, auto_print=False):
        """Transmite um XML gerado em contingência e atualiza o arquivo físico"""
        try:
            self.lib.NFE_LimparLista()
            
            # Decodifica o Base64 que veio do banco e carrega no ACBr
            xml_puro = base64.b64decode(xml_base64).decode('utf-8')
            if self.lib.NFE_CarregarXML(xml_puro.encode('utf-8')) != 0:
                raise Exception("Erro ao carregar XML de contingência.")
            
            # Envia sem re-assinar (Lote=1, Imprimir=0, Sincrono=1)
            buffer = ctypes.create_string_buffer(4096)
            tamanho = ctypes.c_int(4096)
            self.lib.NFE_Enviar(1, 0, 1, buffer, ctypes.byref(tamanho))
            
            dados_retorno = self._obter_retorno_ini()
            xml_autorizado = self._obter_xml_memoria()
            
            if not dados_retorno or not xml_autorizado:
                raise Exception("Falha ao ler retorno da SEFAZ na memória.")

            secao_nfe = [s for s in dados_retorno.sections() if s.startswith('NFe')][0]
            cStat = int(dados_retorno.get(secao_nfe, 'cStat', fallback=0))
            access_key = dados_retorno.get(secao_nfe, 'chDFe', fallback='')
            
            resultado = {
                "status": "REJECTED",
                "accessKey": access_key,
                "protocol": dados_retorno.get(secao_nfe, 'nProt', fallback=''),
                "xmlBase64": base64.b64encode(xml_autorizado.encode('utf-8')).decode('utf-8'),
                "sefaz": {
                    "cStat": cStat,
                    "message": dados_retorno.get(secao_nfe, 'xMotivo', fallback='')
                }
            }

            if cStat in [100, 150]:
                resultado["status"] = "AUTHORIZED"
                
                # Salva o novo autorizado
                self.realizar_backup_xml(company_id, access_key, xml_autorizado)
                
                # Apaga o arquivo offline antigo (limpeza)
                old_file = os.path.join(self.base_dir, 'backups', company_id, datetime.date.today().strftime("%Y-%m"), f"{access_key}_CONT.xml")
                if os.path.exists(old_file):
                    os.remove(old_file)
                
                if auto_print:
                    self.imprimir_documento(xml_autorizado, "NFCE")

            return resultado

        except Exception as e:
            return {"status": "ERROR", "message": str(e)}

    def cancelar_documento(self, access_key, justification, cnpj, company_id):
        """Cancela uma nota e salva o XML do evento no disco"""
        try:
            buffer = ctypes.create_string_buffer(4096)
            tamanho = ctypes.c_int(4096)
            
            res = self.lib.NFE_Cancelar(
                access_key.encode('utf-8'), 
                justification.encode('utf-8'), 
                cnpj.encode('utf-8'), 
                1, buffer, ctypes.byref(tamanho)
            )
            
            if res != 0:
                raise Exception("Erro ao processar comando de cancelamento.")
                
            dados_retorno = self._obter_retorno_ini()
            xml_evento = self._obter_xml_memoria() # Puxa o XML do evento de cancelamento
            
            secao_canc = [s for s in dados_retorno.sections() if s.startswith('Cancelamento')][0]
            cStat = int(dados_retorno.get(secao_canc, 'cStat', fallback=0))
            
            if cStat in [135, 136, 155] and xml_evento:
                # Salva com sufixo para agrupar com a nota original no Windows
                self.realizar_backup_xml(company_id, access_key, xml_evento, suffix="-canc")
            
            return {
                "status": "CANCELED" if cStat in [135, 136, 155] else "REJECTED",
                "cancelProtocol": dados_retorno.get(secao_canc, 'nProt', fallback=''),
                "cancelXmlBase64": base64.b64encode(xml_evento.encode('utf-8')).decode('utf-8') if xml_evento else "", # NOME CORRIGIDO AQUI
                "sefaz": {
                    "cStat": cStat,
                    "message": dados_retorno.get(secao_canc, 'xMotivo', fallback='')
                }
            }
        except Exception as e:
            return {"status": "ERROR", "message": str(e)}

    def carta_correcao(self, access_key, justification, cnpj, sequence, company_id):
        """Emite CC-e e salva o XML do evento no disco"""
        try:
            buffer = ctypes.create_string_buffer(4096)
            tamanho = ctypes.c_int(4096)
            
            res = self.lib.NFE_CartaDeCorrecao(
                access_key.encode('utf-8'), 
                justification.encode('utf-8'), 
                sequence, 
                cnpj.encode('utf-8'), 
                1, buffer, ctypes.byref(tamanho)
            )
            
            if res != 0:
                raise Exception("Erro ao processar CC-e.")
                
            dados_retorno = self._obter_retorno_ini()
            xml_evento = self._obter_xml_memoria()
            
            secao_cce = [s for s in dados_retorno.sections() if s.startswith('CartaDeCorrecao')][0]
            cStat = int(dados_retorno.get(secao_cce, 'cStat', fallback=0))
            
            if cStat in [135, 136] and xml_evento:
                # Salva com o número da sequência para não sobrescrever correções anteriores
                self.realizar_backup_xml(company_id, access_key, xml_evento, suffix=f"-cce{sequence}")

            return {
                "status": "AUTHORIZED" if cStat in [135, 136] else "REJECTED",
                "protocol": dados_retorno.get(secao_cce, 'nProt', fallback=''),
                "xmlBase64": base64.b64encode(xml_evento.encode('utf-8')).decode('utf-8') if xml_evento else "",
                "sefaz": {
                    "cStat": cStat,
                    "message": dados_retorno.get(secao_cce, 'xMotivo', fallback='')
                }
            }
        except Exception as e:
            return {"status": "ERROR", "message": str(e)}

# Instância global
acbr_instance = ACBrService()