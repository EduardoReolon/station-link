from flask import Blueprint, jsonify, request

fiscal_bp = Blueprint('fiscal_bp', __name__)

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
        "companies": [
            {
                "companyId": "uuid-da-empresa-1",
                "document": "CNPJ",
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
                "certificatePassword": "...",
                "fiscalEnvironment": 1
            }
        ]
    }
    
    AÇÃO DO PYTHON:
    1. Recebe essa lista e injeta no dicionário 'COMPANIES_MEMORY_STORE'.
    2. (Opcional) Instancia a DLL do ACBr em background e já testa a validade 
       das senhas/certificados para avisar o Front se algum estiver vencido.
    """
    data = request.json
    # TODO: Lógica de armazenamento em COMPANIES_MEMORY_STORE
    
    return jsonify({"status": "ok", "message": "Estação armada com certificados em memória."})


@fiscal_bp.route('/api/fiscal/emit', methods=['POST'])
def emit_document():
    """
    ROTA DE EMISSÃO DE NFe/NFCe (O coração da operação)
    
    Chamada quando a venda é concluída. O NestJS envia a "receita do bolo" 
    exata e o Python executa através do ACBr.
    
    PAYLOAD ESPERADO DO NESTJS:
    {
        "companyId": "uuid-da-empresa-1", # Usado para resgatar o certificado na memória
        
        # DADOS DO CABEÇALHO (DEVEM VIR PRONTOS DO NESTJS)
        "model": "65",          # 55 para NFe, 65 para NFCe
        "series": 1,            # SÉRIE DEVE VIR PRONTA DO BACK
        "number": 1542,         # NÚMERO SEQUENCIAL DEVE VIR PRONTO DO BACK (evita furos)
        "issueDate": "2026-04-13T11:00:00-03:00",
        
        "customer": {
            "document": "12345678909",  // CPF ou CNPJ (apenas números).
            "name": "João da Silva",    // Nome completo ou Razão Social.
            "email": "joao@email.com",  // Opcional. Usado pela SEFAZ ou ACBr para envio automático do XML/PDF.
            
            // Indicador da Inscrição Estadual (IndIEDest)
            // 1 = Contribuinte ICMS (ie obrigatória)
            // 2 = Contribuinte Isento de Inscrição no cadastro de Contribuintes do ICMS
            // 9 = Não Contribuinte (pode ou não possuir IE)
            "ieIndicator": 9,           
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
                "quantity": 2,
                "unitPrice": 10.00,
                # IMPOSTOS PRÉ-CALCULADOS PELO NESTJS
                "icmsBase": 20.00,
                "icmsRate": 18.00,
                "icmsValue": 3.60,
                "cstIcms": "000"
            }
        ]
    }
    
    AÇÕES DO PYTHON / ACBr:
    1. Seleciona o certificado correto via 'companyId'.
    2. Monta o arquivo INI de entrada do ACBr com os dados exatos do JSON.
    3. ATENÇÃO: Configura o ACBr para Auto-Calcular Totais. O ACBr vai somar 
       os valores dos itens (ex: 3.60 de ICMS) e preencher automaticamente as 
       tags de totais da nota (<vBC>, <vICMS>, <vProd>, <vNF>).
    4. ACBr gera a Chave de Acesso (44 dígitos), assina o XML e transmite.
    
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
        
        "rejectionReason": null # Preenchido se status == REJECTED
    }
    """
    data = request.json
    # TODO: Lógica de injeção no ACBr, envio e parse do retorno
    
    return jsonify({"status": "pending_implementation"})

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
        "xmlBase64": "..." # O XML do evento de correção para você salvar
    }
    """
    data = request.json
    # TODO: Lógica de evento CC-e no ACBr
    
    return jsonify({"status": "pending_implementation"})

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
        "cancelXmlBase64": "..."
    }
    """
    data = request.json
    # TODO: Lógica de evento de cancelamento no ACBr
    
    return jsonify({"status": "pending_implementation"})

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
    data = request.json
    # TODO: Lógica de carregamento do XML e chamada do método Imprimir do ACBr
    
    return jsonify({"status": "pending_implementation"})