"""
Zanini Scraps — API JSON para o app mobile (Expo / React Native)
Auth: JWT via flask-jwt-extended
Prefixo: /api/
"""
import os
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import (
    create_access_token, jwt_required, get_jwt_identity,
    get_jwt
)
from werkzeug.utils import secure_filename

from modules.models import (
    db, User, Category, Listing, ListingImage,
    Proposal, Transaction, Favorite, Notification
)
from modules import mercadopago as mp

api = Blueprint('api', __name__, url_prefix='/api')

ALLOWED_EXT = {'jpg', 'jpeg', 'png', 'webp'}


def _allowed(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


def _user_dict(u):
    return {
        'id': u.id, 'uid': u.uid, 'name': u.name,
        'email': u.email, 'role': u.role,
        'phone': u.phone, 'city': u.city, 'state': u.state,
        'pix_key': u.pix_key, 'pix_key_type': u.pix_key_type,
        'is_verified': u.is_verified, 'avatar': u.avatar,
    }


def _listing_dict(l, detail=False):
    d = {
        'id': l.id, 'uid': l.uid,
        'title': l.title, 'description': l.description,
        'material_type': l.material_type, 'condition': l.condition,
        'quantity': l.quantity, 'unit': l.unit,
        'price': l.price, 'price_type': l.price_type,
        'city': l.city, 'state': l.state,
        'delivery_type': l.delivery_type,
        'has_invoice': l.has_invoice,
        'availability': l.availability,
        'observations': l.observations,
        'who_picks_up': l.who_picks_up,
        'venda_imediata': l.venda_imediata,
        'status': l.status,
        'is_featured': l.is_featured,
        'views': l.views,
        'favorites': l.favorites,
        'created_at': l.created_at.isoformat(),
        'main_image': l.main_image,
        'category': l.category.name if l.category else None,
        'seller': {
            'uid': l.seller.uid,
            'name': l.seller.name,
            'city': l.seller.city,
            'state': l.seller.state,
            'is_verified': l.seller.is_verified,
        },
        'images': [{'path': img.path, 'is_main': img.is_main}
                   for img in l.images.all()] if detail else [],
    }
    return d


def _proposal_dict(p):
    return {
        'id': p.id, 'uid': p.uid,
        'listing_uid': p.listing.uid,
        'listing_title': p.listing.title,
        'listing_image': p.listing.main_image,
        'amount': p.amount, 'quantity': p.quantity,
        'message': p.message, 'status': p.status,
        'counter_amount': p.counter_amount,
        'counter_message': p.counter_message,
        'buyer': {'uid': p.buyer.uid, 'name': p.buyer.name},
        'seller': {'uid': p.seller_user.uid, 'name': p.seller_user.name},
        'created_at': p.created_at.isoformat(),
    }


def _tx_dict(tx):
    proposal = Proposal.query.get(tx.proposal_id) if tx.proposal_id else None
    listing = None
    if proposal:
        listing = proposal.listing
    elif tx.listing_id:
        listing = Listing.query.get(tx.listing_id)
    return {
        'id': tx.id, 'uid': tx.uid,
        'gross_amount': tx.gross_amount,
        'commission': tx.commission,
        'net_amount': tx.net_amount,
        'payment_status': tx.payment_status,
        'payment_method': tx.payment_method,
        'logistics_status': tx.logistics_status,
        'repasse_status': tx.repasse_status,
        'mp_qr_code': tx.mp_qr_code,
        'mp_qr_base64': tx.mp_qr_base64,
        'mp_boleto_url': tx.mp_boleto_url,
        'mp_barcode': tx.mp_barcode,
        'escrow_release_date': tx.escrow_release_date.isoformat() if tx.escrow_release_date else None,
        'created_at': tx.created_at.isoformat(),
        'listing': {
            'uid': listing.uid, 'title': listing.title,
            'image': listing.main_image,
        } if listing else None,
        'buyer': {'uid': tx.buyer.uid, 'name': tx.buyer.name},
        'seller': {'uid': tx.seller.uid, 'name': tx.seller.name},
    }


# ─── AUTH ────────────────────────────────────────────────

@api.route('/auth/login', methods=['POST'])
def api_login():
    data = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({'error': 'E-mail ou senha incorretos'}), 401
    token = create_access_token(identity=str(user.id))
    return jsonify({'token': token, 'user': _user_dict(user)})


@api.route('/auth/register', methods=['POST'])
def api_register():
    data = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    name  = (data.get('name')  or '').strip()
    password = data.get('password') or ''
    role  = data.get('role', 'comprador')   # comprador | fornecedor

    if not email or not name or not password:
        return jsonify({'error': 'Preencha nome, e-mail e senha'}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'E-mail já cadastrado'}), 409
    if role not in ('comprador', 'fornecedor'):
        role = 'comprador'

    user = User(name=name, email=email, role=role,
                phone=data.get('phone', ''),
                city=data.get('city', ''),
                state=data.get('state', ''))
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    token = create_access_token(identity=str(user.id))
    return jsonify({'token': token, 'user': _user_dict(user)}), 201


@api.route('/auth/me')
@jwt_required()
def api_me():
    user = User.query.get(int(get_jwt_identity()))
    if not user:
        return jsonify({'error': 'Usuário não encontrado'}), 404
    return jsonify({'user': _user_dict(user)})


# ─── CATEGORIAS ──────────────────────────────────────────

@api.route('/categories')
def api_categories():
    cats = Category.query.all()
    return jsonify({'categories': [
        {'id': c.id, 'name': c.name, 'slug': c.slug,
         'count': c.listings.filter_by(status='active').count()}
        for c in cats
    ]})


# ─── LISTINGS ────────────────────────────────────────────

@api.route('/listings')
def api_listings():
    q       = request.args.get('q', '')
    cat_id  = request.args.get('category')
    state   = request.args.get('state')
    sort    = request.args.get('sort', 'recent')
    page    = int(request.args.get('page', 1))
    per_page = 20

    query = Listing.query.filter_by(status='active')
    if q:
        query = query.filter(
            (Listing.title.ilike(f'%{q}%')) |
            (Listing.material_type.ilike(f'%{q}%'))
        )
    if cat_id:
        query = query.filter_by(category_id=cat_id)
    if state:
        query = query.filter_by(state=state)

    if sort == 'price_asc':
        query = query.order_by(Listing.price.asc())
    elif sort == 'price_desc':
        query = query.order_by(Listing.price.desc())
    elif sort == 'popular':
        query = query.order_by(Listing.views.desc())
    else:
        query = query.order_by(Listing.created_at.desc())

    pag = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        'listings': [_listing_dict(l) for l in pag.items],
        'pagination': {
            'page': pag.page, 'pages': pag.pages,
            'total': pag.total, 'has_next': pag.has_next,
        }
    })


@api.route('/listing/<uid>')
def api_listing_detail(uid):
    listing = Listing.query.filter_by(uid=uid).first_or_404()
    listing.views += 1
    db.session.commit()
    return jsonify({'listing': _listing_dict(listing, detail=True)})


@api.route('/listing', methods=['POST'])
@jwt_required()
def api_listing_create():
    user = User.query.get(int(get_jwt_identity()))
    if not user.is_seller:
        return jsonify({'error': 'Somente fornecedores podem publicar lotes'}), 403

    data = request.form if request.content_type and 'multipart' in request.content_type else (request.get_json() or {})

    try:
        listing = Listing(
            seller_id=user.id,
            title=data.get('title', '').strip(),
            description=data.get('description', '').strip(),
            category_id=data.get('category_id') or None,
            material_type=data.get('material_type', '').strip(),
            condition=data.get('condition', 'usado'),
            quantity=float(data.get('quantity', 0)),
            unit=data.get('unit', 'un'),
            price=float(data.get('price', 0)),
            price_type=data.get('price_type', 'total'),
            city=data.get('city', user.city or ''),
            state=data.get('state', user.state or ''),
            delivery_type=data.get('delivery_type', 'retirada'),
            has_invoice=str(data.get('has_invoice', 'false')).lower() in ('true', '1'),
            venda_imediata=str(data.get('venda_imediata', 'false')).lower() in ('true', '1'),
            availability=data.get('availability', ''),
            observations=data.get('observations', ''),
            who_picks_up=data.get('who_picks_up', 'comprador'),
            status='active',
        )
        db.session.add(listing)
        db.session.flush()

        files = request.files.getlist('images') if request.files else []
        for i, f in enumerate(files):
            if f and _allowed(f.filename):
                ext = f.filename.rsplit('.', 1)[1].lower()
                fname = f"{listing.uid}_{i}.{ext}"
                upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'listings')
                os.makedirs(upload_dir, exist_ok=True)
                f.save(os.path.join(upload_dir, fname))
                db.session.add(ListingImage(
                    listing_id=listing.id,
                    path=f'/static/uploads/listings/{fname}',
                    is_main=(i == 0), order=i
                ))

        db.session.commit()
        return jsonify({'listing': _listing_dict(listing, detail=True)}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400


# ─── PROPOSALS ───────────────────────────────────────────

@api.route('/proposal/<listing_uid>', methods=['POST'])
@jwt_required()
def api_proposal_create(listing_uid):
    user = User.query.get(int(get_jwt_identity()))
    listing = Listing.query.filter_by(uid=listing_uid, status='active').first_or_404()
    if listing.seller_id == user.id:
        return jsonify({'error': 'Não pode propor no próprio lote'}), 403

    data = request.get_json() or {}
    try:
        proposal = Proposal(
            listing_id=listing.id,
            buyer_id=user.id,
            seller_id=listing.seller_id,
            amount=float(data.get('amount', listing.price)),
            quantity=float(data.get('quantity', listing.quantity)),
            message=data.get('message', ''),
        )
        db.session.add(proposal)
        db.session.add(Notification(
            user_id=listing.seller_id, type='proposal',
            title='Nova proposta recebida!',
            content=f'{user.name} fez uma proposta de R$ {proposal.amount:,.2f} no lote "{listing.title[:40]}".',
            link='#'
        ))
        db.session.commit()
        return jsonify({'proposal': _proposal_dict(proposal)}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400


@api.route('/proposals')
@jwt_required()
def api_proposals():
    user = User.query.get(int(get_jwt_identity()))
    sent = Proposal.query.filter_by(buyer_id=user.id).order_by(Proposal.created_at.desc()).all()
    received = Proposal.query.filter_by(seller_id=user.id).order_by(Proposal.created_at.desc()).all()
    return jsonify({
        'sent': [_proposal_dict(p) for p in sent],
        'received': [_proposal_dict(p) for p in received],
    })


@api.route('/proposal/<uid>/accept', methods=['POST'])
@jwt_required()
def api_proposal_accept(uid):
    user = User.query.get(int(get_jwt_identity()))
    proposal = Proposal.query.filter_by(uid=uid, seller_id=user.id, status='pending').first_or_404()
    if proposal.listing.status != 'active':
        return jsonify({'error': 'Este lote não está mais disponível.'}), 400
    proposal.status = 'accepted'
    proposal.listing.status = 'reserved'
    Proposal.query.filter(Proposal.listing_id == proposal.listing_id,
                          Proposal.id != proposal.id,
                          Proposal.status == 'pending').update({'status': 'rejected'})
    commission = proposal.amount * current_app.config['COMMISSION_RATE']
    tx = Transaction(
        proposal_id=proposal.id,
        buyer_id=proposal.buyer_id,
        seller_id=proposal.seller_id,
        gross_amount=proposal.amount,
        commission=commission,
        net_amount=proposal.amount - commission,
        payment_status='pending',
        escrow_release_date=datetime.utcnow() + timedelta(days=current_app.config['ESCROW_RELEASE_DAYS'])
    )
    db.session.add(tx)
    db.session.add(Notification(
        user_id=proposal.buyer_id, type='proposal',
        title='Proposta aceita!',
        content=f'Sua proposta foi aceita. Realize o pagamento para confirmar.',
        link='#'
    ))
    db.session.commit()
    return jsonify({'transaction': _tx_dict(tx)})


@api.route('/proposal/<uid>/reject', methods=['POST'])
@jwt_required()
def api_proposal_reject(uid):
    user = User.query.get(int(get_jwt_identity()))
    proposal = Proposal.query.filter_by(uid=uid, seller_id=user.id, status='pending').first_or_404()
    proposal.status = 'rejected'
    db.session.commit()
    return jsonify({'ok': True})


# ─── BUY NOW ─────────────────────────────────────────────

@api.route('/listing/<uid>/buy-now', methods=['POST'])
@jwt_required()
def api_buy_now(uid):
    user = User.query.get(int(get_jwt_identity()))
    listing = Listing.query.filter_by(uid=uid, status='active', venda_imediata=True).first_or_404()
    if listing.seller_id == user.id:
        return jsonify({'error': 'Não pode comprar o próprio lote'}), 403

    commission = listing.price * current_app.config['COMMISSION_RATE']
    tx = Transaction(
        listing_id=listing.id,
        buyer_id=user.id,
        seller_id=listing.seller_id,
        gross_amount=listing.price,
        commission=commission,
        net_amount=listing.price - commission,
        payment_status='pending',
        escrow_release_date=datetime.utcnow() + timedelta(days=current_app.config['ESCROW_RELEASE_DAYS'])
    )
    db.session.add(tx)
    listing.status = 'reserved'
    db.session.commit()
    return jsonify({'transaction': _tx_dict(tx)}), 201


# ─── TRANSACTIONS ─────────────────────────────────────────

@api.route('/transactions')
@jwt_required()
def api_transactions():
    user = User.query.get(int(get_jwt_identity()))
    txs = Transaction.query.filter(
        (Transaction.buyer_id == user.id) | (Transaction.seller_id == user.id)
    ).order_by(Transaction.created_at.desc()).all()
    return jsonify({'transactions': [_tx_dict(tx) for tx in txs]})


@api.route('/transaction/<uid>')
@jwt_required()
def api_transaction_detail(uid):
    user = User.query.get(int(get_jwt_identity()))
    tx = Transaction.query.filter_by(uid=uid).first_or_404()
    if user.id not in (tx.buyer_id, tx.seller_id):
        return jsonify({'error': 'Acesso negado'}), 403
    return jsonify({'transaction': _tx_dict(tx)})


@api.route('/transaction/<uid>/pay', methods=['POST'])
@jwt_required()
def api_transaction_pay(uid):
    user = User.query.get(int(get_jwt_identity()))
    tx = Transaction.query.filter_by(uid=uid, buyer_id=user.id, payment_status='pending').first_or_404()

    proposal = Proposal.query.get(tx.proposal_id) if tx.proposal_id else None
    listing = proposal.listing if proposal else Listing.query.get(tx.listing_id)
    descricao = f'Zanini Scraps — {listing.title[:80]}' if listing else 'Zanini Scraps — Lote'

    mp_enabled = bool(current_app.config.get('MP_ACCESS_TOKEN'))
    if mp_enabled:
        try:
            result = mp.criar_pagamento_pix(
                tx_uid=tx.uid,
                valor=tx.gross_amount,
                descricao=descricao,
                email_comprador=user.email,
                nome_comprador=user.name,
            )
            tx.mp_payment_id  = result['payment_id']
            tx.mp_qr_code     = result['qr_code']
            tx.mp_qr_base64   = result['qr_code_base64']
            tx.payment_method = 'pix'
            tx.payment_status = 'awaiting_payment'
            db.session.commit()
            return jsonify({'transaction': _tx_dict(tx)})
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 500
    else:
        # modo teste
        tx.payment_status = 'escrow'
        tx.payment_method = 'pix'
        tx.mp_qr_code = 'SIMULADO-QR-CODE'
        tx.mp_qr_base64 = ''
        if listing:
            listing.status = 'reserved'
        db.session.commit()
        return jsonify({'transaction': _tx_dict(tx), 'test_mode': True})


@api.route('/transaction/<uid>/confirm-delivery', methods=['POST'])
@jwt_required()
def api_confirm_delivery(uid):
    user = User.query.get(int(get_jwt_identity()))
    tx = Transaction.query.filter_by(uid=uid, buyer_id=user.id, payment_status='escrow').first_or_404()
    tx.payment_status = 'released'
    tx.logistics_status = 'confirmed'
    tx.delivery_date = datetime.utcnow()

    proposal = Proposal.query.get(tx.proposal_id) if tx.proposal_id else None
    _lid = proposal.listing_id if proposal else tx.listing_id
    if _lid:
        listing = Listing.query.get(_lid)
        if listing:
            listing.status = 'sold'

    from app import executar_repasse
    executar_repasse(tx)
    db.session.commit()
    return jsonify({'transaction': _tx_dict(tx), 'repasse_status': tx.repasse_status})


# ─── PERFIL ──────────────────────────────────────────────

@api.route('/profile', methods=['GET', 'PUT'])
@jwt_required()
def api_profile():
    user = User.query.get(int(get_jwt_identity()))
    if request.method == 'GET':
        return jsonify({'user': _user_dict(user)})

    data = request.get_json() or {}
    for field in ('name', 'phone', 'city', 'state', 'pix_key', 'pix_key_type'):
        v = data.get(field)
        if v is not None:
            setattr(user, field, v)
    db.session.commit()
    return jsonify({'user': _user_dict(user)})


# ─── MY LISTINGS ─────────────────────────────────────────

@api.route('/my-listings')
@jwt_required()
def api_my_listings():
    user = User.query.get(int(get_jwt_identity()))
    listings = Listing.query.filter_by(seller_id=user.id).order_by(Listing.created_at.desc()).all()
    return jsonify({'listings': [_listing_dict(l) for l in listings]})


# ─── NOTIFICATIONS ───────────────────────────────────────

@api.route('/notifications')
@jwt_required()
def api_notifications():
    user = User.query.get(int(get_jwt_identity()))
    notifs = Notification.query.filter_by(user_id=user.id).order_by(Notification.created_at.desc()).limit(30).all()
    Notification.query.filter_by(user_id=user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return jsonify({'notifications': [
        {'id': n.id, 'type': n.type, 'title': n.title,
         'content': n.content, 'is_read': n.is_read,
         'created_at': n.created_at.isoformat()}
        for n in notifs
    ]})
