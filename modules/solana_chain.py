"""
Integração Solana — Zanini Scraps Marketplace (Hackathon Passo Fundo)
Pagamento via Solana Pay (USDC devnet) com custódia na carteira da plataforma
e repasse automático on-chain ao vendedor.

Fluxo:
  comprador escaneia QR Solana Pay → paga USDC à carteira da plataforma
  (transação carrega uma *reference* única) → backend confirma via RPC →
  escrow → confirmação de entrega → transferência USDC plataforma→vendedor
  (repasse com hash público no explorer).

Ativa somente quando SOLANA_PLATFORM_SECRET estiver configurada.
"""
import os
from decimal import Decimal
from urllib.parse import quote

# Devnet por padrão (hackathon). Mainnet: trocar as duas envs.
SOLANA_RPC = os.environ.get('SOLANA_RPC_URL') or (
    f"https://devnet.helius-rpc.com/?api-key={os.environ['HELIUS_API_KEY']}"
    if os.environ.get('HELIUS_API_KEY') else 'https://api.devnet.solana.com'
)
# USDC (Circle) na devnet
USDC_MINT = os.environ.get('SOLANA_USDC_MINT', '4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU')
USDC_DECIMALS = 6
CLUSTER = os.environ.get('SOLANA_CLUSTER', 'devnet')


def habilitado():
    return bool(os.environ.get('SOLANA_PLATFORM_SECRET'))


def _client():
    from solana.rpc.api import Client
    return Client(SOLANA_RPC)


def _platform_keypair():
    from solders.keypair import Keypair
    secret = os.environ['SOLANA_PLATFORM_SECRET']  # base58 (64 bytes)
    return Keypair.from_base58_string(secret)


def carteira_plataforma():
    return str(_platform_keypair().pubkey())


def gerar_referencia():
    """Pubkey aleatória usada como *reference* do Solana Pay (rastreia o pagamento)."""
    from solders.keypair import Keypair
    return str(Keypair().pubkey())


def link_pagamento(valor_usdc, referencia, label='Zanini Scraps', mensagem=''):
    """Monta a URL Solana Pay (vira QR no front)."""
    amount = f"{Decimal(str(valor_usdc)):.2f}"
    url = (f"solana:{carteira_plataforma()}"
           f"?amount={amount}"
           f"&spl-token={USDC_MINT}"
           f"&reference={referencia}"
           f"&label={quote(label)}")
    if mensagem:
        url += f"&message={quote(mensagem[:60])}"
    return url


def verificar_pagamento(referencia):
    """
    Procura na chain uma transação confirmada que carregue a *reference*.
    Retorna a assinatura (hash) se encontrada e bem-sucedida, senão None.
    """
    from solders.pubkey import Pubkey
    client = _client()
    resp = client.get_signatures_for_address(Pubkey.from_string(referencia), limit=5)
    for info in (resp.value or []):
        if info.err is None:
            return str(info.signature)
    return None


def verificar_assinatura(assinatura: str, valor_usdc) -> bool:
    """
    Confere on-chain se a transação *assinatura* creditou pelo menos *valor_usdc*
    em USDC na carteira da plataforma. Usado quando o comprador envia manualmente
    pela carteira (sem o link Solana Pay) e cola a hash.
    """
    from solders.signature import Signature
    client = _client()
    try:
        sig = Signature.from_string(assinatura.strip())
    except Exception:
        return False
    resp = client.get_transaction(sig, encoding='jsonParsed', max_supported_transaction_version=0)
    if resp.value is None or resp.value.transaction.meta is None or resp.value.transaction.meta.err is not None:
        return False
    meta = resp.value.transaction.meta
    plataforma = str(carteira_plataforma())

    def saldo(lista):
        for b in (lista or []):
            if str(b.mint) == USDC_MINT and b.owner is not None and str(b.owner) == plataforma:
                return Decimal(b.ui_token_amount.ui_amount_string or '0')
        return Decimal('0')

    recebido = saldo(meta.post_token_balances) - saldo(meta.pre_token_balances)
    return recebido >= Decimal(str(valor_usdc)) - Decimal('0.000001')


def _ata(owner_pubkey, mint_pubkey):
    """Associated Token Account do dono para o mint."""
    from spl.token.instructions import get_associated_token_address
    return get_associated_token_address(owner_pubkey, mint_pubkey)


def repasse_usdc(carteira_vendedor: str, valor_usdc) -> dict:
    """
    Transfere USDC da carteira da plataforma para a do vendedor (repasse on-chain).
    Cria a conta de token do vendedor se ainda não existir.
    Retorna {'signature': hash} ou lança exceção.
    """
    from solders.pubkey import Pubkey
    from solders.transaction import Transaction
    from solders.message import Message
    from spl.token.instructions import (
        transfer_checked, TransferCheckedParams,
        create_associated_token_account, get_associated_token_address,
    )
    from spl.token.constants import TOKEN_PROGRAM_ID

    client = _client()
    payer = _platform_keypair()
    mint = Pubkey.from_string(USDC_MINT)
    destino_owner = Pubkey.from_string(carteira_vendedor)

    origem_ata = get_associated_token_address(payer.pubkey(), mint)
    destino_ata = get_associated_token_address(destino_owner, mint)

    instrucoes = []
    conta = client.get_account_info(destino_ata)
    if conta.value is None:  # vendedor ainda não tem conta USDC — plataforma cria
        instrucoes.append(create_associated_token_account(
            payer=payer.pubkey(), owner=destino_owner, mint=mint))

    amount = int(Decimal(str(valor_usdc)) * (10 ** USDC_DECIMALS))
    instrucoes.append(transfer_checked(TransferCheckedParams(
        program_id=TOKEN_PROGRAM_ID,
        source=origem_ata, mint=mint, dest=destino_ata,
        owner=payer.pubkey(), amount=amount, decimals=USDC_DECIMALS,
    )))

    blockhash = client.get_latest_blockhash().value.blockhash
    msg = Message.new_with_blockhash(instrucoes, payer.pubkey(), blockhash)
    tx = Transaction([payer], msg, blockhash)
    resultado = client.send_transaction(tx)
    return {'signature': str(resultado.value)}


def link_explorer(assinatura: str) -> str:
    sufixo = f'?cluster={CLUSTER}' if CLUSTER != 'mainnet-beta' else ''
    return f'https://explorer.solana.com/tx/{assinatura}{sufixo}'
