import json
import os
from flask import Blueprint, jsonify, request
from core.acbr_service import acbr_instance

fiscal_bp = Blueprint('fiscal_bp', __name__)
PENDING_FILE = os.path.join(os.getcwd(), 'logs', 'pendencias.json')

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
        # 1. Inicializa a DLL (se ainda não foi)
        res = acbr_instance.inicializar()
        if res != 0:
            return jsonify({"status": "error", "message": "Falha ao inicializar ACBrLib"}), 500
        
        acbr_instance.configurar_estacao()

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
                "ie": co.get('ie', ''),
                "crt": co.get('crt', 1),
                "address": co.get('address', {})
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
        ]
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
    auto_print = payload.get('autoPrint', False)
    # Use um ID único que o frontend enviou, ou o próprio numero+serie da nota
    transacao_id = payload.get('documentId', f"{company_id}-{payload.get('series', '0')}-{payload.get('number', '0')}")
    
    if company_id not in COMPANIES_MEMORY_STORE:
        return jsonify({"status": "error", "message": "Empresa não carregada na memória."}), 400

    company_info = COMPANIES_MEMORY_STORE.get(company_id, {})
    ambiente = payload.get('environment', 2)

    try:
        # 1. Troca o certificado e ambiente na DLL para esta emissão
        acbr_instance.preparar_empresa(company_info, ambiente)
        
        # 2. Converte o JSON do NestJS para INI
        ini_string = acbr_instance.converter_json_para_ini(company_info, payload)

        # 3. Executa a emissão
        resultado = acbr_instance.emitir_nota(ini_string, company_id=company_id, auto_print=auto_print)

        # GRAVA EM DISCO ANTES DE DEVOLVER (O Failsafe)
        salvar_pendencia(transacao_id, resultado)
        
        return jsonify(resultado)

    except Exception as e:
        # Erro grave de execução (não erro da SEFAZ)
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
        acbr_instance.preparar_empresa(company_info, payload.get('environment', 2))
        
        resultado = acbr_instance.carta_correcao(
            access_key=payload.get('accessKey'),
            justification=payload.get('correctionText'),
            cnpj=company_info['cnpj'],
            sequence=payload.get('sequence', 1)
        )
        return jsonify(resultado)
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
        acbr_instance.preparar_empresa(company_info, payload.get('environment', 2))
        
        resultado = acbr_instance.cancelar_documento(
            access_key=payload.get('accessKey'),
            justification=payload.get('justification'),
            cnpj=company_info['cnpj']
        )
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@fiscal_bp.route('/api/fiscal/print', methods=['POST'])
def print_fiscal_document():
    """
    ROTA DE IMPRESSÃO / REIMPRESSÃO (NFe, NFCe, Canc, CC-e)
    
    O ACBr carrega o XML em memória e envia para o spooler do Windows, 
    renderizando o DANFE perfeito.
    
    PAYLOAD ESPERADO DO NESTJS:
    {
        "companyId": "uuid-da-empresa", # Para carregar configs de margem/logo
        "xmlBase64": "...",             # O XML completo autorizado salvo no seu banco
        "documentType": "NFE",          # Opções: "NFE", "NFCE", "CCE", "CANCELAMENTO"
        "printerName": "Nome da Impressora" # (Opcional) Se null, usa a padrão configurada
    }
    
    RETORNO PARA O NESTJS:
    {
        "status": "ok",
        "message": "Enviado para a fila de impressão"
    }
    """
    payload = request.json
    company_id = payload.get('companyId')
    
    if company_id not in COMPANIES_MEMORY_STORE:
         return jsonify({"status": "error", "message": "Empresa não encontrada."}), 400

    try:
        # Prepara configs (logo, margens atreladas à empresa)
        acbr_instance.preparar_empresa(COMPANIES_MEMORY_STORE[company_id], 2)
        
        resultado_impressao = acbr_instance.imprimir_documento(
            xml_string=payload.get('xmlBase64'), # Decodificar base64 aqui se necessário
            doc_type=payload.get('documentType'),
            return_pdf=payload.get('returnPdf', False),
            printer_name=payload.get('printerName')
        )
        
        if payload.get('returnPdf'):
            return jsonify({"status": "ok", "pdfBase64": resultado_impressao})
            
        return jsonify({"status": "ok", "message": "Enviado para impressão."})

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
        acbr_instance.preparar_empresa(COMPANIES_MEMORY_STORE[company_id], 1) # Transmissão é sempre ambiente prod/homol atual
        
        resultado = acbr_instance.transmitir_contingencia(
            xml_base64=payload.get('xmlBase64'),
            company_id=company_id,
            auto_print=payload.get('autoPrint', False)
        )
        return jsonify(resultado)
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