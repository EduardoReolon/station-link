from lxml import etree
from pynfe.entidades.evento import EventoCancelarNota, EventoCartaCorrecao
from pynfe.utils.flags import CODIGOS_ESTADOS, NAMESPACE_NFE, VERSAO_PADRAO
from pynfe.processamento.serializacao import SerializacaoXML

from core.pynfe.builders import AmbienteEmissao, FonteMemoria, ModeloDocumento

class EventoBuilderBase:
    """Classe base que concentra a infraestrutura comum a todos os eventos"""
    def __init__(self, company_info: dict, payload: dict):
        self.company = company_info
        self.payload = payload
        
        self.cnpj = self.company.get('cnpj')
        self.uf = self.company.get('address', {}).get('state') or self.company.get('uf') or 'PR'
        self.ambiente = int(self.payload.get('environment', AmbienteEmissao.HOMOLOGACAO.value))
        self.homologacao = (self.ambiente == AmbienteEmissao.HOMOLOGACAO.value)

    def _serializar_e_limpar(self, entidade, namespace_tag: str):
        """Padroniza a geração do XML (Mock -> Serializador -> Limpeza LXML)"""
        serializador = SerializacaoXML(FonteMemoria(entidade), homologacao=self.homologacao)
        lote_root = serializador.exportar()
        
        ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
        no_principal = lote_root.find(f".//nfe:{namespace_tag}", namespaces=ns)
        
        if no_principal is None:
            no_principal = lote_root

        xml_bytes = etree.tostring(no_principal, encoding='utf-8')
        return xml_bytes.decode('utf-8', errors='replace')


class CancelamentoBuilder(EventoBuilderBase):
    def gerar_xml(self) -> str:
        # Usando o nome correto da classe (EventoCancelarNota)
        evento = EventoCancelarNota(
            cnpj=self.cnpj,
            uf=self.uf,
            chave=self.payload.get('chave'),
            protocolo=self.payload.get('protocolo'),
            justificativa=self.payload.get('justificativa', '').strip()
        )
        return self._serializar_e_limpar(evento, "envEvento")


class CCeBuilder(EventoBuilderBase):
    def gerar_xml(self) -> str:
        evento = EventoCartaCorrecao(
            cnpj=self.cnpj,
            uf=self.uf,
            chave=self.payload.get('chave'),
            n_seq_evento=int(self.payload.get('sequencia', 1)), # Nome correto do atributo na biblioteca
            correcao=self.payload.get('correcao', '').strip()
        )
        return self._serializar_e_limpar(evento, "envEvento")


class InutilizacaoBuilder(EventoBuilderBase):
    def gerar_xml(self) -> str:
        """Monta o XML de inutilização exatamente como a SEFAZ exige"""
        ano = str(self.payload.get('ano', ''))[-2:]
        uf_cod = CODIGOS_ESTADOS[self.uf.upper()]
        
        # Limpa o CNPJ para deixar só números
        cnpj = "".join(filter(str.isdigit, self.cnpj))
        
        modelo = str(self.payload.get('modelo', ModeloDocumento.NFCE.value))
        serie_str = str(self.payload.get('serie', '1'))
        num_ini = self.payload.get('numeroInicial', 0)
        num_fin = self.payload.get('numeroFinal', 0)

        # Regra rigorosa de formação do ID da SEFAZ
        id_unico = f"ID{uf_cod}{ano}{cnpj}{modelo}{serie_str.zfill(3)}{str(num_ini).zfill(9)}{str(num_fin).zfill(9)}"

        raiz = etree.Element('inutNFe', versao=VERSAO_PADRAO, xmlns=NAMESPACE_NFE)
        inf_inut = etree.SubElement(raiz, 'infInut', Id=id_unico)
        etree.SubElement(inf_inut, 'tpAmb').text = str(self.ambiente)
        etree.SubElement(inf_inut, 'xServ').text = 'INUTILIZAR'
        etree.SubElement(inf_inut, 'cUF').text = str(uf_cod)
        etree.SubElement(inf_inut, 'ano').text = ano
        etree.SubElement(inf_inut, 'CNPJ').text = cnpj
        etree.SubElement(inf_inut, 'mod').text = modelo
        etree.SubElement(inf_inut, 'serie').text = serie_str
        etree.SubElement(inf_inut, 'nNFIni').text = str(num_ini)
        etree.SubElement(inf_inut, 'nNFFin').text = str(num_fin)
        etree.SubElement(inf_inut, 'xJust').text = str(self.payload.get('justificativa', '')).strip()

        xml_bytes = etree.tostring(raiz, encoding='utf-8')
        return xml_bytes.decode('utf-8')