"""
Estoque do lote: reserva, liberação e conclusão de vendas, inclusive vendas parciais.

Venda parcial (o comprador leva parte do lote): a quantidade sai do anúncio, que continua
ativo com o saldo. Se o preço do anúncio é do lote inteiro, ele é ajustado na proporção.
Venda do lote inteiro: o anúncio fica reservado e, ao concluir, vendido (comportamento original).
"""
from modules.models import db, Listing

_EPS = 1e-9


def reservar(listing_id, qtd=None):
    """Reserva o lote de forma atômica. Retorna (ok, quantidade_reservada, parcial).
    qtd None ou >= saldo do anúncio reserva o lote inteiro.
    Grave o resultado na transação com marcar(tx, qtd, parcial)."""
    listing = Listing.query.get(listing_id)
    if not listing or listing.status != 'active':
        return False, None, False
    if qtd and qtd > (listing.quantity or 0) + _EPS:   # pediu mais do que restou no lote
        return False, None, False
    if qtd and 0 < qtd < (listing.quantity or 0) - _EPS:
        valores = {'quantity': Listing.quantity - qtd}
        if listing.price_type != 'por_unidade':
            valores['price'] = Listing.price * (Listing.quantity - qtd) / Listing.quantity
        ok = Listing.query.filter(Listing.id == listing_id, Listing.status == 'active',
                                  Listing.quantity > qtd).update(valores, synchronize_session=False)
        if ok:
            db.session.expire(listing)
        return bool(ok), qtd, True
    ok = Listing.query.filter_by(id=listing_id, status='active').update({'status': 'reserved'})
    return bool(ok), listing.quantity, False


def marcar(tx, qtd, parcial):
    tx.reserved_qty, tx.reserved_partial = qtd, parcial


def liberar(tx):
    """Negociação cancelada: devolve ao anúncio o que estava separado."""
    listing = tx.listing_ref
    if not listing:
        return
    if tx.reserved_partial and tx.reserved_qty:
        q = tx.reserved_qty
        if listing.price_type != 'por_unidade' and listing.quantity:
            listing.price = round(listing.price * (listing.quantity + q) / listing.quantity, 2)
        listing.quantity = (listing.quantity or 0) + q
        if listing.status == 'sold':
            listing.status = 'active'
    elif listing.status == 'reserved':
        listing.status = 'active'


def garantir_reserva(tx):
    """Pagamento confirmado: mantém o lote fora do ar só se a venda foi do lote inteiro."""
    listing = tx.listing_ref
    if listing and not tx.reserved_partial and listing.status == 'active':
        listing.status = 'reserved'


def concluir(tx):
    """Entrega confirmada: lote inteiro vira vendido; venda parcial não mexe no saldo restante."""
    listing = tx.listing_ref
    if listing and not tx.reserved_partial:
        listing.status = 'sold'
