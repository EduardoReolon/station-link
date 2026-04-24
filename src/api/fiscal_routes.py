import base64
import ctypes
import json
import os
import sys
from flask import Blueprint, jsonify, request
from core.pynfe.eventos import CCeBuilder, CancelamentoBuilder, InutilizacaoBuilder
from core.pynfe.service import pynfe_service

fiscal_bp = Blueprint('fiscal_bp', __name__)
PENDING_FILE = os.path.join(os.getcwd(), 'logs', 'pendencias.json')

def salvar_xml_em_disco(company_id: str, access_key: str, xml_base64: str, event_type: str = None, sequence: str = None):
    """
    Salva o XML em base64 no disco físico.
    Destino: storage/xmls/{companyId}/
    """
    if not xml_base64 or not access_key or not company_id:
        return None

    # Cria a estrutura de pastas storage/xmls/{companyId}
    diretorio_destino = os.path.join(os.getcwd(), 'storage', 'xmls', str(company_id))
    os.makedirs(diretorio_destino, exist_ok=True)

    # Define o sufixo exatamente como na sua regra do NestJS
    sufixo = ""
    if event_type == "CANCELED":
        sufixo = "-cancelamento"
    elif event_type == "CCE":
        sufixo = f"-cce-{sequence}" if sequence else "-cce"

    nome_arquivo = f"{access_key}{sufixo}.xml"
    caminho_completo = os.path.join(diretorio_destino, nome_arquivo)

    # Decodifica e salva
    try:
        xml_bytes = base64.b64decode(xml_base64)
        with open(caminho_completo, 'wb') as f:
            f.write(xml_bytes)
        return caminho_completo
    except Exception as e:
        print(f"Erro ao salvar XML no disco: {e}")
        return None

def salvar_pendencia(transacao_id, dados):
    """Grava o resultado em disco antes de responder ao front"""
    pendencias = ler_pendencias()
    pendencias[transacao_id] = dados
    with open(PENDING_FILE, 'w') as f:
        json.dump(pendencias, f)

def ler_pendencias():
    if not os.path.exists(PENDING_FILE):
        return {}
    with open(PENDING_FILE, 'r') as f:
        return json.load(f)

def remover_pendencia(transacao_id):
    pendencias = ler_pendencias()
    if transacao_id in pendencias:
        del pendencias[transacao_id]
        with open(PENDING_FILE, 'w') as f:
            json.dump(pendencias, f)

# Memória volátil para armazenar as credenciais das empresas (não vai para o disco)
# Estrutura esperada: { "company_id_1": { "cnpj": "...", "cert_base64": "...", "senha": "...", "uf": "..." } }
COMPANIES_MEMORY_STORE = {}

@fiscal_bp.route('/api/fiscal/init', methods=['POST'])
def init_station():
    """
    ROTA DE INICIALIZAÇÃO (Chamada no boot do PC ou abertura do PDV)
    
    O Front-end (React) ou o próprio agente faz um POST para cá enviando os dados 
    buscados do NestJS.
    
    PAYLOAD ESPERADO DO BACKEND (NestJS):
    {
        "stationId": "uuid-da-estacao",
        "developer": {
            "cnpj": "12345678000199",
            "idCsrt": "01",
            "csrt": "G8063VRTNDMO886SF..." 
        },
        "companies": [
            {
                "companyId": "uuid-da-empresa-1",
                "document": "12345678000195", 
                "name": "Razao Social da Empresa LTDA", 
                "ie": "123456789", 
                "crt": 3, 
                "address": {
                    "id": "uuid-do-endereco",
                    "label": "Matriz",
                    "zipcode": "80000000",
                    "street": "Rua Exemplo",
                    "number": "123",
                    "district": "Centro",
                    "city": "Curitiba",
                    "state": "PR",
                    "ibgeCode": "4106902"
                },
                "certificateBase64": "...",
                "certificatePassword": "..."
            }
        ]
    }
    
    AÇÃO DO PYTHON:
    1. Recebe essa lista e injeta no dicionário 'COMPANIES_MEMORY_STORE'.
    2. (Opcional) Instancia a DLL do ACBr em background e já testa a validade 
       das senhas/certificados para avisar o Front se algum estiver vencido.
    """
    data = request.json
    try:
        # 2. Guarda as empresas na memória
        for co in data.get('companies', []):
            COMPANIES_MEMORY_STORE[co['companyId']] = {
                # Mapeamento ANTIGO (para não quebrar a sua lógica do certificado)
                "cnpj": co.get('document', ''),
                "cert_base64": co.get('certificateBase64', ''),
                "senha": co.get('certificatePassword', ''),
                "uf": co.get('address', {}).get('state', ''),
                
                # Mapeamento NOVO (para o gerador de INI ler o endereço e o nome)
                "name": co.get('name', 'Razao Social Omitida'),
                "nome_fantasia": co.get('nome_fantasia', 'Fantasia Omitida'),
                "ie": co.get('ie', ''),
                "crt": co.get('crt', 1),

                "cscHomologacao": co.get('cscHomologacao', ''),
                "idCscHomologacao": co.get('idCscHomologacao', ''),
                "cscProducao": co.get('cscProducao', ''),
                "idCscProducao": co.get('idCscProducao', ''),

                "address": co.get('address', {}),
                "developer": data.get('developer', {}),
                "fiscalDefaults": co.get('fiscalDefaults', {})
            }

        return jsonify({
            "status": "ok", 
            "message": f"Estação pronta. {len(COMPANIES_MEMORY_STORE)} empresas carregadas."
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@fiscal_bp.route('/api/fiscal/emit', methods=['POST'])
def emit_document():
    """
    ROTA DE EMISSÃO DE NFe/NFCe (O coração da operação)

    STATUS POSSÍVEIS DE RETORNO:
    - AUTHORIZED: Autorizado normalmente online.
    - REJECTED: Rejeitado por erro de validação da SEFAZ (ex: NCM inválido).
    - CONTINGENCY: Falha de rede/timeout. O agente gerou, assinou e imprimiu offline.
    
    Chamada quando a venda é concluída. O NestJS envia a "receita do bolo" 
    exata e o Python executa através do ACBr.
    
    PAYLOAD ESPERADO DO NESTJS:
    {
        "companyId": "uuid-da-empresa-1", # Usado para resgatar o certificado na memória
        "documentId": "uuid-do-documento-1",

        # DADOS DO CABEÇALHO (DEVEM VIR PRONTOS DO NESTJS)
        "model": "65",          # 55 para NFe, 65 para NFCe
        "series": 1,            # SÉRIE DEVE VIR PRONTA DO BACK
        "number": 1542,         # NÚMERO SEQUENCIAL DEVE VIR PRONTO DO BACK (evita furos)
        "issueDate": "2026-04-13T11:00:00-03:00",
        "environment": 1,         # 1 = Produção, 2 = Homologação (SEFAZ)
        "tipoSaida": True,         # False é para devolução de compra do cliente
        "autoPrint": True,          # Se true, o ACBr já imprime automaticamente após a emissão (NFCe ou NFe em contingência)

        "customer": {
            "document": "12345678909",  // CPF ou CNPJ (apenas números).
            "name": "João da Silva",    // Nome completo ou Razão Social.
            "email": "joao@email.com",  // Opcional. Usado pela SEFAZ ou ACBr para envio automático do XML/PDF.
            
            // Indicador da Inscrição Estadual (IndIEDest)
            // 1 = Contribuinte ICMS (ie obrigatória)
            // 2 = Contribuinte Isento de Inscrição no cadastro de Contribuintes do ICMS
            // 9 = Não Contribuinte (pode ou não possuir IE)
            "indIEDest": 9,              // 1 (Contribuinte), 2 (Isento), 9 (Não Contribuinte)
            "isFinalConsumer": true,           
            "ie": "",                   // Inscrição Estadual (apenas números). Obrigatório se ieIndicator for 1.
            
            // Endereço do destinatário
            "address": {
                "zipcode": "80000000",
                "street": "Rua Exemplo",
                "number": "123",
                "district": "Centro",
                "city": "Curitiba",
                "state": "PR",
                "ibgeCode": "4106902"   // Opcional, mas recomendado para evitar rejeições da SEFAZ
            }
        },
        
        # ITENS E TRIBUTOS (A CARGA TRIBUTÁRIA DEVE VIR CALCULADA DO NESTJS)
        "items": [
            {
                "gtin": "7891000315507",
                "description": "Produto Exemplo",
                "ncm": "22041010",
                "cfop": "5102",
                "origem": "0", // 0 = Nacional, 1 = Estrangeira Importação Direta, 2 = Estrangeira Adquirida no Mercado Interno
                "quantity": 2,
                "unit": "un",
                "unitPrice": 10.00,
                
                // --- IMPOSTOS (Transição) ---
                "cstIcms": "000",
                "icmsBase": 20.00,
                "icmsRate": 18.00,
                "icmsValue": 3.60,
                "icmsStValue": 0.00,

                // PIS / COFINS (OBRIGATÓRIO NO XML, mande 0 se não incidir)
                "cstPis": "01",
                "pisBase": 20.00,
                "pisRate": 1.65,
                "pisValue": 0.33,
                
                "cstCofins": "01",
                "cofinsBase": 20.00,
                "cofinsRate": 7.60,
                "cofinsValue": 1.52,
                
                // --- IMPOSTOS (Reforma Tributária) ---
                "cstIbsCbs": "01",
                "ibsBase": 20.00,
                "ibsRate": 1.20,
                "ibsValue": 0.24,
                "cbsBase": 20.00,
                "cbsRate": 5.80,
                "cbsValue": 1.16,
                "isValue": 0.00
            }
        ],

        "payments": [
            {
                "method": "03", // Código da SEFAZ mapeado (01 Dinheiro, 03 Crédito, 17 Pix...)
                "value": 150.00,
                "cardIndicator": 2 // 1 ou 2 (Apenas quando method for 03 ou 04)
            }
        ],
        
        // --- TRANSPORTE E FRETE (Obrigatório para NFe mod 55) ---
        "freight": {
            // 0=Remetente, 1=Destinatário, 2=Terceiros, 3=Próprio Rte, 4=Próprio Dest, 9=Sem Frete
            "modFrete": 0, 

            // Dados da Transportadora (Opcional se modFrete for 9)
            "carrier": {
                "document": "12345678000195", // CNPJ ou CPF (só números)
                "name": "Expresso Logistica LTDA",
                "ie": "123456789",
                "address": {
                    "zipcode": "80000000",
                    "street": "Rua Exemplo",
                    "number": "123",
                    "district": "Centro",
                    "city": "Curitiba",
                    "state": "PR",
                    "ibgeCode": "4106902"   // Opcional, mas recomendado para evitar rejeições da SEFAZ
                }
            },

            // Volumes transportados
            "volumes": [
                {
                    "quantity": 10,
                    "species": "CAIXA", // Ex: CAIXA, PALETE, TAMBOR
                    "netWeight": 50.500,  // Peso Líquido (kg)
                    "grossWeight": 52.000 // Peso Bruto (kg)
                }
            ]
        },
    }
    
    AÇÕES DO PYTHON / ACBr:
    1. Seleciona o certificado correto via 'companyId'.
    2. Monta o arquivo INI de entrada do ACBr com os dados exatos do JSON.
    3. ATENÇÃO: Configura o ACBr para Auto-Calcular Totais. O ACBr vai somar 
       os valores dos itens (ex: 3.60 de ICMS) e preencher automaticamente as 
       tags de totais da nota (<vBC>, <vICMS>, <vProd>, <vNF>).
    4. ACBr gera a Chave de Acesso (44 dígitos), assina o XML e transmite online (tpEmis = 1).
    5. SE FALHAR (Timeout ou erro de rede):
       - Muda para Contingência Offline (tpEmis = 9).
       - Regera a Chave de Acesso, assina o XML.
       - Imprime o Cupom automaticamente.
       - Retorna o status CONTINGENCY para o NestJS salvar.
    
    RETORNO PARA O NESTJS (Salvar no banco):
    {
        "status": "AUTHORIZED", # ou REJECTED
        "accessKey": "412604... (Gerada e calculada pela DLL)",
        "protocol": "141... (Retornado pela SEFAZ)",
        "receiptNumber": "...",
        "xmlBase64": "...", # O XML autorizado para você arquivar no seu S3/Banco
        
        # TOTAIS RECALCULADOS (Retornados para você sobrescrever no backend)
        "totals": {
            "totalProducts": 20.00,
            "totalDocument": 20.00,
            "totalIcms": 3.60
        },
        
        "sefaz": {
            "cStat": 100, // ou 204 no caso de retentativa bem-sucedida
            "message": "..." // ou "Rejeicao: Duplicidade de NF-e"
        }
    }

    RETORNO PARA O NESTJS (Caso Contingência Automática):
    {
        "status": "contingency",
        "accessKey": "...", # Nova chave gerada para contingência
        "protocol": null,   # Não tem protocolo ainda
        "xmlBase64": "...", # XML assinado offline (Guarde isso a sete chaves no banco!)
        "totals": { ... }
        "sefaz": {
            "cStat": 100, // ou 204 no caso de retentativa bem-sucedida
            "message": "..." // ou "Rejeicao: Duplicidade de NF-e"
        }
    }
    """
    payload = request.json
    company_id = payload.get('companyId')
    transacao_id = payload.get('documentId', f"{company_id}-{payload.get('series')}-{payload.get('number')}")
    
    if company_id not in COMPANIES_MEMORY_STORE:
        return jsonify({"status": "error", "message": "Empresa não carregada na memória."}), 400

    company_info = COMPANIES_MEMORY_STORE.get(company_id)

    try:
        # A lógica passa inteira para o nosso service do PyNFe
        resultado = pynfe_service.emitir_nota(company_info, payload)
        
        # Salva o fail-safe em disco igual você fazia
        salvar_pendencia(transacao_id, resultado)

        # Quando a nota for autorizada, basta chamar passando os dados
        salvar_xml_em_disco(company_id, resultado['accessKey'], resultado['xmlBase64'])
        
        return jsonify(resultado)

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

"""
outros casos de retorno sefaz
"sefaz": {
    "cStat": 778,
    "message": "Rejeicao: Informado NCM inexistente"
}
"sefaz": {
    "cStat": 0, // Zero ou null, pois não houve resposta da SEFAZ
    "message": "Emitido em contingência offline por falha de rede"
}
"""

@fiscal_bp.route('/api/fiscal/cce', methods=['POST'])
def correction_letter():
    """
    ROTA DE CARTA DE CORREÇÃO (CC-e)
    
    A CC-e é um evento atrelado a uma NFe autorizada.
    
    PAYLOAD ESPERADO DO NESTJS:
    {
        "companyId": "uuid-da-empresa",
        "accessKey": "412604...",   # Chave da NFe original
        "sequence": 1,              # nSeqEvento: Número sequencial da correção (1, 2, 3...)
        "correctionText": "O correto e rua X e nao rua Y..." # Mínimo 15, Máximo 1000 caracteres
    }
    
    RETORNO PARA O NESTJS:
    {
        "status": "AUTHORIZED",
        "protocol": "141...",
        "xmlBase64": "...", # O XML do evento de correção para você salvar
        "sefaz": {
            "cStat": 100, // ou 204 no caso de retentativa bem-sucedida
            "message": "..." // ou "Rejeicao: Duplicidade de NF-e"
        }
    }
    """
    payload = request.json
    company_id = payload.get('companyId')
    
    if company_id not in COMPANIES_MEMORY_STORE:
        return jsonify({"status": "error", "message": "Empresa não carregada."}), 400

    try:
        company_info = COMPANIES_MEMORY_STORE[company_id]
        
        # Traduz o payload do NestJS para o que o CCeBuilder espera
        payload_evento = {
            "environment": payload.get('environment', 2),
            "chave": payload.get('accessKey'),
            "sequencia": payload.get('sequence', 1),
            "correcao": payload.get('correctionText')
        }
        
        resultado = pynfe_service.processar_evento(company_info, payload_evento, CCeBuilder)
        
        # Se a validação Fail Fast falhar, ela já devolve o erro formatado
        if resultado.get('status') == 'error':
            return jsonify(resultado), 400

        # Formata a saída exatamente como o NestJS espera
        is_authorized = resultado.get('status') == 'authorized'
        retorno_nest = {
            "status": "authorized" if is_authorized else "rejected",
            "protocol": resultado.get('protocol', ''),
            "xmlBase64": resultado.get('xmlBase64', ''),
            "sefaz": resultado.get('sefaz', {})
        }

        if is_authorized:
            salvar_xml_em_disco(
                company_id=company_id, 
                access_key=payload.get('accessKey'), 
                xml_base64=retorno_nest['xmlBase64'], 
                event_type="CANCELED"
            )
        
        return jsonify(retorno_nest)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@fiscal_bp.route('/api/fiscal/cancel', methods=['POST'])
def cancel_document():
    """
    ROTA DE CANCELAMENTO
    
    PAYLOAD ESPERADO DO NESTJS:
    {
        "companyId": "uuid",
        "accessKey": "412604...", # Necessário para o ACBr localizar e cancelar
        "protocol": "141...",     # Necessário referenciar o protocolo de autorização
        "justification": "Erro de digitacao dos itens" # Mínimo 15 caracteres
    }
    
    RETORNO PARA O NESTJS:
    {
        "status": "CANCELED",
        "cancelProtocol": "141...", # Novo protocolo gerado pelo cancelamento
        "cancelXmlBase64": "...",
        "sefaz": {
            "cStat": 100, // ou 204 no caso de retentativa bem-sucedida
            "message": "..." // ou "Rejeicao: Duplicidade de NF-e"
        }
    }
    """
    payload = request.json
    company_id = payload.get('companyId')
    
    if company_id not in COMPANIES_MEMORY_STORE:
        return jsonify({"status": "error", "message": "Empresa não carregada."}), 400

    try:
        company_info = COMPANIES_MEMORY_STORE[company_id]
        
        # Traduz o payload do NestJS para o que o CancelamentoBuilder espera
        payload_evento = {
            "environment": payload.get('environment', 2),
            "chave": payload.get('accessKey'),
            "protocolo": payload.get('protocol'),
            "justificativa": payload.get('justification')
        }
        
        resultado = pynfe_service.processar_evento(company_info, payload_evento, CancelamentoBuilder)
        
        if resultado.get('status') == 'error':
            return jsonify(resultado), 400

        # Formata a saída com os nomes de variáveis específicos do Cancelamento (conforme sua docstring)
        is_canceled = resultado.get('status') == 'authorized'
        retorno_nest = {
            "status": "canceled" if is_canceled else "rejected",
            "cancelProtocol": resultado.get('protocol', ''),
            "cancelXmlBase64": resultado.get('xmlBase64', ''),
            "sefaz": resultado.get('sefaz', {})
        }

        if is_canceled:
            salvar_xml_em_disco(
                company_id=company_id, 
                access_key=payload.get('accessKey'), 
                xml_base64=retorno_nest['cancelXmlBase64'], 
                event_type="CANCELED"
            )
        
        return jsonify(retorno_nest)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@fiscal_bp.route('/api/fiscal/inutilization', methods=['POST'])
def inutilization_document():
    """
    ROTA DE INUTILIZAÇÃO DE NUMERAÇÃO
    
    PAYLOAD ESPERADO DO NESTJS:
    {
        "companyId": "uuid",
        "environment": 2,
        "year": "23",           # 2 últimos dígitos do ano
        "model": "65",          # 55 ou 65
        "series": "1",
        "initialNumber": 100,
        "finalNumber": 105,
        "justification": "Quebra de sequencia por queda de energia" # Mínimo 15 caracteres
    }
    
    RETORNO PARA O NESTJS:
    {
        "status": "INUTILIZED", // ou REJECTED
        "protocol": "141...",
        "xmlBase64": "...",
        "sefaz": {
            "cStat": 102,
            "message": "Inutilizacao de numero homologada"
        }
    }
    """
    payload = request.json
    company_id = payload.get('companyId')
    
    if company_id not in COMPANIES_MEMORY_STORE:
        return jsonify({"status": "error", "message": "Empresa não carregada."}), 400

    try:
        company_info = COMPANIES_MEMORY_STORE[company_id]
        
        # Traduz o payload do NestJS para o InutilizacaoBuilder
        payload_evento = {
            "environment": payload.get('environment', 2),
            "ano": payload.get('year'),
            "modelo": payload.get('model'),
            "serie": payload.get('series'),
            "numeroInicial": payload.get('initialNumber'),
            "numeroFinal": payload.get('finalNumber'),
            "justificativa": payload.get('justification')
        }
        
        resultado = pynfe_service.processar_evento(company_info, payload_evento, InutilizacaoBuilder)
        
        if resultado.get('status') == 'error':
            return jsonify(resultado), 400

        is_inutilized = resultado.get('status') == 'authorized'
        retorno_nest = {
            "status": "inutilized" if is_inutilized else "rejected",
            "protocol": resultado.get('protocol', ''),
            "xmlBase64": resultado.get('xmlBase64', ''),
            "sefaz": resultado.get('sefaz', {})
        }
        
        return jsonify(retorno_nest)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@fiscal_bp.route('/api/fiscal/transmit', methods=['POST'])
def transmit_contingency():
    """
    ROTA DE RETENTATIVA (Sincronização de Contingência)
    
    Chamada por um CronJob do NestJS ou botão manual no Front-end para 
    notas que estão com status CONTINGENCY no banco.
    
    PAYLOAD ESPERADO DO NESTJS:
    {
        "companyId": "uuid-da-empresa",
        "xmlBase64": "...", # O exato XML gerado offline na rota /emit
        "autoPrint" = True, # Se true, o ACBr já imprime automaticamente após a transmissão
    }
    
    AÇÕES DO PYTHON:
    1. Carrega o certificado da Company.
    2. Carrega o XML em memória no ACBr.
    3. Aciona o método de Envio (sem regerar chaves).
    
    RETORNO PARA O NESTJS:
    {
        "status": "AUTHORIZED", # Retorna isso mesmo se for erro 204 (Duplicidade)
        "protocol": "141...",   # Agora sim, a SEFAZ devolveu o protocolo
        "xmlBase64": "...",     # O novo XML atualizado com a tag de autorização
        "sefaz": {
            "cStat": 100, // ou 204 no caso de retentativa bem-sucedida
            "message": "..." // ou "Rejeicao: Duplicidade de NF-e"
        }
    }
    # NOTA: Se a SEFAZ rejeitar (ex: cStat 539 - Duplicidade com diferença na Chave),
    # o status retorna "REJECTED" e você tratará isso no seu painel.
    """
    payload = request.json
    company_id = payload.get('companyId')
    
    if company_id not in COMPANIES_MEMORY_STORE:
        return jsonify({"status": "error", "message": "Empresa não carregada."}), 400

    try:
        company_info = COMPANIES_MEMORY_STORE[company_id]
        
        # Aciona o serviço para transmitir o XML offline já assinado
        resultado = pynfe_service.transmitir_contingencia(company_info, payload)
        
        # Caso a validação de dados falhe antes do envio
        if resultado.get('status') == 'error':
            return jsonify(resultado), 400

        is_authorized = resultado.get('status') == 'authorized'
        
        # Retorno formatado conforme o contrato do NestJS
        return jsonify({
            "status": "authorized" if is_authorized else "rejected",
            "protocol": resultado.get('protocol', ''),
            "xmlBase64": resultado.get('xmlBase64', ''),
            "sefaz": resultado.get('sefaz', {})
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@fiscal_bp.route('/api/fiscal/pending', methods=['GET'])
def get_pending():
    """
    Rota chamada pelo Front-end logo que abre/reinicia.
    Devolve tudo que o backend processou, mas que o front não confirmou.
    """
    return jsonify(ler_pendencias())

@fiscal_bp.route('/api/fiscal/pending/ack', methods=['POST'])
def ack_pending():
    """
    O Front-end chama essa rota avisando: 
    'Recebi essa transação e já salvei no meu banco local/NestJS. Pode apagar.'
    """
    document_id = request.json.get('documentId')
    remover_pendencia(document_id)
    return jsonify({"status": "ok"})
