import datetime
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from typing import List
from zoneinfo import ZoneInfo
from lxml import etree
import re
from enum import Enum

# Imports da biblioteca PyNFe
from pynfe.entidades.emitente import Emitente
from pynfe.entidades.notafiscal import NotaFiscal
from pynfe.entidades.cliente import Cliente
from pynfe.processamento.serializacao import SerializacaoXML
from pynfe.entidades.transportadora import Transportadora


# --- ENUMS E CONSTANTES ---

class AmbienteEmissao(Enum):
    PRODUCAO = 1
    HOMOLOGACAO = 2

class FormaEmissao(Enum):
    NORMAL = '1'
    CONTINGENCIA_OFFLINE_NFCE = '9'

class ModeloDocumento(Enum):
    NFE = '55'
    NFCE = '65'

class TipoOperacao(Enum):
    ENTRADA = '0'
    SAIDA = '1'

class TipoImpressao(Enum):
    RETRATO_NFE = '1'
    BOBINA_NFCE = '4'

class ModalidadeFrete(Enum):
    SEM_FRETE = 9

class FormaPagamentoDefault(Enum):
    DINHEIRO = '01'

class RegimeTributario(Enum):
    SIMPLES_NACIONAL = '1'

def gerar_cnf_por_uuid(document_id: str) -> str:
    """Transforma um UUID em um código numérico de 8 dígitos para a SEFAZ"""
    if not document_id:
        return '12345678' # Fallback de segurança 
        
    # Gera um hash matemático único baseado no UUID
    hash_obj = hashlib.md5(str(document_id).encode('utf-8'))
    
    # Converte o hash para número inteiro e pega os últimos 8 dígitos
    numero_inteiro = int(hash_obj.hexdigest(), 16)
    cnf = str(numero_inteiro)[-8:]
    
    return cnf.zfill(8)


# --- AUXILIARES ---

class FonteMemoria:
    """Mock para enganar o serializador do PyNFe que espera um banco de dados"""
    def __init__(self, nota):
        self.nota = nota
        
    def obter_lista(self, _classe, **kwargs):
        return [self.nota]
        
    def limpar_dados(self):
        pass

def D(value):
    """Auxiliar para garantir precisão decimal de 2 casas"""
    if value is None: return Decimal('0.00')
    return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


# --- BUILDER PRINCIPAL ---

class NFeBuilder:
    def __init__(self, company_info: dict, payload: dict):
        self.company = company_info
        self.payload = payload
        self.namespace = "http://www.portalfiscal.inf.br/nfe"

    def calcular_totais(self) -> dict:
        """Calcula os totais da nota garantindo a precisão decimal"""
        totais = {
            'vProd': D(0), 'vICMS': D(0), 'vPIS': D(0),
            'vCOFINS': D(0), 'vIBS': D(0), 'vCBS': D(0),
            'vNF': D(0)
        }
        
        for item in self.payload.get('items', []):
            qtd = D(item.get('quantity', 0))
            preco = D(item.get('unitPrice', 0))
            v_prod = qtd * preco
            
            totais['vProd'] += v_prod
            totais['vICMS'] += D(item.get('icmsValue', 0))
            totais['vPIS'] += D(item.get('pisValue', 0))
            totais['vCOFINS'] += D(item.get('cofinsValue', 0))
            totais['vIBS'] += D(item.get('ibsValue', 0))
            totais['vCBS'] += D(item.get('cbsValue', 0))
            
        # vNF = vProd - vDesc + vST + vFrete + vSeg + vOutro + vIPI...
        # Por enquanto focado no básico:
        totais['vNF'] = totais['vProd'] + totais['vIBS'] + totais['vCBS']
        
        return totais

    def _montar_dados_basicos(self) -> NotaFiscal:
        endereco_empresa = self.company.get('address') or {}
        uf_empresa = endereco_empresa.get('state', 'PR')
        municipio_empresa = endereco_empresa.get('ibgeCode', '')
        
        agora = datetime.datetime.now(ZoneInfo("America/Sao_Paulo"))
        
        modelo = str(self.payload.get('model', ModeloDocumento.NFCE.value))
        tipo_impressao = TipoImpressao.BOBINA_NFCE.value if modelo == ModeloDocumento.NFCE.value else TipoImpressao.RETRATO_NFE.value
        tipo_operacao = TipoOperacao.SAIDA.value if self.payload.get('tipoSaida', True) else TipoOperacao.ENTRADA.value
        
        cliente_payload = self.payload.get('customer') or {'isFinalConsumer': True}

        forma_emissao = str(self.payload.get('formaEmissao', FormaEmissao.NORMAL.value))

        document_id = self.payload.get('documentId')
        codigo_seguro = gerar_cnf_por_uuid(document_id)

        nfe = NotaFiscal(
            uf=uf_empresa,
            municipio=municipio_empresa,
            natureza_operacao=self.payload.get('naturezaOperacao', 'VENDA'),
            tipo_documento=tipo_operacao,
            forma_emissao=forma_emissao,
            modelo=modelo,
            codigo_numerico_aleatorio=codigo_seguro,
            serie=str(self.payload.get('series', '1')),
            numero_nf=str(self.payload.get('number', '1')),
            data_emissao=agora,
            cliente_final=1 if cliente_payload.get('isFinalConsumer') else 0,
            indicador_presencial=1,
            finalidade_emissao='1',
            tipo_impressao_danfe=tipo_impressao,
        )

        dev_info = self.company.get('developer', {})
        nfe.adicionar_responsavel_tecnico(
            cnpj=dev_info.get('cnpj', ''),
            contato=dev_info.get('contato', ''),
            email=dev_info.get('email', ''),
            fone=dev_info.get('fone', '')
        )
        return nfe

    def _adicionar_emitente(self, nfe: NotaFiscal):
        endereco = self.company.get('address', {})
        uf = endereco.get('state') or self.company.get('uf') or 'PR'
        municipio = endereco.get('city') or 'Curitiba'

        razao_social = str(self.company.get('name', '')).strip()
        nome_fantasia = str(self.company.get('nome_fantasia', '')).strip()
        if not nome_fantasia or len(nome_fantasia) < 2:
            nome_fantasia = razao_social[:30]
        
        nfe.emitente = Emitente(
            razao_social=razao_social,
            nome_fantasia=nome_fantasia,
            cnpj=self.company.get('cnpj'),
            inscricao_estadual=self.company.get('ie'),
            endereco_logradouro=str(endereco.get('street', 'Rua Principal')),
            endereco_numero=str(endereco.get('number', 'S/N')),
            endereco_bairro=str(endereco.get('district', 'Centro')),
            endereco_municipio=str(municipio),
            endereco_uf=str(uf),
            endereco_cep=str(endereco.get('zipcode', '80000000')),
            codigo_de_regime_tributario=str(self.company.get('crt', RegimeTributario.SIMPLES_NACIONAL.value))
        )

    def _adicionar_destinatario(self, nfe: NotaFiscal):
        cliente_payload = self.payload.get('customer') or {}
        
        if not cliente_payload.get('document'):
            # Cliente Dummy para NFC-e anônima
            nfe.cliente = Cliente(
                razao_social='CONSUMIDOR',
                tipo_documento='CPF',
                numero_documento='00000000000',
                indicador_ie='9',
                endereco_logradouro='Rua',
                endereco_numero='0',
                endereco_bairro='Centro',
                endereco_municipio='Curitiba',
                endereco_uf='PR',
                endereco_cep='80000000'
            )
            return

        endereco = cliente_payload.get('address', {})
        documento = cliente_payload['document']
        
        nfe.cliente = Cliente(
            razao_social=cliente_payload.get('name', 'CONSUMIDOR'),
            tipo_documento='CPF' if len(documento) == 11 else 'CNPJ',
            numero_documento=documento,
            indicador_ie=str(cliente_payload.get('indIEDest', 9)),
            inscricao_estadual=cliente_payload.get('ie'),
            endereco_logradouro=str(endereco.get('street', 'Rua')),
            endereco_numero=str(endereco.get('number', '0')),
            endereco_bairro=str(endereco.get('district', 'Bairro')),
            endereco_municipio=str(endereco.get('city', 'Curitiba')),
            endereco_uf=str(endereco.get('state', 'PR')),
            endereco_cep=str(endereco.get('zipcode', '80000000')),
        )

    def _adicionar_itens(self, nfe: NotaFiscal) -> Decimal:
        ambiente = int(self.payload.get('environment', AmbienteEmissao.HOMOLOGACAO.value))
        crt_empresa = str(self.company.get('crt', RegimeTributario.SIMPLES_NACIONAL.value))
        
        fiscal_defs = self.company.get('fiscalDefaults') or {}
        def_csosn = str(fiscal_defs.get('csosn') or '103')
        def_cst = str(fiscal_defs.get('cst') or '000')
        def_pis_cofins = str(fiscal_defs.get('cst_pis_cofins') or '07')
        def_cfop = str(fiscal_defs.get('defaultCfop') or '5102')

        total_nota = D(0)
        totais_tributos_aproximado = D(0)

        for index, item in enumerate(self.payload.get('items', [])):
            qtd = D(item.get('quantity'))
            preco = D(item.get('unitPrice'))
            total_nota += qtd * preco

            descricao = str(item.get('description', 'PRODUTO')).strip()
            if index == 0 and ambiente == AmbienteEmissao.HOMOLOGACAO.value:
                descricao = "NOTA FISCAL EMITIDA EM AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL"

            valor_tributos = (
                D(item.get('icmsValue', 0)) + D(item.get('pisValue', 0)) + 
                D(item.get('cofinsValue', 0)) + D(item.get('ibsValue', 0)) + 
                D(item.get('cbsValue', 0))
            )
            totais_tributos_aproximado += valor_tributos

            cst_enviado = str(item.get('cstIcms') or '').strip()
            kwargs_icms = {}

            if crt_empresa == RegimeTributario.SIMPLES_NACIONAL.value:
                validos_csosn = ['101', '102', '103', '201', '202', '203', '300', '400', '500', '900']
                csosn_resolvido = cst_enviado if cst_enviado in validos_csosn else def_csosn
                
                if csosn_resolvido in ['102', '103', '300', '400']:
                    base_icms, aliq_icms, val_icms = D(0), D(0), D(0)
                else:
                    base_icms = D(item.get('icmsBase', 0))
                    aliq_icms = D(item.get('icmsRate', 0))
                    val_icms = D(item.get('icmsValue', 0))
                    
                kwargs_icms = {
                    'icms_csosn': csosn_resolvido,
                    'icms_modalidade': csosn_resolvido,
                    'icms_origem': str(item.get('origem', 0)),
                    'icms_base_calculo': base_icms,
                    'icms_aliquota': aliq_icms,
                    'icms_valor': val_icms
                }
            else:
                cst_resolvido = cst_enviado if cst_enviado else def_cst
                if len(cst_resolvido) == 3:
                    cst_resolvido = cst_resolvido[-2:]
                
                kwargs_icms = {
                    'icms_modalidade': cst_resolvido.zfill(2),
                    'icms_origem': str(item.get('origem', 0)),
                    'icms_base_calculo': D(item.get('icmsBase', 0)),
                    'icms_aliquota': D(item.get('icmsRate', 0)),
                    'icms_valor': D(item.get('icmsValue', 0))
                }
            
            nfe.adicionar_produto_servico(
                codigo=str(item.get('gtin') or 'SEM GTIN'),
                ean=str(item.get('gtin') or 'SEM GTIN'),
                ean_tributavel=str(item.get('gtin') or 'SEM GTIN'),
                descricao=descricao,
                ncm=str(item.get('ncm', '00000000')),
                cfop=str(item.get('cfop') or def_cfop),
                unidade_comercial=str(item.get('unit', 'UN')),
                quantidade_comercial=qtd,
                valor_unitario_comercial=preco,
                valor_total_bruto=qtd * preco,
                unidade_tributavel=str(item.get('unit', 'UN')),
                quantidade_tributavel=qtd,
                valor_unitario_tributavel=preco,
                ind_total=1,
                valor_tributos_aprox=valor_tributos,
                **kwargs_icms,
                pis_modalidade=str(item.get('cstPis') or def_pis_cofins).zfill(2),
                pis_base_calculo=D(item.get('pisBase', 0)),
                pis_aliquota=D(item.get('pisRate', 0)),
                pis_valor=D(item.get('pisValue', 0)),
                cofins_modalidade=str(item.get('cstCofins') or def_pis_cofins).zfill(2),
                cofins_base_calculo=D(item.get('cofinsBase', 0)),
                cofins_aliquota=D(item.get('cofinsRate', 0)),
                cofins_valor=D(item.get('cofinsValue', 0))
            )
        
        nfe.totais_tributos_aproximado = totais_tributos_aproximado
        return total_nota

    def _adicionar_transporte(self, nfe: NotaFiscal):
        frete_payload = self.payload.get('freight') or {}
        modalidade = frete_payload.get('modFrete', ModalidadeFrete.SEM_FRETE.value)
        
        nfe.transporte_modalidade_frete = modalidade
        
        if modalidade != ModalidadeFrete.SEM_FRETE.value and frete_payload.get('carrier'):
            carrier = frete_payload['carrier']
            endereco = carrier.get('address', {})
            volumes = frete_payload.get('volumes', [])
            
            nfe.transporte_transportadora = Transportadora(
                transporte_razao=carrier.get('name'),
                tipo_documento="CNPJ",
                numero_documento=carrier.get('document', ''),
                inscricao_estadual=carrier.get('ie'),
                endereco_logradouro=endereco.get('street'),
                endereco_uf=endereco.get('state'),
                endereco_municipio=endereco.get('city')
            )
            
            for volume in volumes:
                nfe.adicionar_transporte_volume(
                    quantidade=volume.get('quantity', 1),
                    especie=volume.get("species"),
                    peso_liquido=volume.get("netWeight"),
                    peso_bruto=volume.get("grossWeight"),
                )

    def _adicionar_pagamentos(self, nfe: NotaFiscal, total_nota: Decimal):
        pagamentos = self.payload.get('payments', [])
        valor_troco = total_nota
        
        if not pagamentos:
            nfe.adicionar_pagamento(
                t_pag=FormaPagamentoDefault.DINHEIRO.value,
                v_pag=D('0.00')
            )
        else:
            for pag in pagamentos:
                forma = str(pag.get('method', FormaPagamentoDefault.DINHEIRO.value)).zfill(2) 
                valor = D(str(pag.get('value', 0)))
                valor_troco -= valor
                
                kwargs_pag = {
                    't_pag': forma,
                    'v_pag': valor
                }
                
                if forma in ['03', '04', '17']:
                    kwargs_pag['tp_integra'] = str(pag.get('integration', '2'))
                    
                    if kwargs_pag['tp_integra'] == '1' and pag.get('cnpj'):
                        kwargs_pag['cnpj'] = str(pag.get('cnpj'))
                        kwargs_pag['t_band'] = str(pag.get('brand', '99'))
                        kwargs_pag['c_aut'] = str(pag.get('auth', ''))

                nfe.adicionar_pagamento(**kwargs_pag)
                
        valor_troco = valor_troco * -1
        nfe.valor_troco = valor_troco if valor_troco > 0 else D('0.00')
    
    def gerar_xml_base(self):
        """Monta o objeto NotaFiscal e serializa para XML string"""
        nfe = self._montar_dados_basicos()
        self._adicionar_emitente(nfe)
        self._adicionar_destinatario(nfe)
        total_nota = self._adicionar_itens(nfe)
        self._adicionar_transporte(nfe)
        self._adicionar_pagamentos(nfe, total_nota)

        ambiente = int(self.payload.get('environment', AmbienteEmissao.HOMOLOGACAO.value))

        # Pega a justificativa se estiver em modo contingência
        is_contingencia = self.payload.get('isContingency', False)
        justificativa = self.payload.get('justificativaContingencia')
        
        serializador = SerializacaoXML(
            FonteMemoria(nfe),
            homologacao=(ambiente == AmbienteEmissao.HOMOLOGACAO.value),
            contingencia=justificativa if is_contingencia else None
        )
        lote_root = serializador.exportar() 
        
        ns = {"nfe": self.namespace}
        nfe_node = lote_root.find(".//nfe:NFe", namespaces=ns)
        
        if nfe_node is None:
            nfe_node = lote_root

        xml_bytes = etree.tostring(nfe_node, encoding='utf-8')
        xml_string_segura = xml_bytes.decode('utf-8', errors='replace')
        
        return xml_string_segura, self.calcular_totais()
    
    def injetar_tags_reforma(self, xml_string_base):
        xml_limpo = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', xml_string_base)
        root = etree.fromstring(xml_limpo.encode('utf-8'))
        busca_ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}

        cliente_payload = self.payload.get('customer') or {}
        if not cliente_payload.get('document'):
            dest_node = root.find(".//nfe:dest", busca_ns)
            if dest_node is not None:
                dest_node.getparent().remove(dest_node)

        xml_bytes = etree.tostring(root, encoding='utf-8')
        xml_injetado = xml_bytes.decode('utf-8', errors='replace')
        
        return xml_injetado