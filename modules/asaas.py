"""
Integração Asaas — Zanini Scraps Marketplace
Cobrança (PIX + cartão via checkout hospedado) e repasse automático (transferência PIX).
Ativa somente quando ASAAS_API_KEY estiver configurada; caso contrário o sistema
continua usando o Mercado Pago.
"""
import os
from datetime import datetime, timedelta

import requests

ASAAS_BASE_URL = os.environ.get('ASAAS_BASE_URL', 'https://api.asaas.com/v3')


def _key():
    return os.environ.get('ASAAS_API_KEY', '')


def habilitado():
    return bool(_key())


def _headers():
    return {
        'access_token': _key(),
        'Content-Type': 'application/json',
        'User-Agent': 'ZaniniScraps/1.0',
    }


def _post(path, payload):
    resp = requests.post(f'{ASAAS_BASE_URL}{path}', json=payload, headers=_headers(), timeout=20)
    if resp.status_code >= 400:
        try:
            detalhe = resp.json().get('errors', [{}])[0].get('description', resp.text[:200])
        except Exception:
            detalhe = resp.text[:200]
        raise RuntimeError(f'Asaas {resp.status_code}: {detalhe}')
    return resp.json()


def _get(path, params=None):
    resp = requests.get(f'{ASAAS_BASE_URL}{path}', params=params or {}, headers=_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json()


def obter_ou_criar_cliente(nome, cpf_cnpj, email):
    """Retorna o id do cliente Asaas, criando se necessário."""
    doc = ''.join(c for c in (cpf_cnpj or '') if c.isdigit())
    if not doc:
        raise RuntimeError('CPF/CNPJ obrigatório para pagar via Asaas. Complete seu cadastro no perfil.')
    existentes = _get('/customers', {'cpfCnpj': doc})
    if existentes.get('data'):
        return existentes['data'][0]['id']
    novo = _post('/customers', {'name': nome[:120], 'cpfCnpj': doc, 'email': email})
    return novo['id']


def criar_cobranca(tx_uid, valor, descricao, cliente_id):
    """
    Cria cobrança com checkout hospedado (o pagador escolhe PIX ou cartão) e
    também o QR PIX direto. Retorna dict com payment_id, invoice_url, qr_code, qr_base64.
    """
    pagamento = _post('/payments', {
        'customer': cliente_id,
        'billingType': 'UNDEFINED',            # invoiceUrl aceita PIX, cartão e boleto
        'value': float(round(valor, 2)),
        'dueDate': (datetime.utcnow() + timedelta(days=3)).strftime('%Y-%m-%d'),
        'description': descricao[:500],
        'externalReference': tx_uid,
    })
    qr_code, qr_base64 = '', ''
    try:
        qr = _get(f"/payments/{pagamento['id']}/pixQrCode")
        qr_code = qr.get('payload', '')
        qr_base64 = qr.get('encodedImage', '')
    except Exception:
        pass  # QR indisponível não impede o invoiceUrl
    return {
        'payment_id': pagamento['id'],
        'invoice_url': pagamento.get('invoiceUrl', ''),
        'qr_code': qr_code,
        'qr_base64': qr_base64,
        'status': pagamento.get('status', ''),
    }


def consultar_cobranca(payment_id):
    return _get(f'/payments/{payment_id}')


_PIX_TYPE = {'cpf': 'CPF', 'cnpj': 'CNPJ', 'email': 'EMAIL',
             'telefone': 'PHONE', 'aleatoria': 'EVP'}


def transferir_pix(valor, pix_key, pix_key_type, descricao):
    """Transferência PIX da conta Asaas para a chave do vendedor (repasse)."""
    resp = _post('/transfers', {
        'value': float(round(valor, 2)),
        'pixAddressKey': pix_key,
        'pixAddressKeyType': _PIX_TYPE.get((pix_key_type or 'email').lower(), 'EMAIL'),
        'description': descricao[:100],
        'operationType': 'PIX',
    })
    return {'transfer_id': str(resp.get('id', '')), 'status': resp.get('status', '')}


def verificar_webhook(request_headers):
    """Valida o token do webhook configurado no painel Asaas."""
    esperado = os.environ.get('ASAAS_WEBHOOK_TOKEN', '')
    if not esperado:
        return False  # fail-closed: sem token configurado, não aceita webhooks
    import hmac
    recebido = request_headers.get('asaas-access-token', '')
    return hmac.compare_digest(recebido, esperado)
