"""
Integração Mercado Pago — Zanini Scraps Marketplace
Suporta: PIX (QR Code), Boleto, Transferência PIX automática ao vendedor.
Fluxo: comprador paga → escrow na conta Zanini → confirmação/D+2 → PIX automático ao vendedor.
"""
import os
import hmac
import hashlib
import requests

MP_ACCESS_TOKEN = os.environ.get('MP_ACCESS_TOKEN', '')
MP_BASE_URL = 'https://api.mercadopago.com'

HEADERS = lambda: {
    'Authorization': f'Bearer {MP_ACCESS_TOKEN}',
    'Content-Type': 'application/json',
    'X-Idempotency-Key': '',  # preenchido por função
}


def _headers(idempotency_key=''):
    return {
        'Authorization': f'Bearer {MP_ACCESS_TOKEN}',
        'Content-Type': 'application/json',
        'X-Idempotency-Key': idempotency_key,
    }


def criar_pagamento_pix(tx_uid, valor, descricao, email_comprador, nome_comprador):
    """
    Cria cobrança PIX no Mercado Pago.
    Retorna dict com qr_code, qr_code_base64 e payment_id.
    """
    payload = {
        'transaction_amount': float(valor),
        'description': descricao[:253],
        'payment_method_id': 'pix',
        'payer': {
            'email': email_comprador,
            'first_name': nome_comprador.split()[0],
            'last_name': ' '.join(nome_comprador.split()[1:]) or '-',
        },
        'metadata': {'tx_uid': tx_uid},
        'notification_url': os.environ.get('MP_WEBHOOK_URL', ''),
    }
    resp = requests.post(
        f'{MP_BASE_URL}/v1/payments',
        json=payload,
        headers=_headers(idempotency_key=f'pix-{tx_uid}'),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    pix = data.get('point_of_interaction', {}).get('transaction_data', {})
    return {
        'payment_id': str(data['id']),
        'status': data['status'],
        'qr_code': pix.get('qr_code', ''),
        'qr_code_base64': pix.get('qr_code_base64', ''),
        'expiration': pix.get('ticket_url', ''),
    }


def criar_pagamento_boleto(tx_uid, valor, descricao, email_comprador, nome_comprador, cpf_cnpj):
    """
    Cria boleto bancário no Mercado Pago.
    Retorna dict com transaction_id e boleto_url.
    """
    payload = {
        'transaction_amount': float(valor),
        'description': descricao[:253],
        'payment_method_id': 'bolbradesco',
        'payer': {
            'email': email_comprador,
            'first_name': nome_comprador.split()[0],
            'last_name': ' '.join(nome_comprador.split()[1:]) or '-',
            'identification': {
                'type': 'CNPJ' if len(cpf_cnpj.replace('.','').replace('/','').replace('-','')) == 14 else 'CPF',
                'number': cpf_cnpj.replace('.','').replace('/','').replace('-',''),
            },
        },
        'metadata': {'tx_uid': tx_uid},
        'notification_url': os.environ.get('MP_WEBHOOK_URL', ''),
    }
    resp = requests.post(
        f'{MP_BASE_URL}/v1/payments',
        json=payload,
        headers=_headers(idempotency_key=f'boleto-{tx_uid}'),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        'payment_id': str(data['id']),
        'status': data['status'],
        'boleto_url': data.get('transaction_details', {}).get('external_resource_url', ''),
        'barcode': data.get('barcode', {}).get('content', ''),
    }


def consultar_pagamento(payment_id):
    """Consulta status de um pagamento pelo ID."""
    resp = requests.get(
        f'{MP_BASE_URL}/v1/payments/{payment_id}',
        headers=_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def transferir_pix(tx_uid: str, valor: float, pix_key: str, pix_key_type: str, descricao: str):
    """
    Envia PIX da conta Zanini para a chave PIX do vendedor.
    Usa o endpoint /v1/account/bank_transfers do MP.
    Retorna dict com transfer_id e status, ou lança exceção em falha.
    """
    pix_type_map = {
        'cpf': 'CPF', 'cnpj': 'CNPJ',
        'email': 'EMAIL', 'telefone': 'PHONE',
        'aleatoria': 'RANDOM_KEY',
    }
    payload = {
        'amount': float(round(valor, 2)),
        'currency_id': 'BRL',
        'description': descricao[:100],
        'destination': {
            'pix_key': pix_key,
            'type': pix_type_map.get(pix_key_type, 'EMAIL'),
        },
    }
    resp = requests.post(
        f'{MP_BASE_URL}/v1/account/bank_transfers',
        json=payload,
        headers=_headers(idempotency_key=f'repasse-{tx_uid}'),
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        'transfer_id': str(data.get('id', '')),
        'status': data.get('status', ''),
    }


def verificar_webhook(data_id: str, x_signature: str, x_request_id: str) -> bool:
    """
    Verifica assinatura do webhook MP para evitar fraudes.
    x-signature vem no formato "ts=<timestamp>,v1=<hash>".
    https://www.mercadopago.com.br/developers/pt/docs/your-integrations/notifications/webhooks
    """
    secret = os.environ.get('MP_WEBHOOK_SECRET', '')
    if not secret:
        return True  # sem secret configurado, aceita (handler reconsulta o MP de qualquer forma)
    try:
        parts = dict(p.strip().split('=', 1) for p in x_signature.split(','))
        ts, v1 = parts['ts'], parts['v1']
    except (ValueError, KeyError, AttributeError):
        return False
    manifest = f'id:{str(data_id).lower()};request-id:{x_request_id};ts:{ts};'
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)
