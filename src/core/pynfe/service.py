import base64
import os
import re
import tempfile
import traceback
from contextlib import contextmanager
from lxml import etree

from pynfe.processamento.assinatura import AssinaturaA1
from pynfe.processamento.comunicacao import ComunicacaoSefaz
from pynfe.processamento.serializacao import SerializacaoQrcode
import requests

from core.pynfe.builders import NFeBuilder, AmbienteEmissao, ModeloDocumento

DICIONARIO_TAGS_NFE = {
    "ide": "Identificação da NF-e",
    "cUF": "Código da UF",
    "cNF": "Código Numérico",
    "natOp": "Natureza da Operação",
    "mod": "Modelo do Documento Fiscal",
    "serie": "Série do Documento",
    "nNF": "Número da NF-e",
    "dhEmi": "Data e Hora de Emissão",
    "tpNF": "Tipo de Operação (0-Entrada, 1-Saída)",
    "idDest": "Indicador de Destino da Operação",
    "cMunFG": "Código do Município do Fato Gerador",
    "tpImp": "Formato de Impressão do DANFE",
    "tpEmis": "Tipo de Emissão (Normal, Contingência, etc)",
    "tpAmb": "Ambiente (1-Produção, 2-Homologação)",
    "finNFe": "Finalidade de Emissão da NF-e",
    "indFinal": "Indicador de Consumidor Final",
    "indPres": "Indicador de Presença do Comprador",
    "CRT": "Código de Regime Tributário",
    "CNPJ": "CNPJ",
    "CPF": "CPF",
    "IE": "Inscrição Estadual",
    "xNome": "Razão Social ou Nome",
    "xLgr": "Logradouro (Rua, Avenida, etc)",
    "nro": "Número do Endereço",
    "cMun": "Código do Município (IBGE)",
    "UF": "Sigla da UF",
    "CEP": "CEP",
    "cProd": "Código do Produto",
    "cEAN": "Código de Barras EAN",
    "xProd": "Descrição do Produto",
    "NCM": "Código NCM",
    "CEST": "Código Especificador da Substituição Tributária",
    "CFOP": "Código Fiscal de Operações e Prestações",
    "uCom": "Unidade Comercial",
    "qCom": "Quantidade Comercial",
    "vUnCom": "Valor Unitário de Comercialização",
    "vProd": "Valor Total Bruto dos Produtos",
    "cEANTrib": "Código de Barras EAN Tributável",
    "vFrete": "Valor do Frete",
    "vDesc": "Valor do Desconto",
    "orig": "Origem da Mercadoria",
    "CST": "Código de Situação Tributária",
    "CSOSN": "Código de Situação da Operação - Simples Nacional",
    "vBC": "Valor da Base de Cálculo",
    "pICMS": "Alíquota do ICMS",
    "vICMS": "Valor do ICMS",
    "vNF": "Valor Total da NF-e",
    "modFrete": "Modalidade do Frete",
    "tPag": "Meio de Pagamento",
    "vPag": "Valor Pago",
    "infCpl": "Informações Complementares"
}

class PyNFEService:    
    def __init__(self):
        pass

    @contextmanager
    def _certificado_temporario(self, cert_base64: str):
        """Gerenciador de contexto para garantir que o arquivo temp seja excluído"""
        pfx_bytes = base64.b64decode(cert_base64)
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pfx') as temp_cert:
            temp_cert.write(pfx_bytes)
            temp_path = temp_cert.name
        try:
            yield temp_path
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _get_comunicacao(self, company_info, payload, cert_path):
        """Instancia a classe de comunicação da PyNFe"""
        ambiente = int(payload.get('environment', AmbienteEmissao.HOMOLOGACAO.value))
        return ComunicacaoSefaz(
            uf=company_info['address']['state'],
            certificado=cert_path,
            certificado_senha=company_info['senha'],
            homologacao=(ambiente == AmbienteEmissao.HOMOLOGACAO.value)
        )

    def _assinar_xml(self, xml_arvore, cert_path, senha):
        """Assina o objeto lxml e retorna a árvore assinada"""
        assinador = AssinaturaA1(cert_path, senha)
        return assinador.assinar(xml_arvore)

    def _parse_sefaz_response(self, resposta_comunicacao, xml_enviado=None):
        """
        Extrai os dados da resposta SEFAZ. Se houver erro de schema (225),
        tenta identificar a tag causadora usando o xml_enviado.
        """
        try:
            # 1. Identificação do alvo (Response ou Objeto XML)
            if isinstance(resposta_comunicacao, tuple) and len(resposta_comunicacao) > 1:
                alvo = resposta_comunicacao[1]
            else:
                alvo = resposta_comunicacao

            if hasattr(alvo, 'text'):
                raw_xml_text = alvo.text
            else:
                raw_xml_text = etree.tostring(alvo, encoding='unicode')

            # 2. Parsing do XML de retorno
            xml_limpo = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw_xml_text)
            tree = etree.fromstring(xml_limpo.encode('utf-8'))
            
            def find(tag):
                res = tree.xpath(f"//*[local-name() = '{tag}']")
                return res[0].text if res else ''

            cStat = find('cStat')
            xMotivo = find('xMotivo')

            # 3. Lógica Especial para Erro de Schema (225)
            if cStat == '225' and xml_enviado:
                xMotivo = self._decifrar_erro_schema(xMotivo, xml_enviado)

            return {
                "cStat": cStat,
                "xMotivo": xMotivo,
                "chNFe": find('chNFe'),
                "nProt": find('nProt'),
                "raw_xml": raw_xml_text
            }
        except Exception as e:
            return {
                "cStat": "999",
                "xMotivo": f"Erro no parser da resposta SEFAZ: {str(e)}",
                "chNFe": "", "nProt": "", "raw_xml": ""
            }

    def _decifrar_erro_schema(self, mensagem, xml_enviado):
        """Helper privado para injetar a tag suspeita via triangulação de valor/coluna."""
        try:
            import re
            match_coluna = re.search(r'columnNumber:\s*(\d+)', mensagem)
            if not match_coluna: 
                return mensagem
            
            coluna = int(match_coluna.group(1))
            
            # Serializa com unicode para evitar erro de bytes e manter minificado
            if hasattr(xml_enviado, 'tag'):
                xml_str = etree.tostring(xml_enviado, encoding='unicode')
            elif isinstance(xml_enviado, bytes):
                xml_str = xml_enviado.decode('utf-8')
            else:
                xml_str = str(xml_enviado)
            
            tag_suspeita = None
            valor_tag = "..."

            # ==========================================
            # TÁTICA 1: O erro entregou o valor? (ex: Value '0')
            # ==========================================
            match_valor = re.search(r"Value\s*'([^']+)'", mensagem)
            if match_valor:
                valor_invalido = match_valor.group(1)
                
                # Procura TODAS as tags que tenham exatamente esse valor (ex: <CRT>0</CRT> ou <idDest>0</idDest>)
                padrao_tags = re.compile(rf'<([a-zA-Z0-9_]+)[^>]*>{re.escape(valor_invalido)}</\1>')
                
                menor_distancia = float('inf')
                # Acha a que está geograficamente mais perto da "coluna" reportada
                for m in padrao_tags.finditer(xml_str):
                    distancia = abs(m.start() - coluna)
                    if distancia < menor_distancia:
                        menor_distancia = distancia
                        tag_suspeita = m.group(1)
                        valor_tag = valor_invalido

            # ==========================================
            # TÁTICA 2: O erro entregou o nome do elemento? (ex: element 'xProd')
            # ==========================================
            if not tag_suspeita:
                match_elemento = re.search(r"(?:element|elemento)\s*'([^']+)'", mensagem, re.IGNORECASE)
                if match_elemento:
                    tag_suspeita = match_elemento.group(1).split(':')[-1] # Limpa namespace se houver
                    
                    # Tenta capturar qual era o valor dela no XML
                    m_conteudo = re.search(rf'<{tag_suspeita}[^>]*>(.*?)</{tag_suspeita}>', xml_str)
                    if m_conteudo:
                        valor_tag = m_conteudo.group(1)

            # ==========================================
            # TÁTICA 3: Fallback (Última tag aberta antes da coluna)
            # ==========================================
            if not tag_suspeita:
                # Corta a string um pouquinho depois da coluna para engolir a tag atual
                corte = xml_str[:coluna + 10]
                # Pega apenas tags de abertura
                tags_abertas = re.findall(r'<([a-zA-Z0-9_]+)[^>/]*>', corte)
                
                if tags_abertas:
                    tag_suspeita = tags_abertas[-1]
                    m_conteudo = re.search(rf'<{tag_suspeita}[^>]*>(.*?)</{tag_suspeita}>', xml_str)
                    if m_conteudo:
                        valor_tag = m_conteudo.group(1)

            # Se mesmo assim não achou nada, devolve a mensagem original
            if not tag_suspeita:
                return mensagem

            # ==========================================
            # MONTAGEM DO RETORNO (Com ou Sem Dicionário)
            # ==========================================
            trecho_exibicao = f"<{tag_suspeita}>{valor_tag}</{tag_suspeita}>"
            significado = DICIONARIO_TAGS_NFE.get(tag_suspeita)

            if significado:
                alerta = f"⚠️ Falha de Preenchimento: Verifique o campo '{significado}' ({trecho_exibicao}). O valor informado é inválido ou incompatível."
            else:
                alerta = f"⚠️ Falha de Preenchimento: Verifique a tag ({trecho_exibicao}). O valor informado é inválido ou incompatível."
            
            return f"{alerta}\n\n--- Retorno Original SEFAZ ---\n{mensagem}"

        except Exception:
            # Blindagem: qualquer problema na extração não derruba a aplicação
            return mensagem
    
    def _validar_dados_emissao(self, company_info: dict, payload: dict):
        """Validação Fail Fast antes de iniciar o processamento da nota"""
        erros = []

        # 1. Validações Críticas da Loja (Emitente)
        if not company_info:
            erros.append("Dados da empresa não informados.")
        else:
            if not company_info.get('cnpj'): erros.append("O CNPJ da loja é obrigatório.")
            if not company_info.get('ie'): erros.append("A Inscrição Estadual (IE) da loja é obrigatória.")
            if not company_info.get('cert_base64'): erros.append("O Certificado Digital não foi enviado.")
            if not company_info.get('senha'): erros.append("A senha do Certificado Digital é obrigatória.")

        # 2. Validações da Operação e Destinatário
        modelo = str(payload.get('model', '65'))
        if modelo not in ['55', '65']:
            erros.append("O modelo da nota precisa ser 55 (NF-e) ou 65 (NFC-e).")
        
        if modelo == '55':
            cliente = payload.get('customer') or {}
            if not cliente.get('document'):
                erros.append("Para NF-e (Modelo 55), o CPF ou CNPJ do cliente é obrigatório.")

        # 3. Validações dos Produtos
        itens = payload.get('items', [])
        if not itens:
            erros.append("A nota precisa ter pelo menos um produto.")
        else:
            for idx, item in enumerate(itens):
                nome_prod = str(item.get('description', f'Item {idx+1}')).strip()
                
                # Sefaz rejeita NCM vazio ou menor que 8 dígitos (exceção para serviços em notas conjugadas, mas foge do padrão)
                ncm = str(item.get('ncm', '')).strip()
                if not ncm or len(ncm) < 2:
                    erros.append(f"O produto '{nome_prod}' está sem o NCM ou é inválido.")
                
                try:
                    # Verifica preço
                    if float(item.get('unitPrice', 0)) <= 0:
                        erros.append(f"O produto '{nome_prod}' não pode ter valor zero.")
                    # Verifica quantidade
                    if float(item.get('quantity', 0)) <= 0:
                        erros.append(f"O produto '{nome_prod}' está com quantidade zerada.")
                except ValueError:
                    erros.append(f"O produto '{nome_prod}' possui valores numéricos mal formatados.")

        if erros:
            mensagem_agrupada = "Verifique os seguintes dados antes de emitir:\n- " + "\n- ".join(erros)
            return {
                "status": "error",
                "message": mensagem_agrupada
            }
            
        return None

    def emitir_nota(self, company_info: dict, payload: dict):
        """Fluxo completo de emissão com Auto-Contingência para NFC-e"""
        
        # Validação Antecipada (Fail Fast)
        falha_validacao = self._validar_dados_emissao(company_info, payload)
        if falha_validacao:
            return falha_validacao

        modelo = str(payload.get('model', '65'))
        modelo_label = "nfe" if modelo == ModeloDocumento.NFE.value else "nfce"
        ambiente = int(payload.get('environment', AmbienteEmissao.HOMOLOGACAO.value))
        
        # Função local auxiliar para não repetirmos código na contingência
        def _gerar_e_assinar(payload_atual, cert_path):
            builder = NFeBuilder(company_info, payload_atual)
            xml_string_base, totais = builder.gerar_xml_base()
            xml_injetado = builder.injetar_tags_reforma(xml_string_base)
            xml_arvore = etree.fromstring(xml_injetado.encode('utf-8'))
            
            xml_assinado = self._assinar_xml(xml_arvore, cert_path, company_info['senha'])
            return xml_assinado, totais

        try:
            with self._certificado_temporario(company_info['cert_base64']) as cert_path:
                
                # ==========================================
                # TENTATIVA 1: EMISSÃO NORMAL (ONLINE)
                # ==========================================
                payload['formaEmissao'] = '1'
                payload['isContingency'] = False
                
                xml_assinado, totais_finais = _gerar_e_assinar(payload, cert_path)

                # Gera QR Code (agora para ambos os modelos, online=True)
                csc = company_info.get('cscProducao' if ambiente == 1 else 'cscHomologacao')
                csc_id = company_info.get('idCscProducao' if ambiente == 1 else 'idCscHomologacao')
                gerador_qr = SerializacaoQrcode()
                xml_pronto = gerador_qr.gerar_qrcode(token=csc_id, csc=csc, xml=xml_assinado, online=True)

                comunicacao = self._get_comunicacao(company_info, payload, cert_path)
                
                try:
                    # Tenta transmitir para a SEFAZ
                    resposta = comunicacao.autorizacao(
                        modelo=modelo_label, 
                        nota_fiscal=xml_pronto,
                        contingencia=False,
                        timeout=10 # Timeout curto para o caixa não ficar travado muito tempo
                    )
                
                except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e_rede:
                    # ==========================================
                    # FALHA DE REDE: ENTRANDO EM AUTO-CONTINGÊNCIA
                    # ==========================================
                    if modelo == '65':
                        print("\n[!] Falha de comunicação com a SEFAZ. Ativando contingência offline para NFC-e...")
                        
                        # Altera o payload para forçar a reconstrução do XML
                        payload['formaEmissao'] = '9'
                        payload['isContingency'] = True
                        payload['justificativaContingencia'] = "Falha de comunicacao com a SEFAZ no momento da emissao"
                        
                        # Gera um NOVO xml e assina novamente
                        xml_assinado_cont, _ = _gerar_e_assinar(payload, cert_path)
                        
                        # O QR Code offline é diferente estruturalmente, a lib lida com isso se online=False
                        xml_pronto_cont = gerador_qr.gerar_qrcode(token=csc_id, csc=csc, xml=xml_assinado_cont, online=False)
                        
                        xml_final_bytes = etree.tostring(xml_pronto_cont, encoding='utf-8')
                        ns = {"ns": "http://www.portalfiscal.inf.br/nfe"}
                        chNFe = xml_pronto_cont.xpath("//ns:chNFe", namespaces=ns)[0].text
                        
                        return {
                            "status": "contingency",
                            "accessKey": chNFe,
                            "protocol": "",
                            "xmlBase64": base64.b64encode(xml_final_bytes).decode('utf-8'),
                            "totals": {
                                "totalProducts": float(totais_finais.get('vProd', 0)),
                                "totalDocument": float(totais_finais.get('vNF', 0))
                            },
                            "sefaz": {
                                "cStat": 0,
                                "message": "Emitido em contingência offline devido a falha de rede"
                            }
                        }
                    else:
                        # Se for NF-e (55), não existe contingência offline nativa (apenas SVC, que exige envio). 
                        # Então estouramos o erro para o front.
                        raise Exception(f"Sefaz inacessível e o modelo {modelo} não suporta contingência offline. Erro: {str(e_rede)}")

                # ==========================================
                # PROCESSAMENTO DO RETORNO NORMAL (ONLINE)
                # ==========================================
                if isinstance(resposta, tuple) and resposta[0] == 0:
                    # SUCESSO (Status 100 ou 150)
                    xml_autorizado_arvore = resposta[1]
                    xml_final_bytes = etree.tostring(xml_autorizado_arvore, encoding='utf-8')
                    
                    ns = {"ns": "http://www.portalfiscal.inf.br/nfe"}
                    chNFe = xml_autorizado_arvore.xpath("//ns:chNFe", namespaces=ns)[0].text
                    nProt = xml_autorizado_arvore.xpath("//ns:nProt", namespaces=ns)[0].text
                    
                    return {
                        "status": "authorized",
                        "accessKey": chNFe,
                        "protocol": nProt,
                        "xmlBase64": base64.b64encode(xml_final_bytes).decode('utf-8'),
                        "totals": {
                            "totalProducts": float(totais_finais.get('vProd', 0)),
                            "totalDocument": float(totais_finais.get('vNF', 0))
                        },
                        "sefaz": {
                            "cStat": 100,
                            "message": "Autorizado o uso da NF-e"
                        }
                    }
                else:
                    # REJEIÇÃO SEFAZ
                    response_bruto = resposta[1] if isinstance(resposta, tuple) else resposta
                    retorno_erro = self._parse_sefaz_response(response_bruto, xml_enviado=xml_pronto)
                    cStat_erro = int(retorno_erro.get('cStat') or 0)
                    
                    # ----------------------------------------------------
                    # AQUI ENTRA A INTERCEPTAÇÃO DO ERRO 204
                    # ----------------------------------------------------
                    if cStat_erro == 204:
                        print(f"\n[i] Duplicidade detectada (204). Tentando resgatar XML da Sefaz...")
                        ns = {"ns": "http://www.portalfiscal.inf.br/nfe"}
                        chave_gerada = xml_pronto.xpath("//ns:chNFe", namespaces=ns)[0].text
                        
                        resultado_resgate = self.resgatar_xml_sefaz(
                            comunicacao=comunicacao,
                            chave_nfe=chave_gerada,
                            xml_arvore=xml_pronto,
                            modelo_label=modelo_label,
                            totais=totais_finais
                        )
                        
                        # Se o resgate não der erro de código, devolvemos ele em vez da rejeição
                        if resultado_resgate.get("status") != "error":
                            return resultado_resgate

                    # ----------------------------------------------------
                    # Se não for 204, ou se o resgate falhar, segue o baile de rejeição normal
                    # ----------------------------------------------------
                    return {
                        "status": "rejected",
                        "accessKey": "",
                        "protocol": "",
                        "xmlBase64": "",
                        "totals": {},
                        "sefaz": {
                            "cStat": cStat_erro,
                            "message": retorno_erro.get('xMotivo', 'Erro na comunicação com a Sefaz')
                        }
                    }

        except Exception as e:
            import traceback
            traceback.print_exc()
            raise Exception(f"Falha na emissão: {str(e)}")

    def resgatar_xml_sefaz(self, comunicacao: ComunicacaoSefaz, chave_nfe, xml_arvore, modelo_label, totais):
        """
        Consulta uma nota já existente na SEFAZ e anexa o protocolo ao XML local.
        Ideal para recuperar o XML completo após um erro 204 (Duplicidade).
        """
        try:
            retorno_consulta = comunicacao.consulta_nota(modelo=modelo_label, chave=chave_nfe)
            retorno_dict = self._parse_sefaz_response(retorno_consulta)
            
            cStat = str(retorno_dict.get('cStat', ''))
            
            # 100 = Autorizado, 150 = Autorizado fora do prazo (comum em contingência offline atrasada)
            if cStat in ['100', '150']:
                # A mágica acontece aqui: junta o XML assinado que você tinha com o retorno da Sefaz
                # 1. Converte o texto da resposta da Sefaz em árvore XML
                arvore_consulta = etree.fromstring(retorno_consulta.content)
                ns = {"ns": "http://www.portalfiscal.inf.br/nfe"}
                
                # 2. Caça a tag <protNFe> (Protocolo) dentro da resposta
                protocolo = arvore_consulta.xpath("//ns:protNFe", namespaces=ns)
                
                if protocolo:
                    # 3. Cria o envelope definitivo <nfeProc> exigido por lei
                    nfe_proc = etree.Element("{http://www.portalfiscal.inf.br/nfe}nfeProc", versao="4.00")
                    
                    # 4. Coloca a sua Nota Assinada e o Protocolo dentro do envelope
                    nfe_proc.append(xml_arvore)
                    nfe_proc.append(protocolo[0])
                    
                    # 5. Gera os bytes finais do XML Autorizado
                    xml_autorizado_bytes = etree.tostring(nfe_proc, encoding='utf-8')
                else:
                    raise Exception("A SEFAZ retornou autorizado, mas ocultou a tag protNFe.")
                
                xml_final_base64 = base64.b64encode(xml_autorizado_bytes).decode('utf-8')
                
                return {
                    "status": "authorized",
                    "accessKey": chave_nfe,
                    "protocol": retorno_dict.get('nProt', ''),
                    "xmlBase64": xml_final_base64,
                    "totals": {
                        "totalProducts": float(totais.get('vProd', 0)),
                        "totalDocument": float(totais.get('vNF', 0))
                    },
                    "sefaz": {
                        "cStat": int(cStat),
                        "message": f"Nota recuperada com sucesso (Sefaz: {retorno_dict.get('xMotivo')})"
                    }
                }
            else:
                # A nota existe lá, mas está cancelada, denegada, etc. Retorna o status real dela.
                return {
                    "status": "rejected",
                    "accessKey": chave_nfe,
                    "protocol": "",
                    "xmlBase64": "",
                    "totals": {},
                    "sefaz": {
                        "cStat": int(cStat) if cStat.isdigit() else 0,
                        "message": f"Não foi possível recuperar a nota: {retorno_dict.get('xMotivo')}"
                    }
                }
        except Exception as e:
            return {
                "status": "error",
                "sefaz": {"cStat": 0, "message": f"Falha ao tentar resgatar a nota: {str(e)}"}
            }

    def transmitir_contingencia(self, company_info: dict, payload: dict):
        """
        Transmite um XML que foi gerado offline (contingência) para a SEFAZ.
        """
        try:
            # 1. Decodifica o XML vindo do NestJS
            xml_bytes = base64.b64decode(payload.get('xmlBase64', ''))
            xml_arvore = etree.fromstring(xml_bytes)
            
            # 2. Identifica o modelo (55 ou 65) para selecionar o endpoint
            ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
            mod_tag = xml_arvore.find(".//nfe:mod", namespaces=ns)
            modelo_label = "nfe" if mod_tag is not None and mod_tag.text == '55' else "nfce"

            with self._certificado_temporario(company_info['cert_base64']) as cert_path:
                comunicacao = self._get_comunicacao(company_info, payload, cert_path)
                
                # 3. Transmissão (contingencia=False pois estamos enviando para a URL normal agora)
                # O PyNFe retornará (0, nfeProc) se o status for 100 ou 150
                resposta = comunicacao.autorizacao(
                    modelo=modelo_label, 
                    nota_fiscal=xml_arvore,
                    contingencia=False
                )

                # 4. Processamento do Retorno
                if isinstance(resposta, tuple) and resposta[0] == 0:
                    # SUCESSO (Status 100 ou 150)
                    xml_completo = resposta[1]
                    xml_final_bytes = etree.tostring(xml_completo, encoding='utf-8')
                    
                    n_prot = xml_completo.xpath("//ns:nProt", namespaces={"ns": self.namespace})[0].text
                    
                    return {
                        "status": "authorized",
                        "protocol": n_prot,
                        "xmlBase64": base64.b64encode(xml_final_bytes).decode('utf-8'),
                        "sefaz": {"cStat": 100, "message": "Autorizado o uso da NF-e"}
                    }
                else:
                    # REJEIÇÃO OU DUPLICIDADE (Status 204, etc)
                    response_bruto = resposta[1] if isinstance(resposta, tuple) else resposta
                    retorno_sefaz = self._parse_sefaz_response(response_bruto, xml_enviado=xml_arvore)
                    
                    c_stat = retorno_sefaz.get('cStat')
                    # 204 = Duplicidade (A nota já está na SEFAZ, tratamos como sucesso para o NestJS)
                    is_authorized = c_stat == '204'

                    return {
                        "status": "authorized" if is_authorized else "rejected",
                        "protocol": retorno_sefaz.get('nProt', ''),
                        "xmlBase64": payload.get('xmlBase64'), # Mantém o original se não houver nfeProc
                        "sefaz": {
                            "cStat": int(c_stat or 0),
                            "message": retorno_sefaz.get('xMotivo', 'Erro na transmissão')
                        }
                    }

        except Exception as e:
            raise Exception(f"Erro na retentativa de contingência: {str(e)}")

    def _validar_dados_evento(self, company_info: dict, payload: dict, builder_class):
        """Validação Fail Fast para Eventos (Cancelamento, CCe, Inutilização)"""
        erros = []
        nome_evento = builder_class.__name__

        # 1. Validações da Empresa e Certificado (Comum a todos)
        if not company_info:
            erros.append("Dados da empresa não informados.")
        else:
            if not company_info.get('cnpj'): erros.append("O CNPJ da loja é obrigatório para eventos.")
            if not company_info.get('address', {}).get('state') and not company_info.get('uf'): 
                erros.append("A UF (Estado) da loja é obrigatória.")
            if not company_info.get('cert_base64') or not company_info.get('senha'):
                erros.append("Certificado Digital e senha são obrigatórios.")

        # 2. Regras específicas por tipo de evento
        if nome_evento in ['CancelamentoBuilder', 'CCeBuilder']:
            chave = str(payload.get('chave', '')).strip()
            if len(chave) != 44 or not chave.isdigit():
                erros.append(f"A chave de acesso informada é inválida (deve conter exatamente 44 números). Informado: '{chave}'")

        if nome_evento == 'CancelamentoBuilder':
            justificativa = str(payload.get('justificativa', '')).strip()
            protocolo = str(payload.get('protocolo', '')).strip()
            
            if not protocolo:
                erros.append("O número do protocolo de autorização é obrigatório para cancelar a nota.")
            if len(justificativa) < 15:
                erros.append("A SEFAZ exige que a justificativa de cancelamento tenha no mínimo 15 caracteres.")

        elif nome_evento == 'CCeBuilder':
            correcao = str(payload.get('correcao', '')).strip()
            if len(correcao) < 15:
                erros.append("A SEFAZ exige que o texto da Carta de Correção tenha no mínimo 15 caracteres.")

        elif nome_evento == 'InutilizacaoBuilder':
            justificativa = str(payload.get('justificativa', '')).strip()
            n_inicial = int(payload.get('numeroInicial', 0))
            n_final = int(payload.get('numeroFinal', 0))
            
            if len(justificativa) < 15:
                erros.append("A SEFAZ exige que a justificativa de inutilização tenha no mínimo 15 caracteres.")
            if n_inicial <= 0 or n_final <= 0:
                erros.append("O número inicial e final da inutilização devem ser maiores que zero.")
            if n_final < n_inicial:
                erros.append("O número final da inutilização não pode ser menor que o número inicial.")
            if not str(payload.get('ano', '')).isdigit() or len(str(payload.get('ano', ''))) != 2:
                erros.append("O ano para inutilização deve conter exatamente 2 dígitos (ex: 23, 24).")

        if erros:
            mensagem_agrupada = "Verifique os seguintes dados antes de enviar o evento:\n- " + "\n- ".join(erros)
            return {
                "status": "error",
                "message": mensagem_agrupada
            }
            
        return None
    
    def processar_evento(self, company_info: dict, payload: dict, builder_class):
        """Método genérico para Cancelamento, CCe e Inutilização."""
        
        falha_validacao = self._validar_dados_evento(company_info, payload, builder_class)
        if falha_validacao:
            return falha_validacao

        try:
            builder = builder_class(company_info, payload)
            xml_evento_string = builder.gerar_xml()
            xml_arvore = etree.fromstring(xml_evento_string.encode('utf-8'))

            with self._certificado_temporario(company_info['cert_base64']) as cert_path:
                xml_assinado = self._assinar_xml(xml_arvore, cert_path, company_info['senha'])
                comunicacao = self._get_comunicacao(company_info, payload, cert_path)

                nome_evento = builder_class.__name__
                
                # Determinamos o modelo (nfe ou nfce) para a biblioteca
                # O payload pode vir com 'model' (emissão) ou 'modelo' (inutilização)
                modelo_val = str(payload.get('modelo', payload.get('model', '65')))
                modelo_label = "nfe" if modelo_val == '55' else "nfce"

                if nome_evento == 'InutilizacaoBuilder':
                    # Mantemos a lógica customizada para Inutilização para garantir o retorno do XML assinado
                    url = comunicacao._get_url(modelo=modelo_label, consulta='INUTILIZACAO')
                    xml_soap = comunicacao._construir_xml_soap("NFeInutilizacao4", xml_assinado)
                    resposta_bruta = comunicacao._post(url, xml_soap)
                else:
                    # CHAMADA CORRIGIDA: O método no seu arquivo é 'evento'
                    # Assinatura: def evento(self, modelo, evento, id_lote=1):
                    resposta_bruta = comunicacao.evento(
                        modelo=modelo_label, 
                        evento=xml_assinado
                    )

                retorno = self._parse_sefaz_response(resposta_bruta, xml_enviado=xml_assinado)

                # =========================================================
                # DEEP PARSING: Buscar status real do Evento (ignorar o 128)
                # =========================================================
                if nome_evento != 'InutilizacaoBuilder':
                    try:
                        tree = etree.fromstring(retorno['raw_xml'].encode('utf-8'))
                        
                        # Busca o cStat, nProt e xMotivo de DENTRO do retEvento
                        cStat_evento = tree.xpath("//*[local-name() = 'retEvento']//*[local-name() = 'cStat']")
                        nProt_evento = tree.xpath("//*[local-name() = 'retEvento']//*[local-name() = 'nProt']")
                        xMotivo_evento = tree.xpath("//*[local-name() = 'retEvento']//*[local-name() = 'xMotivo']")

                        if cStat_evento:
                            retorno['cStat'] = cStat_evento[0].text
                        
                        if nProt_evento:
                            retorno['nProt'] = nProt_evento[0].text
                            
                        if xMotivo_evento:
                            retorno['xMotivo'] = xMotivo_evento[0].text
                    except Exception as parse_err:
                        print(f"Erro ao fazer deep parse do evento: {parse_err}")

                # Mantemos o envio do XML assinado original para o seu frontend
                xml_final_bytes = etree.tostring(xml_assinado, encoding='utf-8')
                xml_final_base64 = base64.b64encode(xml_final_bytes).decode('utf-8')

                # Sucesso apenas se o evento foi registrado (135) ou CC-e vinculada (136)
                is_success = retorno['cStat'] in ['135', '136']

                return {
                    "status": "authorized" if is_success else "rejected",
                    "protocol": retorno.get('nProt', ''),
                    "xmlBase64": xml_final_base64,
                    "sefaz": {
                        "cStat": int(retorno['cStat'] or 0),
                        "message": retorno.get('xMotivo', '')
                    }
                }
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise Exception(f"Falha no processamento do evento: {str(e)}")

pynfe_service = PyNFEService()