"""
Zanini Scraps - Sistema Nacional de Comercialização de Sobras
Desenvolvido por Grupo Zanini S.A.
"""
import os
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, send_from_directory, abort)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from flask_mail import Mail, Message as MailMessage
from werkzeug.utils import secure_filename

from config import Config
from modules.models import (db, User, Category, Listing, ListingImage,
                            Proposal, Message, Transaction, Favorite, Notification)
from modules import mercadopago as mp

app = Flask(__name__)
app.config.from_object(Config)

mail = Mail(app)

# ── Filtro Jinja: formato BRL (R$ 1.234,56) ──
def _brl(value):
    try:
        s = f"{float(value):,.2f}"          # "1,234.56"
        int_part, dec_part = s.split('.')
        return f"R$ {int_part.replace(',', '.')},{dec_part}"
    except Exception:
        return f"R$ {value}"

app.jinja_env.filters['brl'] = _brl

# ── Helper para strings Python (flash/notificações) ──
def brl(value):
    return _brl(value)

# ── Corrige encoding Windows (double-UTF-8) e força charset ──
import re as _re
def _fix_mojibake(text):
    """Reverte double-UTF-8: 'Ã§' → 'ç', 'Ã£' → 'ã', etc."""
    try:
        return text.encode('latin-1').decode('utf-8')
    except Exception:
        return text

@app.after_request
def fix_encoding(response):
    if not response.content_type.startswith('text/html'):
        return response
    try:
        raw = response.get_data(as_text=False)
        text = raw.decode('utf-8', errors='replace')
        # Detecta mojibake típico: Ã seguido de char latin1 especial
        if _re.search(r'Ã[§£³©¡ºÀ-ÿ]', text):
            text = _fix_mojibake(text)
            response.set_data(text.encode('utf-8'))
    except Exception:
        pass
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    return response

def send_email(to, subject, body_html):
    """Envia e-mail silenciosamente — não quebra o fluxo se falhar."""
    if not app.config.get('MAIL_ENABLED'):
        return
    try:
        msg = MailMessage(
            subject=f'[Zanini Scraps] {subject}',
            recipients=[to],
            html=body_html
        )
        mail.send(msg)
    except Exception as e:
        app.logger.warning(f'Falha ao enviar e-mail para {to}: {e}')

# Ensure dirs
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'listings'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'avatars'), exist_ok=True)
os.makedirs(os.path.join(os.path.dirname(__file__), 'data'), exist_ok=True)

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Faça login para acessar esta página.'
login_manager.login_message_category = 'warning'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


def executar_repasse(tx):
    """
    Tenta enviar PIX ao vendedor via MP.
    Atualiza tx.repasse_status: 'enviado', 'falhou' ou 'manual'.
    Não faz commit — chame db.session.commit() após.
    """
    seller = User.query.get(tx.seller_id)
    if not seller or not seller.pix_key:
        tx.repasse_status = 'manual'
        tx.repasse_obs = 'Vendedor sem chave PIX cadastrada. Transferir manualmente.'
        return

    mp_enabled = bool(app.config.get('MP_ACCESS_TOKEN'))
    if not mp_enabled:
        tx.repasse_status = 'manual'
        tx.repasse_obs = 'MP sem credenciais — ambiente de teste.'
        return

    try:
        result = mp.transferir_pix(
            tx_uid=tx.uid,
            valor=tx.net_amount,
            pix_key=seller.pix_key,
            pix_key_type=seller.pix_key_type or 'email',
            descricao=f'Zanini Scraps — repasse tx {tx.uid[:8]}',
        )
        tx.repasse_status = 'enviado'
        tx.repasse_transfer_id = result.get('transfer_id', '')
        tx.repasse_at = datetime.utcnow()
        tx.repasse_obs = f"PIX enviado. ID: {result.get('transfer_id')} Status: {result.get('status')}"
        app.logger.info(f'Repasse tx {tx.uid}: PIX enviado para {seller.pix_key}')
    except Exception as e:
        tx.repasse_status = 'falhou'
        tx.repasse_obs = f'Erro MP: {str(e)[:250]}'
        app.logger.error(f'Repasse tx {tx.uid} falhou: {e}')


# ─── Context Processors ───
@app.context_processor
def inject_globals():
    unread_msgs = 0
    unread_notifs = 0
    if current_user.is_authenticated:
        unread_msgs = Message.query.filter_by(receiver_id=current_user.id, is_read=False).count()
        unread_notifs = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return dict(
        platform_name='Zanini Scraps',
        current_year=datetime.utcnow().year,
        unread_messages=unread_msgs,
        unread_notifications=unread_notifs,
        categories=Category.query.order_by(Category.name).all() if Category.query.first() else []
    )


# ─── AUTH ───
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            next_page = request.args.get('next')
            flash(f'Bem-vindo, {user.display_name}!', 'success')
            return redirect(next_page or url_for('dashboard'))
        flash('E-mail ou senha incorretos.', 'danger')
    return render_template('auth/login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        role = 'usuario'
        document_type = request.form.get('document_type', 'cnpj')
        document = request.form.get('document', '').strip()
        phone = request.form.get('phone', '').strip()
        city = request.form.get('city', '').strip()
        state = request.form.get('state', '').strip()

        if User.query.filter_by(email=email).first():
            flash('E-mail já cadastrado.', 'danger')
            return render_template('auth/register.html')

        if document and User.query.filter_by(document=document).first():
            flash('CNPJ/CPF já cadastrado.', 'danger')
            return render_template('auth/register.html')

        user = User(
            name=name, email=email, role=role,
            document_type=document_type, document=document,
            phone=phone, city=city, state=state
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user, remember=True)
        send_email(user.email, 'Bem-vindo ao Zanini Scraps!',
            f'<p>Olá, <strong>{user.name}</strong>!</p>'
            f'<p>Sua conta foi criada com sucesso. Agora você pode comprar e vender sobras industriais.</p>'
            f'<p><a href="{url_for("marketplace", _external=True)}">Acessar o marketplace</a></p>')
        flash('Cadastro realizado com sucesso!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('auth/register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Você saiu do sistema.', 'info')
    return redirect(url_for('home'))


# ─── PAGES ───
@app.route('/')
def home():
    featured = Listing.query.filter_by(status='active', is_featured=True).order_by(Listing.created_at.desc()).limit(6).all()
    recent = Listing.query.filter_by(status='active').order_by(Listing.created_at.desc()).limit(12).all()
    stats = {
        'total_listings': Listing.query.filter_by(status='active').count(),
        'total_users': User.query.count(),
        'total_transactions': Transaction.query.count(),
        'total_gmv': db.session.query(db.func.sum(Transaction.gross_amount)).scalar() or 0
    }
    return render_template('home.html', featured=featured, recent=recent, stats=stats)


@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))

    my_listings = Listing.query.filter_by(seller_id=current_user.id).order_by(Listing.created_at.desc()).limit(10).all()
    my_proposals_sent = Proposal.query.filter_by(buyer_id=current_user.id).order_by(Proposal.created_at.desc()).limit(10).all()
    my_proposals_received = Proposal.query.filter_by(seller_id=current_user.id).order_by(Proposal.created_at.desc()).limit(10).all()
    my_transactions = Transaction.query.filter(
        (Transaction.buyer_id == current_user.id) | (Transaction.seller_id == current_user.id)
    ).order_by(Transaction.created_at.desc()).limit(10).all()

    stats = {
        'active_listings': Listing.query.filter_by(seller_id=current_user.id, status='active').count(),
        'pending_proposals': Proposal.query.filter_by(seller_id=current_user.id, status='pending').count(),
        'total_sold': db.session.query(db.func.sum(Transaction.gross_amount)).filter_by(seller_id=current_user.id, payment_status='released').scalar() or 0,
        'total_bought': db.session.query(db.func.sum(Transaction.gross_amount)).filter_by(buyer_id=current_user.id, payment_status='released').scalar() or 0,
    }

    return render_template('dashboard/index.html',
                           listings=my_listings,
                           proposals_sent=my_proposals_sent,
                           proposals_received=my_proposals_received,
                           transactions=my_transactions,
                           stats=stats)


# ─── LISTINGS ───
@app.route('/marketplace')
def marketplace():
    page = request.args.get('page', 1, type=int)
    q = request.args.get('q', '')
    cat = request.args.get('category', '')
    material = request.args.get('material', '')
    state = request.args.get('state', '')
    price_min = request.args.get('price_min', type=float)
    price_max = request.args.get('price_max', type=float)
    sort = request.args.get('sort', 'recent')

    query = Listing.query.filter_by(status='active')

    if q:
        query = query.filter(
            (Listing.title.ilike(f'%{q}%')) |
            (Listing.description.ilike(f'%{q}%')) |
            (Listing.material_type.ilike(f'%{q}%'))
        )
    if cat:
        query = query.filter_by(category_id=cat)
    if material:
        query = query.filter(Listing.material_type.ilike(f'%{material}%'))
    if state:
        query = query.filter_by(state=state)
    if price_min:
        query = query.filter(Listing.price >= price_min)
    if price_max:
        query = query.filter(Listing.price <= price_max)

    if sort == 'price_asc':
        query = query.order_by(Listing.price.asc())
    elif sort == 'price_desc':
        query = query.order_by(Listing.price.desc())
    elif sort == 'popular':
        query = query.order_by(Listing.views.desc())
    else:
        query = query.order_by(Listing.created_at.desc())

    pagination = query.paginate(page=page, per_page=app.config['ITEMS_PER_PAGE'], error_out=False)

    materials = db.session.query(Listing.material_type).filter(
        Listing.status == 'active', Listing.material_type.isnot(None)
    ).distinct().all()
    materials = sorted(set(m[0] for m in materials if m[0]))

    states_list = db.session.query(Listing.state).filter(
        Listing.status == 'active', Listing.state.isnot(None)
    ).distinct().all()
    states_list = sorted(set(s[0] for s in states_list if s[0]))

    return render_template('listings/marketplace.html',
                           listings=pagination.items,
                           pagination=pagination,
                           materials=materials,
                           states_list=states_list)


@app.route('/listing/<uid>')
def listing_detail(uid):
    listing = Listing.query.filter_by(uid=uid).first_or_404()
    listing.views += 1
    db.session.commit()

    is_favorited = False
    if current_user.is_authenticated:
        is_favorited = Favorite.query.filter_by(
            user_id=current_user.id, listing_id=listing.id
        ).first() is not None

    similar = Listing.query.filter(
        Listing.status == 'active',
        Listing.id != listing.id,
        Listing.category_id == listing.category_id
    ).limit(4).all()

    return render_template('listings/detail.html',
                           listing=listing,
                           is_favorited=is_favorited,
                           similar=similar)


@app.route('/listing/new', methods=['GET', 'POST'])
@login_required
def listing_create():
    if request.method == 'POST':
        listing = Listing(
            seller_id=current_user.id,
            title=request.form.get('title', '').strip(),
            description=request.form.get('description', '').strip(),
            category_id=request.form.get('category_id') or None,
            material_type=request.form.get('material_type', '').strip(),
            condition=request.form.get('condition', 'usado'),
            quantity=float(request.form.get('quantity', 0)),
            unit=request.form.get('unit', 'kg'),
            price=float(request.form.get('price', 0)),
            price_type=request.form.get('price_type', 'total'),
            min_order=float(request.form.get('min_order', 0) or 0),
            city=request.form.get('city', current_user.city or ''),
            state=request.form.get('state', current_user.state or ''),
            cep=request.form.get('cep', ''),
            delivery_type=request.form.get('delivery_type', 'retirada'),
            origin_declaration=request.form.get('origin_declaration', ''),
            has_invoice=bool(request.form.get('has_invoice')),
            availability=request.form.get('availability', ''),
            observations=request.form.get('observations', ''),
            who_picks_up=request.form.get('who_picks_up', 'comprador'),
            venda_imediata=bool(request.form.get('venda_imediata')),
            status=request.form.get('status', 'active'),
        )
        db.session.add(listing)
        db.session.flush()

        # Upload de imagens
        files = request.files.getlist('images')
        for i, f in enumerate(files):
            if f and allowed_file(f.filename):
                ext = f.filename.rsplit('.', 1)[1].lower()
                fname = f"{listing.uid}_{i}.{ext}"
                fpath = os.path.join(app.config['UPLOAD_FOLDER'], 'listings', fname)
                f.save(fpath)
                img = ListingImage(
                    listing_id=listing.id,
                    path=f'/static/uploads/listings/{fname}',
                    is_main=(i == 0),
                    order=i
                )
                db.session.add(img)

        db.session.commit()
        flash('Anúncio publicado com sucesso!', 'success')
        return redirect(url_for('listing_detail', uid=listing.uid))

    return render_template('listings/create.html')


@app.route('/listing/<uid>/edit', methods=['GET', 'POST'])
@login_required
def listing_edit(uid):
    listing = Listing.query.filter_by(uid=uid, seller_id=current_user.id).first_or_404()
    if request.method == 'POST':
        listing.title = request.form.get('title', listing.title)
        listing.description = request.form.get('description', listing.description)
        listing.category_id = request.form.get('category_id') or listing.category_id
        listing.material_type = request.form.get('material_type', listing.material_type)
        listing.condition = request.form.get('condition', listing.condition)
        listing.quantity = float(request.form.get('quantity', listing.quantity))
        listing.unit = request.form.get('unit', listing.unit)
        listing.price = float(request.form.get('price', listing.price))
        listing.price_type = request.form.get('price_type', listing.price_type)
        listing.city = request.form.get('city', listing.city)
        listing.state = request.form.get('state', listing.state)
        listing.delivery_type = request.form.get('delivery_type', listing.delivery_type)
        listing.origin_declaration = request.form.get('origin_declaration', listing.origin_declaration)
        listing.has_invoice = bool(request.form.get('has_invoice'))
        listing.availability = request.form.get('availability', listing.availability)
        listing.observations = request.form.get('observations', listing.observations)
        listing.who_picks_up = request.form.get('who_picks_up', listing.who_picks_up)
        listing.venda_imediata = bool(request.form.get('venda_imediata'))
        listing.status = request.form.get('status', listing.status)

        # Novas imagens
        files = request.files.getlist('images')
        for i, f in enumerate(files):
            if f and f.filename and allowed_file(f.filename):
                ext = f.filename.rsplit('.', 1)[1].lower()
                fname = f"{listing.uid}_{datetime.utcnow().timestamp()}_{i}.{ext}"
                fpath = os.path.join(app.config['UPLOAD_FOLDER'], 'listings', fname)
                f.save(fpath)
                img = ListingImage(
                    listing_id=listing.id,
                    path=f'/static/uploads/listings/{fname}',
                    is_main=False,
                    order=listing.images.count() + i
                )
                db.session.add(img)

        db.session.commit()
        flash('Anúncio atualizado!', 'success')
        return redirect(url_for('listing_detail', uid=listing.uid))

    return render_template('listings/edit.html', listing=listing)


@app.route('/listing/<uid>/delete', methods=['POST'])
@login_required
def listing_delete(uid):
    listing = Listing.query.filter_by(uid=uid, seller_id=current_user.id).first_or_404()
    db.session.delete(listing)
    db.session.commit()
    flash('Anúncio removido.', 'info')
    return redirect(url_for('dashboard'))


# ─── FAVORITES ───
@app.route('/favorite/<int:listing_id>', methods=['POST'])
@login_required
def toggle_favorite(listing_id):
    fav = Favorite.query.filter_by(user_id=current_user.id, listing_id=listing_id).first()
    listing = Listing.query.get_or_404(listing_id)
    if fav:
        db.session.delete(fav)
        listing.favorites = max(0, listing.favorites - 1)
        status = 'removed'
    else:
        db.session.add(Favorite(user_id=current_user.id, listing_id=listing_id))
        listing.favorites += 1
        status = 'added'
    db.session.commit()
    return jsonify({'status': status, 'favorites': listing.favorites})


# ─── PROPOSALS ───
@app.route('/proposal/new/<uid>', methods=['POST'])
@login_required
def proposal_create(uid):
    listing = Listing.query.filter_by(uid=uid, status='active').first_or_404()
    if listing.seller_id == current_user.id:
        flash('Você não pode fazer proposta no próprio anúncio.', 'warning')
        return redirect(url_for('listing_detail', uid=uid))

    amount = float(request.form.get('amount', 0))
    quantity = float(request.form.get('quantity', listing.quantity) or listing.quantity)
    message = request.form.get('message', '').strip()

    proposal = Proposal(
        listing_id=listing.id,
        buyer_id=current_user.id,
        seller_id=listing.seller_id,
        amount=amount,
        quantity=quantity,
        message=message
    )
    db.session.add(proposal)
    db.session.flush()  # garante que proposal.uid seja gerado antes de usar

    notif = Notification(
        user_id=listing.seller_id,
        type='proposal',
        title='Nova proposta recebida',
        content=f'{current_user.name} fez uma proposta de { brl(amount) } em "{listing.title}"',
        link=url_for('proposal_detail', uid=proposal.uid)
    )
    db.session.add(notif)
    db.session.commit()

    seller = listing.seller_user
    send_email(seller.email, 'Nova proposta recebida',
        f'<p>Olá, <strong>{seller.name}</strong>!</p>'
        f'<p><strong>{current_user.name}</strong> fez uma proposta de <strong>{ brl(amount) }</strong> '
        f'em "<strong>{listing.title}</strong>".</p>'
        f'<p><a href="{url_for("proposal_detail", uid=proposal.uid, _external=True)}">Ver proposta</a></p>')

    flash('Proposta enviada com sucesso!', 'success')
    return redirect(url_for('proposal_detail', uid=proposal.uid))


@app.route('/proposal/<uid>')
@login_required
def proposal_detail(uid):
    proposal = Proposal.query.filter_by(uid=uid).first_or_404()
    if current_user.id not in (proposal.buyer_id, proposal.seller_id) and not current_user.is_admin:
        abort(403)
    messages = Message.query.filter_by(proposal_id=proposal.id).order_by(Message.created_at.asc()).all()
    # Mark messages as read
    Message.query.filter_by(proposal_id=proposal.id, receiver_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return render_template('proposals/detail.html', proposal=proposal, messages=messages)


@app.route('/proposal/<uid>/accept', methods=['POST'])
@login_required
def proposal_accept(uid):
    proposal = Proposal.query.filter_by(uid=uid, seller_id=current_user.id, status='pending').first_or_404()
    proposal.status = 'accepted'

    commission = proposal.amount * app.config['COMMISSION_RATE']
    tx = Transaction(
        proposal_id=proposal.id,
        buyer_id=proposal.buyer_id,
        seller_id=proposal.seller_id,
        gross_amount=proposal.amount,
        commission=commission,
        net_amount=proposal.amount - commission,
        payment_status='pending',
        escrow_release_date=datetime.utcnow() + timedelta(days=app.config['ESCROW_RELEASE_DAYS'])
    )
    db.session.add(tx)

    notif = Notification(
        user_id=proposal.buyer_id,
        type='proposal',
        title='Proposta aceita!',
        content=f'Sua proposta de { brl(proposal.amount) } foi aceita.',
        link=url_for('proposal_detail', uid=uid)
    )
    db.session.add(notif)
    db.session.commit()

    buyer = proposal.buyer
    send_email(buyer.email, 'Sua proposta foi aceita!',
        f'<p>Olá, <strong>{buyer.name}</strong>!</p>'
        f'<p>Sua proposta de <strong>{ brl(proposal.amount) }</strong> foi aceita.</p>'
        f'<p>Acesse para realizar o pagamento e concluir a compra.</p>'
        f'<p><a href="{url_for("proposal_detail", uid=uid, _external=True)}">Ver proposta</a></p>')

    flash('Proposta aceita! Transação criada.', 'success')
    return redirect(url_for('proposal_detail', uid=uid))


@app.route('/proposal/<uid>/reject', methods=['POST'])
@login_required
def proposal_reject(uid):
    proposal = Proposal.query.filter_by(uid=uid, seller_id=current_user.id, status='pending').first_or_404()
    proposal.status = 'rejected'
    db.session.commit()
    flash('Proposta recusada.', 'info')
    return redirect(url_for('proposal_detail', uid=uid))


@app.route('/proposal/<uid>/counter', methods=['POST'])
@login_required
def proposal_counter(uid):
    proposal = Proposal.query.filter_by(uid=uid, seller_id=current_user.id, status='pending').first_or_404()
    proposal.status = 'countered'
    proposal.counter_amount = float(request.form.get('counter_amount', 0))
    proposal.counter_message = request.form.get('counter_message', '').strip()
    db.session.commit()
    flash('Contraproposta enviada.', 'success')
    return redirect(url_for('proposal_detail', uid=uid))


@app.route('/proposal/<uid>/accept-counter', methods=['POST'])
@login_required
def proposal_accept_counter(uid):
    proposal = Proposal.query.filter_by(uid=uid, buyer_id=current_user.id, status='countered').first_or_404()
    proposal.status = 'accepted'
    proposal.amount = proposal.counter_amount

    commission = proposal.amount * app.config['COMMISSION_RATE']
    tx = Transaction(
        proposal_id=proposal.id,
        buyer_id=proposal.buyer_id,
        seller_id=proposal.seller_id,
        gross_amount=proposal.amount,
        commission=commission,
        net_amount=proposal.amount - commission,
        payment_status='pending',
        escrow_release_date=datetime.utcnow() + timedelta(days=app.config['ESCROW_RELEASE_DAYS'])
    )
    db.session.add(tx)
    db.session.commit()
    flash('Contraproposta aceita! Transação criada.', 'success')
    return redirect(url_for('proposal_detail', uid=uid))


@app.route('/proposals')
@login_required
def proposals_list():
    tab = request.args.get('tab', 'received')
    if tab == 'sent':
        proposals = Proposal.query.filter_by(buyer_id=current_user.id).order_by(Proposal.created_at.desc()).all()
    else:
        proposals = Proposal.query.filter_by(seller_id=current_user.id).order_by(Proposal.created_at.desc()).all()
    return render_template('proposals/list.html', proposals=proposals, tab=tab)


# ─── CHAT / MESSAGES ───
@app.route('/chat/<uid>/send', methods=['POST'])
@login_required
def chat_send(uid):
    proposal = Proposal.query.filter_by(uid=uid).first_or_404()
    if current_user.id not in (proposal.buyer_id, proposal.seller_id):
        abort(403)

    content = request.form.get('content', '').strip()
    if not content:
        return redirect(url_for('proposal_detail', uid=uid))

    receiver_id = proposal.seller_id if current_user.id == proposal.buyer_id else proposal.buyer_id
    msg = Message(
        sender_id=current_user.id,
        receiver_id=receiver_id,
        proposal_id=proposal.id,
        content=content
    )
    db.session.add(msg)
    db.session.commit()
    return redirect(url_for('proposal_detail', uid=uid))


@app.route('/messages')
@login_required
def messages_list():
    conversations = db.session.query(Proposal).filter(
        (Proposal.buyer_id == current_user.id) | (Proposal.seller_id == current_user.id)
    ).order_by(Proposal.updated_at.desc()).all()
    return render_template('chat/list.html', conversations=conversations)


# ─── TRANSACTIONS / PAYMENTS ───
@app.route('/transactions')
@login_required
def transactions_list():
    txs = Transaction.query.filter(
        (Transaction.buyer_id == current_user.id) | (Transaction.seller_id == current_user.id)
    ).order_by(Transaction.created_at.desc()).all()
    return render_template('payments/list.html', transactions=txs)


@app.route('/transaction/<uid>')
@login_required
def transaction_detail(uid):
    tx = Transaction.query.filter_by(uid=uid).first_or_404()
    if current_user.id not in (tx.buyer_id, tx.seller_id) and not current_user.is_admin:
        abort(403)
    return render_template('payments/detail.html', tx=tx)


@app.route('/transaction/<uid>/pay', methods=['POST'])
@login_required
def transaction_pay(uid):
    tx = Transaction.query.filter_by(uid=uid, buyer_id=current_user.id, payment_status='pending').first_or_404()
    method = request.form.get('method', 'pix')
    tx.payment_method = method

    # ── Integração Mercado Pago ──
    mp_enabled = bool(app.config.get('MP_ACCESS_TOKEN'))
    if mp_enabled:
        try:
            _listing = tx.proposal.listing if tx.proposal_id else Listing.query.get(tx.listing_id)
            descricao = f'Zanini Scraps — {_listing.title[:80]}' if _listing else 'Zanini Scraps — Lote'
            if method == 'pix':
                resultado = mp.criar_pagamento_pix(
                    tx_uid=tx.uid,
                    valor=tx.gross_amount,
                    descricao=descricao,
                    email_comprador=current_user.email,
                    nome_comprador=current_user.name,
                )
                tx.mp_payment_id   = resultado['payment_id']
                tx.mp_qr_code      = resultado['qr_code']
                tx.mp_qr_base64    = resultado['qr_code_base64']
                tx.payment_status  = 'awaiting_payment'
                db.session.commit()
                flash('QR Code PIX gerado! Escaneie para pagar.', 'success')
                return redirect(url_for('transaction_detail', uid=uid))

            elif method == 'boleto':
                resultado = mp.criar_pagamento_boleto(
                    tx_uid=tx.uid,
                    valor=tx.gross_amount,
                    descricao=descricao,
                    email_comprador=current_user.email,
                    nome_comprador=current_user.name,
                    cpf_cnpj=current_user.document or '00000000000',
                )
                tx.mp_payment_id  = resultado['payment_id']
                tx.mp_boleto_url  = resultado['boleto_url']
                tx.mp_barcode     = resultado['barcode']
                tx.payment_status = 'awaiting_payment'
                db.session.commit()
                flash('Boleto gerado! Acesse o link para imprimir.', 'success')
                return redirect(url_for('transaction_detail', uid=uid))

        except Exception as e:
            app.logger.error(f'Erro MP ao criar pagamento: {e}')
            flash(f'Erro ao gerar pagamento: {str(e)[:120]}', 'danger')
            return redirect(url_for('transaction_detail', uid=uid))

    # ── Modo simulado (sem credenciais MP configuradas) ──
    tx.payment_status = 'escrow'
    tx.escrow_release_date = datetime.utcnow() + timedelta(days=app.config['ESCROW_RELEASE_DAYS'])
    proposal = Proposal.query.get(tx.proposal_id) if tx.proposal_id else None
    _lid = proposal.listing_id if proposal else tx.listing_id
    if _lid:
        listing = Listing.query.get(_lid)
        if listing:
            listing.status = 'reserved'
    db.session.commit()
    flash(f'[MODO TESTE] Pagamento via {method.upper()} simulado.', 'warning')
    return redirect(url_for('transaction_detail', uid=uid))


@app.route('/transaction/<uid>/confirm-delivery', methods=['POST'])
@login_required
def confirm_delivery(uid):
    tx = Transaction.query.filter_by(uid=uid, buyer_id=current_user.id, payment_status='escrow').first_or_404()
    tx.payment_status = 'released'
    tx.logistics_status = 'confirmed'
    tx.delivery_date = datetime.utcnow()

    proposal = Proposal.query.get(tx.proposal_id) if tx.proposal_id else None
    _lid = proposal.listing_id if proposal else tx.listing_id
    if _lid:
        listing = Listing.query.get(_lid)
        if listing:
            listing.status = 'sold'

    executar_repasse(tx)
    db.session.commit()

    if tx.repasse_status == 'enviado':
        flash(f'Entrega confirmada! PIX de { brl(tx.net_amount) } enviado ao vendedor.', 'success')
    elif tx.repasse_status == 'manual':
        flash(f'Entrega confirmada! Repasse de { brl(tx.net_amount) } pendente (vendedor sem PIX cadastrado).', 'warning')
    else:
        flash(f'Entrega confirmada! Repasse de { brl(tx.net_amount) } falhou — verificar painel admin.', 'danger')

    return redirect(url_for('transaction_detail', uid=uid))


# ─── LOGISTICS ───
@app.route('/transaction/<uid>/schedule-pickup', methods=['POST'])
@login_required
def schedule_pickup(uid):
    tx = Transaction.query.filter_by(uid=uid).first_or_404()
    if current_user.id not in (tx.buyer_id, tx.seller_id):
        abort(403)
    pickup_date = request.form.get('pickup_date')
    if pickup_date:
        tx.pickup_date = datetime.strptime(pickup_date, '%Y-%m-%dT%H:%M')
        tx.logistics_status = 'scheduled'
        db.session.commit()
        flash('Retirada agendada!', 'success')
    return redirect(url_for('transaction_detail', uid=uid))


# ─── PROFILE ───
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_user.name = request.form.get('name', current_user.name)
        current_user.phone = request.form.get('phone', current_user.phone)
        current_user.city = request.form.get('city', current_user.city)
        current_user.state = request.form.get('state', current_user.state)
        current_user.cep = request.form.get('cep', current_user.cep)
        current_user.street = request.form.get('street', current_user.street)
        current_user.number = request.form.get('number', current_user.number)
        current_user.neighborhood = request.form.get('neighborhood', current_user.neighborhood)
        pix_key = request.form.get('pix_key', '').strip()
        if pix_key:
            current_user.pix_key = pix_key
            current_user.pix_key_type = request.form.get('pix_key_type', 'email')

        avatar = request.files.get('avatar')
        if avatar and allowed_file(avatar.filename):
            ext = avatar.filename.rsplit('.', 1)[1].lower()
            fname = f"{current_user.uid}.{ext}"
            avatar.save(os.path.join(app.config['UPLOAD_FOLDER'], 'avatars', fname))
            current_user.avatar = f'/static/uploads/avatars/{fname}'

        db.session.commit()
        flash('Perfil atualizado!', 'success')
        return redirect(url_for('profile'))
    return render_template('auth/profile.html')


@app.route('/seller/<uid>')
def seller_profile(uid):
    seller = User.query.filter_by(uid=uid).first_or_404()
    listings = Listing.query.filter_by(seller_id=seller.id, status='active').order_by(Listing.created_at.desc()).all()
    return render_template('auth/seller.html', seller=seller, listings=listings)


# ─── NOTIFICATIONS ───
@app.route('/notifications')
@login_required
def notifications():
    notifs = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(50).all()
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return render_template('dashboard/notifications.html', notifications=notifs)


# ─── WEBHOOK MERCADO PAGO ───
@app.route('/mp-webhook', methods=['POST'])
def mp_webhook():
    """
    Recebe notificações do Mercado Pago e atualiza o status do pagamento automaticamente.
    Configure esta URL no painel MP: https://seudominio.com.br/mp-webhook
    """
    data = request.get_json(silent=True) or {}
    topic = data.get('type') or request.args.get('topic', '')
    resource_id = data.get('data', {}).get('id') or request.args.get('id', '')

    if topic not in ('payment', 'merchant_order') or not resource_id:
        return jsonify({'status': 'ignored'}), 200

    try:
        pagamento = mp.consultar_pagamento(resource_id)
        mp_status = pagamento.get('status')
        tx_uid = pagamento.get('metadata', {}).get('tx_uid')

        if not tx_uid:
            return jsonify({'status': 'sem tx_uid'}), 200

        tx = Transaction.query.filter_by(uid=tx_uid).first()
        if not tx:
            return jsonify({'status': 'tx nao encontrada'}), 200

        if mp_status == 'approved' and tx.payment_status == 'awaiting_payment':
            tx.payment_status = 'escrow'
            tx.escrow_release_date = datetime.utcnow() + timedelta(days=app.config['ESCROW_RELEASE_DAYS'])

            # Marcar listing como reservado
            proposal = Proposal.query.get(tx.proposal_id) if tx.proposal_id else None
            _lid = proposal.listing_id if proposal else tx.listing_id
            if _lid:
                listing = Listing.query.get(_lid)
                if listing:
                    listing.status = 'reserved'

            # Notificar comprador e vendedor
            db.session.add(Notification(
                user_id=tx.buyer_id, type='payment',
                title='Pagamento confirmado!',
                content=f'Seu pagamento de { brl(tx.gross_amount) } foi confirmado pelo Mercado Pago.',
                link=url_for('transaction_detail', uid=tx.uid)
            ))
            db.session.add(Notification(
                user_id=tx.seller_id, type='payment',
                title='Pagamento recebido em escrow!',
                content=f'O comprador pagou { brl(tx.gross_amount) }. Valor retido até confirmação da entrega.',
                link=url_for('transaction_detail', uid=tx.uid)
            ))

            send_email(tx.buyer.email, 'Pagamento confirmado!',
                f'<p>Seu pagamento de <strong>{ brl(tx.gross_amount) }</strong> foi confirmado. '
                f'Agora aguarde a entrega do material.</p>'
                f'<p><a href="{url_for("transaction_detail", uid=tx.uid, _external=True)}">Ver transação</a></p>')

            db.session.commit()
            app.logger.info(f'Pagamento MP {resource_id} aprovado para tx {tx_uid}')

        elif mp_status in ('rejected', 'cancelled') and tx.payment_status == 'awaiting_payment':
            tx.payment_status = 'pending'  # volta para pendente, comprador pode tentar novamente
            tx.mp_payment_id = None
            tx.mp_qr_code = None
            tx.mp_qr_base64 = None
            db.session.commit()

    except Exception as e:
        app.logger.error(f'Erro no webhook MP: {e}')
        return jsonify({'status': 'erro'}), 500

    return jsonify({'status': 'ok'}), 200


# ─── ADMIN ───
@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    total_users = User.query.count()
    total_listings = Listing.query.count()
    active_listings = Listing.query.filter_by(status='active').count()
    total_proposals = Proposal.query.count()
    total_transactions = Transaction.query.count()
    total_gmv = db.session.query(db.func.sum(Transaction.gross_amount)).scalar() or 0
    total_commission = db.session.query(db.func.sum(Transaction.commission)).scalar() or 0

    recent_users = User.query.order_by(User.created_at.desc()).limit(10).all()
    recent_transactions = Transaction.query.order_by(Transaction.created_at.desc()).limit(10).all()
    recent_listings = Listing.query.order_by(Listing.created_at.desc()).limit(10).all()

    return render_template('admin/dashboard.html',
                           total_users=total_users,
                           total_listings=total_listings,
                           active_listings=active_listings,
                           total_proposals=total_proposals,
                           total_transactions=total_transactions,
                           total_gmv=total_gmv,
                           total_commission=total_commission,
                           recent_users=recent_users,
                           recent_transactions=recent_transactions,
                           recent_listings=recent_listings)


@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=users)


@app.route('/admin/listings')
@login_required
@admin_required
def admin_listings():
    listings = Listing.query.order_by(Listing.created_at.desc()).all()
    return render_template('admin/listings.html', listings=listings)


@app.route('/admin/transactions')
@login_required
@admin_required
def admin_transactions():
    txs = Transaction.query.order_by(Transaction.created_at.desc()).all()
    return render_template('admin/transactions.html', transactions=txs)


# ─── API endpoints ───
@app.route('/api/search-suggestions')
def search_suggestions():
    q = request.args.get('q', '')
    if len(q) < 2:
        return jsonify([])
    results = Listing.query.filter(
        Listing.status == 'active',
        (Listing.title.ilike(f'%{q}%')) | (Listing.material_type.ilike(f'%{q}%'))
    ).limit(8).all()
    return jsonify([{
        'id': r.uid, 'title': r.title, 'material': r.material_type,
        'price': r.price, 'city': r.city, 'state': r.state
    } for r in results])


@app.route('/api/freight-estimate', methods=['POST'])
def freight_estimate():
    distance_km = float(request.json.get('distance_km', 0))
    volume_m3 = float(request.json.get('volume_m3', 1))
    rate = app.config['FREIGHT_RATE_PER_KM']
    cost = distance_km * rate * max(1, volume_m3 * 0.3)
    return jsonify({'estimated_cost': round(cost, 2), 'distance_km': distance_km})


# ─── VENDA IMEDIATA ───
@app.route('/listing/<uid>/comprar-agora', methods=['POST'])
@login_required
def buy_now(uid):
    listing = Listing.query.filter_by(uid=uid, status='active', venda_imediata=True).first_or_404()
    if listing.seller_id == current_user.id:
        flash('Você não pode comprar seu próprio lote.', 'danger')
        return redirect(url_for('listing_detail', uid=uid))

    commission = listing.price * app.config['COMMISSION_RATE']
    tx = Transaction(
        listing_id=listing.id,
        buyer_id=current_user.id,
        seller_id=listing.seller_id,
        gross_amount=listing.price,
        commission=commission,
        net_amount=listing.price - commission,
        payment_status='pending',
        escrow_release_date=datetime.utcnow() + timedelta(days=app.config['ESCROW_RELEASE_DAYS'])
    )
    db.session.add(tx)
    listing.status = 'reserved'

    notif = Notification(
        user_id=listing.seller_id,
        type='transaction',
        title='Compra imediata recebida!',
        content=f'{current_user.name} comprou seu lote "{listing.title[:60]}" por {brl(listing.price)}.',
        link='#'
    )
    db.session.add(notif)
    db.session.commit()

    flash('Lote reservado! Realize o pagamento para confirmar.', 'success')
    return redirect(url_for('transaction_detail', uid=tx.uid))



# ─── AUTO-RELEASE D+2 ───
@app.route('/cron/release-escrow')
def cron_release_escrow():
    """
    Libera automaticamente transações em escrow vencidas (D+2).
    Chamar via Railway Cron: GET /cron/release-escrow (a cada hora).
    Protegido por token simples no header ou query string.
    """
    token = request.args.get('token') or request.headers.get('X-Cron-Token', '')
    cron_secret = app.config.get('SECRET_KEY', '')[:16]
    if token != cron_secret:
        abort(403)

    now = datetime.utcnow()
    vencidas = Transaction.query.filter(
        Transaction.payment_status == 'escrow',
        Transaction.escrow_release_date <= now,
    ).all()

    liberadas = 0
    for tx in vencidas:
        tx.payment_status = 'released'
        tx.delivery_date = now

        proposal = Proposal.query.get(tx.proposal_id) if tx.proposal_id else None
        _lid = proposal.listing_id if proposal else tx.listing_id
        if _lid:
            listing = Listing.query.get(_lid)
            if listing:
                listing.status = 'sold'

        executar_repasse(tx)

        db.session.add(Notification(
            user_id=tx.buyer_id, type='transaction',
            title='Transação finalizada automaticamente (D+2)',
            content=f'O prazo de confirmação expirou. Transação encerrada.',
            link=url_for('transaction_detail', uid=tx.uid)
        ))
        db.session.add(Notification(
            user_id=tx.seller_id, type='payment',
            title='Repasse liberado (D+2)',
            content=f'Pagamento de { brl(tx.net_amount) } liberado automaticamente. Status: {tx.repasse_status}.',
            link=url_for('transaction_detail', uid=tx.uid)
        ))
        liberadas += 1

    db.session.commit()
    app.logger.info(f'Cron D+2: {liberadas} transações liberadas.')
    return jsonify({'liberadas': liberadas, 'timestamp': now.isoformat()}), 200


# ─── MIGRATION (admin, executar 1x) ───
@app.route('/admin/migrate-venda-imediata', methods=['GET', 'POST'])
@login_required
@admin_required
def migrate_venda_imediata():
    try:
        stmts = [
            # venda imediata (rodado antes — idempotente)
            "ALTER TABLE listings ADD COLUMN IF NOT EXISTS venda_imediata BOOLEAN DEFAULT FALSE NOT NULL",
            "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS listing_id INTEGER REFERENCES listings(id)",
            # repasse PIX
            "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS repasse_status VARCHAR(20) DEFAULT 'pendente'",
            "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS repasse_transfer_id VARCHAR(64)",
            "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS repasse_at TIMESTAMP",
            "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS repasse_obs VARCHAR(300)",
            # chave PIX do usuário
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS pix_key VARCHAR(150)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS pix_key_type VARCHAR(20)",
        ]
        for s in stmts:
            db.session.execute(db.text(s))
        # torna proposal_id nullable (ignora erro se já for)
        try:
            db.session.execute(db.text("ALTER TABLE transactions ALTER COLUMN proposal_id DROP NOT NULL"))
        except Exception:
            db.session.rollback()
        db.session.commit()
        flash('Migracao executada com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro na migracao: {str(e)[:200]}', 'danger')
    return redirect(url_for('admin_dashboard'))


# ─── Error handlers ───
@app.errorhandler(404)
def not_found(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(403)
def forbidden(e):
    return render_template('errors/403.html'), 403

@app.errorhandler(500)
def server_error(e):
    return render_template('errors/500.html'), 500


# ─── Init DB ───
# db.create_all() é executado via init_db.py — não executar aqui para evitar
# crash no boot se o banco ainda não estiver pronto.


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
