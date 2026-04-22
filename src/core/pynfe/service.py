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

    def _parse_sefaz_response(self, resposta_comunicacao):
        """
        Extrai os dados da tupla de retorno da ComunicacaoSefaz.
        Lida com o fato de a resposta vir como um objeto de retorno ou tupla.
        """
        try:
            # A ComunicacaoSefaz costuma retornar (Sucesso/Erro, ResponseObject)
            if isinstance(resposta_comunicacao, tuple) and len(resposta_comunicacao) > 1:
                raw_xml_text = resposta_comunicacao[1].text
            else:
                # Fallback se retornar o XML direto
                raw_xml_text = etree.tostring(resposta_comunicacao, encoding='unicode')

            # Limpeza de caracteres ilegais
            xml_limpo = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw_xml_text)
            tree = etree.fromstring(xml_limpo.encode('utf-8'))
            
            def find(tag):
                res = tree.xpath(f"//*[local-name() = '{tag}']")
                return res[0].text if res else ''

            return {
                "cStat": find('cStat'),
                "xMotivo": find('xMotivo'),
                "chNFe": find('chNFe'),
                "nProt": find('nProt'),
                "raw_xml": raw_xml_text
            }
        except Exception as e:
            return {
                "cStat": "999",
                "xMotivo": f"Erro no parser da resposta SEFAZ: {str(e)}",
                "chNFe": "",
                "nProt": "",
                "raw_xml": ""
            }
    
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
                    # REJEIÇÃO SEFAZ (Erro de regra de negócio, NCM inválido, etc)
                    # Note que rejeição NÃO entra em contingência. O usuário deve corrigir o erro.
                    response_bruto = resposta[1] if isinstance(resposta, tuple) else resposta
                    retorno_erro = self._parse_sefaz_response(response_bruto)
                    
                    return {
                        "status": "rejected",
                        "accessKey": "",
                        "protocol": "",
                        "xmlBase64": "",
                        "totals": {},
                        "sefaz": {
                            "cStat": int(retorno_erro.get('cStat') or 0),
                            "message": retorno_erro.get('xMotivo', 'Erro na comunicação com a Sefaz')
                        }
                    }

        except Exception as e:
            import traceback
            traceback.print_exc()
            raise Exception(f"Falha na emissão: {str(e)}")

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
                    retorno_sefaz = self._parse_sefaz_response(response_bruto)
                    
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

                retorno = self._parse_sefaz_response(resposta_bruta)

                xml_final_bytes = etree.tostring(xml_assinado, encoding='utf-8')
                xml_final_base64 = base64.b64encode(xml_final_bytes).decode('utf-8')

                return {
                    "status": "authorized" if retorno['cStat'] in ['102', '128', '135', '136'] else "rejected",
                    "protocol": retorno['nProt'],
                    "xmlBase64": xml_final_base64,
                    "sefaz": {
                        "cStat": int(retorno['cStat'] or 0),
                        "message": retorno['xMotivo']
                    }
                }
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise Exception(f"Falha no processamento do evento: {str(e)}")

pynfe_service = PyNFEService()