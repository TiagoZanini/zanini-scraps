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

from flask_jwt_extended import JWTManager
from flask_cors import CORS
from flask_wtf import CSRFProtect

from config import Config
from modules.models import (db, User, Category, Listing, ListingImage,
                            Proposal, Message, Transaction, Favorite, Notification)
from modules import mercadopago as mp
from modules import asaas
from modules import solana_chain as sol
from modules.api import api as api_blueprint

app = Flask(__name__)
app.config.from_object(Config)
app.config['JWT_SECRET_KEY'] = app.config['SECRET_KEY']
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=30)

mail = Mail(app)
jwt = JWTManager(app)
CORS(app, resources={r'/api/*': {'origins': '*'}})
csrf = CSRFProtect(app)
app.config['WTF_CSRF_TIME_LIMIT'] = None  # token vale pela sessão inteira

# ── Filtro Jinja: formato BRL (R$ 1.234,56) ──
def _brl(value):
    try:
        s = f"{float(value):,.2f}"          # "1,234.56"
        int_part, dec_part = s.split('.')
        return f"R$ {int_part.replace(',', '.')},{dec_part}"
    except Exception:
        return f"R$ {value}"

app.jinja_env.filters['brl'] = _brl

# ── Filtro de data/hora no fuso do Brasil (UTC-3) ──
from datetime import timezone as _tz
_BR_TZ = _tz(timedelta(hours=-3))

def _brdt(dt, fmt='%d/%m/%Y às %H:%M'):
    if dt is None:
        return ''
    return dt.replace(tzinfo=_tz.utc).astimezone(_BR_TZ).strftime(fmt)

app.jinja_env.filters['brdt'] = _brdt

# ── Status em português ──
_STATUS_PT = {
    # listing
    'draft': 'Rascunho', 'active': 'Ativo', 'reserved': 'Reservado',
    'sold': 'Vendido', 'expired': 'Expirado',
    # proposta
    'pending': 'Pendente', 'accepted': 'Aceita', 'rejected': 'Recusada',
    'countered': 'Contraproposta', 'expired_proposal': 'Expirada',
    # pagamento
    'awaiting_payment': 'Aguardando pagamento', 'escrow': 'Pago — em custódia',
    'released': 'Concluída', 'refunded': 'Reembolsada', 'disputed': 'Em disputa',
    'cancelled': 'Cancelada',
    # logística
    'scheduled': 'Agendado', 'in_transit': 'Em trânsito',
    'delivered': 'Entregue', 'confirmed': 'Entrega confirmada',
    # repasse
    'pendente': 'Pendente', 'enviado': 'Enviado', 'falhou': 'Repasse manual', 'manual': 'Repasse manual',
}

def _status_pt(value):
    if not value:
        return '—'
    return _STATUS_PT.get(str(value).lower(), str(value).capitalize())

app.jinja_env.filters['status_pt'] = _status_pt

# ── Helper para strings Python (flash/notificações) ──
def brl(value):
    return _brl(value)

# ── Força charset UTF-8 no HTML ──
@app.after_request
def force_charset(response):
    if response.content_type.startswith('text/html'):
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
app.register_blueprint(api_blueprint)
csrf.exempt(api_blueprint)  # API usa Bearer JWT, sem cookies

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
    return '.' in filename and safe_ext(filename) in app.config['ALLOWED_EXTENSIONS']


def safe_ext(filename):
    """Extensão sanitizada: só letras/números minúsculos."""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return ''.join(c for c in ext if c.isalnum())


def salvar_imagem_listing(listing, f, is_main, order):
    """Guarda a imagem no banco (o disco do Railway é efêmero) e retorna o ListingImage."""
    data = f.read()
    if not data or len(data) > 16 * 1024 * 1024:
        return None
    img = ListingImage(
        listing_id=listing.id,
        path='',
        data=data,
        mimetype=f.mimetype if (f.mimetype or '').startswith('image/') else f'image/{safe_ext(f.filename) or "jpeg"}',
        is_main=is_main,
        order=order,
    )
    db.session.add(img)
    db.session.flush()
    img.path = f'/media/{img.id}'
    return img


def executar_repasse(tx):
    """
    Tenta enviar PIX ao vendedor via MP.
    Atualiza tx.repasse_status: 'enviado', 'falhou' ou 'manual'.
    Não faz commit — chame db.session.commit() após.
    """
    if tx.repasse_status not in (None, '', 'pendente'):
        return  # idempotência: nunca repassar duas vezes
    seller = User.query.get(tx.seller_id)

    # ── Pagamento feito via Solana: repasse on-chain em USDC ──
    if tx.payment_method == 'solana' and sol.habilitado():
        if not seller or not seller.solana_wallet:
            tx.repasse_status = 'manual'
            tx.repasse_obs = 'Vendedor sem carteira Solana cadastrada.'
            return
        try:
            liquido_usdc = round((tx.sol_amount_usdc or 0) * (tx.net_amount / tx.gross_amount), 2)
            resultado = sol.repasse_usdc(seller.solana_wallet, liquido_usdc)
            tx.sol_repasse_sig = resultado['signature']
            tx.repasse_status = 'enviado'
            tx.repasse_transfer_id = resultado['signature'][:60]
            tx.repasse_at = datetime.utcnow()
            tx.repasse_obs = f'{liquido_usdc} USDC on-chain: {sol.link_explorer(resultado["signature"])}'
            app.logger.info(f'Repasse Solana tx {tx.uid}: {resultado["signature"]}')
        except Exception as e:
            tx.repasse_status = 'falhou'
            tx.repasse_obs = f'Erro Solana: {str(e)[:250]}'
            app.logger.error(f'Repasse Solana tx {tx.uid} falhou: {e}')
        return

    if not seller or not seller.pix_key:
        tx.repasse_status = 'manual'
        tx.repasse_obs = 'Vendedor sem chave PIX cadastrada. Transferir manualmente.'
        return

    if not asaas.habilitado() and not app.config.get('MP_ACCESS_TOKEN'):
        tx.repasse_status = 'manual'
        tx.repasse_obs = 'Sem gateway configurado — ambiente de teste.'
        return

    try:
        if asaas.habilitado():
            result = asaas.transferir_pix(
                valor=tx.net_amount,
                pix_key=seller.pix_key,
                pix_key_type=seller.pix_key_type or 'email',
                descricao=f'Zanini Scraps — repasse {tx.uid[:8]}',
            )
        else:
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
    fav_ids = set()
    if current_user.is_authenticated:
        unread_msgs = Message.query.filter_by(receiver_id=current_user.id, is_read=False).count()
        unread_notifs = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
        fav_ids = {f.listing_id for f in Favorite.query.filter_by(user_id=current_user.id).all()}
    return dict(
        fav_ids=fav_ids,
        platform_name='Zanini Scraps',
        current_year=datetime.utcnow().year,
        unread_messages=unread_msgs,
        unread_notifications=unread_notifs,
        asaas_enabled=asaas.habilitado(),
        solana_enabled=sol.habilitado(),
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
            next_page = request.args.get('next', '')
            # só redireciona para caminhos internos (evita open redirect)
            if not next_page.startswith('/') or next_page.startswith('//'):
                next_page = None
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

        if not name or '@' not in email or '.' not in email.split('@')[-1]:
            flash('Preencha nome e um e-mail válido.', 'danger')
            return render_template('auth/register.html')
        if len(password) < 6:
            flash('A senha deve ter pelo menos 6 caracteres.', 'danger')
            return render_template('auth/register.html')

        if User.query.filter_by(email=email).first():
            flash('E-mail já cadastrado.', 'danger')
            return render_template('auth/register.html')

        if document and User.query.filter_by(document=document).first():
            flash('CNPJ/CPF já cadastrado.', 'danger')
            return render_template('auth/register.html')

        user = User(
            name=name, email=email, role=role,
            document_type=document_type, document=document or None,
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
    most_viewed = Listing.query.filter_by(status='active').filter(Listing.views > 0).order_by(Listing.views.desc()).limit(4).all()
    stats = {
        'total_listings': Listing.query.filter_by(status='active').count(),
        'total_users': User.query.count(),
        'total_transactions': Transaction.query.count(),
        'total_gmv': db.session.query(db.func.sum(Transaction.gross_amount)).scalar() or 0
    }
    return render_template('home.html', featured=featured, recent=recent,
                           most_viewed=most_viewed, stats=stats)


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

    mat_q = db.session.query(Listing.material_type).filter(
        Listing.status == 'active', Listing.material_type.isnot(None), Listing.material_type != ''
    )
    if cat:
        mat_q = mat_q.filter(Listing.category_id == cat)
    materials = mat_q.distinct().all()
    materials = sorted(set(m[0] for m in materials if m[0]))

    states_list = ['AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS',
                   'MG', 'PA', 'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC',
                   'SP', 'SE', 'TO']

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
        def _num(campo, padrao=0):
            v = str(request.form.get(campo, padrao) or padrao).strip()
            if ',' in v:
                v = v.replace('.', '').replace(',', '.')
            try:
                return float(v)
            except ValueError:
                return float(padrao)

        status_values = request.form.getlist('status')
        status_final = 'draft' if 'draft' in status_values else 'active'
        if _num('price') <= 0:
            flash('Informe um preço válido para o anúncio.', 'danger')
            return render_template('listings/create.html')
        if status_final == 'active' and not request.form.get('has_invoice'):
            flash('Para publicar é obrigatório declarar a nota fiscal de origem do material.', 'danger')
            return render_template('listings/create.html')
        if status_final == 'active' and not request.form.get('category_id'):
            flash('Selecione a categoria do material.', 'danger')
            return render_template('listings/create.html')
        listing = Listing(
            seller_id=current_user.id,
            title=request.form.get('title', '').strip(),
            description=request.form.get('description', '').strip(),
            category_id=request.form.get('category_id') or None,
            material_type=request.form.get('material_type', '').strip(),
            condition=request.form.get('condition', 'usado'),
            quantity=_num('quantity'),
            unit=request.form.get('unit', 'kg'),
            price=_num('price'),
            price_type=request.form.get('price_type', 'total'),
            min_order=_num('min_order'),
            city=request.form.get('city', current_user.city or ''),
            state=request.form.get('state', current_user.state or ''),
            cep=request.form.get('cep', ''),
            delivery_type=request.form.get('delivery_type', 'retirada'),
            origin_declaration=request.form.get('origin_declaration', ''),
            has_invoice=bool(request.form.get('has_invoice')),
            availability=request.form.get('availability', ''),
            observations=request.form.get('observations', ''),
            who_picks_up={'retirada': 'comprador', 'entrega': 'vendedor'}.get(
                request.form.get('delivery_type', 'retirada'), 'negociavel'),
            venda_imediata=bool(request.form.get('venda_imediata')),
            status=status_final,
        )
        db.session.add(listing)
        db.session.flush()

        # Upload de imagens (armazenadas no banco — persistem entre deploys)
        files = request.files.getlist('images')
        for i, f in enumerate(files[:5]):
            if f and f.filename and allowed_file(f.filename):
                salvar_imagem_listing(listing, f, is_main=(i == 0), order=i)

        db.session.commit()
        if status_final == 'draft':
            flash('Rascunho salvo. Ele não aparece no marketplace até você publicar.', 'success')
        else:
            flash('Anúncio publicado com sucesso!', 'success')
        return redirect(url_for('listing_detail', uid=listing.uid))

    return render_template('listings/create.html')


@app.route('/listing/<uid>/edit', methods=['GET', 'POST'])
@login_required
def listing_edit(uid):
    listing = Listing.query.filter_by(uid=uid, seller_id=current_user.id).first_or_404()
    if request.method == 'POST':
        # lote com transação em andamento não pode ser alterado (evita revenda/alteração de preço pós-acordo)
        tx_ativa = Transaction.query.filter(
            ((Transaction.listing_id == listing.id) |
             (Transaction.proposal_id.in_(db.session.query(Proposal.id).filter_by(listing_id=listing.id)))),
            Transaction.payment_status.in_(['pending', 'awaiting_payment', 'escrow'])
        ).first()
        if tx_ativa or listing.status in ('reserved', 'sold'):
            flash('Este lote tem uma negociação em andamento e não pode ser editado.', 'danger')
            return redirect(url_for('listing_detail', uid=listing.uid))
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
        novo_status = request.form.get('status', listing.status)
        if novo_status in ('active', 'draft'):   # nunca aceitar status arbitrário do form
            listing.status = novo_status
        if listing.status == 'active' and not listing.has_invoice:
            db.session.rollback()
            flash('Para manter o anúncio publicado é obrigatório declarar a nota fiscal de origem.', 'danger')
            return redirect(url_for('listing_edit', uid=listing.uid))

        # Novas imagens (armazenadas no banco)
        files = request.files.getlist('images')
        base_order = listing.images.count()
        for i, f in enumerate(files[:5]):
            if f and f.filename and allowed_file(f.filename):
                salvar_imagem_listing(listing, f, is_main=(base_order + i == 0), order=base_order + i)

        db.session.commit()
        flash('Anúncio atualizado!', 'success')
        return redirect(url_for('listing_detail', uid=listing.uid))

    return render_template('listings/edit.html', listing=listing)


@app.route('/listing/<uid>/delete', methods=['POST'])
@login_required
def listing_delete(uid):
    listing = Listing.query.filter_by(uid=uid, seller_id=current_user.id).first_or_404()
    tem_tx = Transaction.query.filter(
        (Transaction.listing_id == listing.id) |
        (Transaction.proposal_id.in_(db.session.query(Proposal.id).filter_by(listing_id=listing.id)))
    ).first()
    if tem_tx:
        flash('Este lote tem transações vinculadas e não pode ser excluído. Ele foi pausado (rascunho).', 'warning')
        listing.status = 'draft'
        db.session.commit()
        return redirect(url_for('dashboard'))
    # limpa referências sem cascade antes de excluir
    Favorite.query.filter_by(listing_id=listing.id).delete()
    Message.query.filter_by(listing_id=listing.id).update({'listing_id': None})
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

    try:
        raw = str(request.form.get('amount', 0) or 0)
        amount = float(raw.replace('.', '').replace(',', '.') if ',' in raw else raw)
    except ValueError:
        amount = 0
    if amount <= 0 or amount > 10_000_000:
        flash('Informe um valor válido para a proposta.', 'danger')
        return redirect(url_for('listing_detail', uid=uid))
    quantity = float(request.form.get('quantity', listing.quantity) or listing.quantity)
    message = request.form.get('message', '').strip()

    ja_existe = Proposal.query.filter_by(listing_id=listing.id, buyer_id=current_user.id, status='pending').first()
    if ja_existe:
        flash('Você já tem uma proposta pendente neste lote. Aguarde a resposta do vendedor.', 'warning')
        return redirect(url_for('proposal_detail', uid=ja_existe.uid))

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

    seller = listing.seller
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
    if not proposal.amount or proposal.amount <= 0:
        flash('Proposta sem valor válido — rejeite-a.', 'danger')
        return redirect(url_for('proposal_detail', uid=uid))
    # reserva atômica: só prossegue se o lote ainda estiver ativo (evita corrida)
    reservado = Listing.query.filter_by(id=proposal.listing_id, status='active').update({'status': 'reserved'})
    if not reservado:
        db.session.rollback()
        flash('Este lote não está mais disponível.', 'danger')
        return redirect(url_for('proposal_detail', uid=uid))
    proposal.status = 'accepted'
    Proposal.query.filter(Proposal.listing_id == proposal.listing_id,
                          Proposal.id != proposal.id,
                          Proposal.status == 'pending').update({'status': 'rejected'})

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
    # vendedor rejeita proposta pendente OU comprador rejeita contraproposta
    proposal = Proposal.query.filter(
        Proposal.uid == uid,
        ((Proposal.seller_id == current_user.id) & (Proposal.status == 'pending')) |
        ((Proposal.buyer_id == current_user.id) & (Proposal.status == 'countered'))
    ).first_or_404()
    proposal.status = 'rejected'
    db.session.commit()
    flash('Proposta recusada.', 'info')
    return redirect(url_for('proposal_detail', uid=uid))


@app.route('/proposal/<uid>/counter', methods=['POST'])
@login_required
def proposal_counter(uid):
    proposal = Proposal.query.filter_by(uid=uid, seller_id=current_user.id, status='pending').first_or_404()
    try:
        counter_amount = float(str(request.form.get('counter_amount', 0)).replace(',', '.'))
    except ValueError:
        counter_amount = 0
    if counter_amount <= 0:
        flash('Informe um valor válido para a contraproposta.', 'danger')
        return redirect(url_for('proposal_detail', uid=uid))
    proposal.status = 'countered'
    proposal.counter_amount = counter_amount
    proposal.counter_message = request.form.get('counter_message', '').strip()
    db.session.commit()
    flash('Contraproposta enviada.', 'success')
    return redirect(url_for('proposal_detail', uid=uid))


@app.route('/proposal/<uid>/accept-counter', methods=['POST'])
@login_required
def proposal_accept_counter(uid):
    proposal = Proposal.query.filter_by(uid=uid, buyer_id=current_user.id, status='countered').first_or_404()
    if not proposal.counter_amount or proposal.counter_amount <= 0:
        flash('Contraproposta sem valor válido.', 'danger')
        return redirect(url_for('proposal_detail', uid=uid))
    # reserva atômica: só prossegue se o lote ainda estiver ativo (evita corrida)
    reservado = Listing.query.filter_by(id=proposal.listing_id, status='active').update({'status': 'reserved'})
    if not reservado:
        db.session.rollback()
        flash('Este lote não está mais disponível.', 'danger')
        return redirect(url_for('proposal_detail', uid=uid))
    proposal.status = 'accepted'
    proposal.amount = proposal.counter_amount
    Proposal.query.filter(Proposal.listing_id == proposal.listing_id,
                          Proposal.id != proposal.id,
                          Proposal.status == 'pending').update({'status': 'rejected'})

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
        listing_id=proposal.listing_id,
        content=content
    )
    db.session.add(msg)
    db.session.commit()
    return redirect(url_for('proposal_detail', uid=uid))


# ─── CHAT POR ANÚNCIO (estilo OLX) ───
@app.route('/chat/l/<listing_uid>')
@app.route('/chat/l/<listing_uid>/<user_uid>')
@login_required
def chat_thread(listing_uid, user_uid=None):
    listing = Listing.query.filter_by(uid=listing_uid).first_or_404()
    if user_uid:
        other = User.query.filter_by(uid=user_uid).first_or_404()
    elif current_user.id != listing.seller_id:
        other = listing.seller          # comprador abrindo chat com o vendedor
    else:
        return redirect(url_for('messages_list'))

    if current_user.id != listing.seller_id and other.id != listing.seller_id:
        abort(403)
    if other.id == current_user.id:
        return redirect(url_for('messages_list'))

    msgs = Message.query.filter(
        Message.listing_id == listing.id,
        ((Message.sender_id == current_user.id) & (Message.receiver_id == other.id)) |
        ((Message.sender_id == other.id) & (Message.receiver_id == current_user.id))
    ).order_by(Message.created_at.asc()).all()

    Message.query.filter_by(listing_id=listing.id, receiver_id=current_user.id,
                            sender_id=other.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return render_template('chat/thread.html', listing=listing, other=other, messages=msgs)


@app.route('/chat/l/<listing_uid>/<user_uid>/send', methods=['POST'])
@login_required
def chat_thread_send(listing_uid, user_uid):
    listing = Listing.query.filter_by(uid=listing_uid).first_or_404()
    other = User.query.filter_by(uid=user_uid).first_or_404()
    if current_user.id != listing.seller_id and other.id != listing.seller_id:
        abort(403)
    content = request.form.get('content', '').strip()
    if content:
        db.session.add(Message(
            sender_id=current_user.id, receiver_id=other.id,
            listing_id=listing.id, content=content
        ))
        db.session.commit()
    return redirect(url_for('chat_thread', listing_uid=listing_uid, user_uid=user_uid))


@app.route('/messages')
@login_required
def messages_list():
    my_msgs = Message.query.filter(
        Message.listing_id.isnot(None),
        (Message.sender_id == current_user.id) | (Message.receiver_id == current_user.id)
    ).order_by(Message.created_at.desc()).all()

    threads, seen = [], set()
    for m in my_msgs:
        other_id = m.receiver_id if m.sender_id == current_user.id else m.sender_id
        key = (m.listing_id, other_id)
        if key in seen:
            continue
        seen.add(key)
        unread = Message.query.filter_by(listing_id=m.listing_id, sender_id=other_id,
                                         receiver_id=current_user.id, is_read=False).count()
        threads.append({'listing': m.listing,
                        'other': m.sender if m.sender_id != current_user.id else m.receiver,
                        'last': m, 'unread': unread})
    return render_template('chat/list.html', threads=threads)


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
    _listing_desc = tx.display_title[:80]

    # ── Integração Asaas (prioritária quando configurada) ──
    if asaas.habilitado():
        try:
            cliente_id = asaas.obter_ou_criar_cliente(
                current_user.name, current_user.document, current_user.email)
            resultado = asaas.criar_cobranca(
                tx_uid=tx.uid, valor=tx.gross_amount,
                descricao=f'Zanini Scraps — {_listing_desc}', cliente_id=cliente_id)
            tx.asaas_payment_id  = resultado['payment_id']
            tx.asaas_invoice_url = resultado['invoice_url']
            tx.mp_qr_code   = resultado['qr_code']
            tx.mp_qr_base64 = resultado['qr_base64']
            tx.payment_status = 'awaiting_payment'
            db.session.commit()
            flash('Cobrança gerada! Pague por PIX ou cartão.', 'success')
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Erro Asaas ao criar cobrança: {e}')
            flash(f'{str(e)[:160]}', 'danger')
        return redirect(url_for('transaction_detail', uid=uid))

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


@app.route('/transaction/<uid>/cancel', methods=['POST'])
@login_required
def transaction_cancel(uid):
    """Cancela negociação não paga — libera o lote de volta ao marketplace."""
    tx = Transaction.query.filter(
        Transaction.uid == uid,
        (Transaction.buyer_id == current_user.id) | (Transaction.seller_id == current_user.id),
        Transaction.payment_status.in_(['pending', 'awaiting_payment'])
    ).first_or_404()

    tx.payment_status = 'cancelled'
    listing = tx.listing_ref
    if listing and listing.status == 'reserved':
        listing.status = 'active'
    if tx.proposal:
        tx.proposal.status = 'rejected'

    outro = tx.seller_id if current_user.id == tx.buyer_id else tx.buyer_id
    db.session.add(Notification(
        user_id=outro, type='transaction',
        title='Negociação cancelada',
        content=f'{current_user.name} cancelou a negociação de "{tx.display_title[:50]}". O lote voltou ao marketplace.',
        link=f'/transaction/{tx.uid}'
    ))
    db.session.commit()
    flash('Negociação cancelada. O lote voltou a ficar disponível no marketplace.', 'info')
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
        try:
            fmt = '%Y-%m-%dT%H:%M' if 'T' in pickup_date else '%Y-%m-%d'
            tx.pickup_date = datetime.strptime(pickup_date, fmt)
        except ValueError:
            flash('Data inválida.', 'danger')
            return redirect(url_for('transaction_detail', uid=uid))
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
        novo_doc = request.form.get('document', '').strip()
        if novo_doc:
            ja_usado = User.query.filter(User.document == novo_doc, User.id != current_user.id).first()
            if ja_usado:
                flash('Este CPF/CNPJ já está cadastrado em outra conta.', 'danger')
                return redirect(url_for('profile'))
            current_user.document = novo_doc
            current_user.document_type = 'cnpj' if len(''.join(c for c in novo_doc if c.isdigit())) == 14 else 'cpf'
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
        solana_wallet = request.form.get('solana_wallet', '').strip()
        if solana_wallet:
            current_user.solana_wallet = solana_wallet

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


# ─── WEBHOOK ASAAS ───
@app.route('/asaas-webhook', methods=['POST'])
@csrf.exempt
def asaas_webhook():
    """Recebe eventos de cobrança do Asaas e confirma pagamentos."""
    if not asaas.verificar_webhook(request.headers):
        return jsonify({'status': 'token invalido'}), 401

    data = request.get_json(silent=True) or {}
    evento = data.get('event', '')
    pagamento = data.get('payment', {}) or {}
    tx_uid = pagamento.get('externalReference', '')

    if evento not in ('PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED') or not tx_uid:
        return jsonify({'status': 'ignored'}), 200

    tx = Transaction.query.filter_by(uid=tx_uid).first()
    if not tx:
        return jsonify({'status': 'tx nao encontrada'}), 200
    if tx.asaas_payment_id and pagamento.get('id') and tx.asaas_payment_id != pagamento.get('id'):
        return jsonify({'status': 'payment_id divergente'}), 200

    if tx.payment_status in ('pending', 'awaiting_payment'):
        tx.payment_status = 'escrow'
        tx.escrow_release_date = datetime.utcnow() + timedelta(days=app.config['ESCROW_RELEASE_DAYS'])
        listing = tx.listing_ref
        if listing and listing.status == 'active':
            listing.status = 'reserved'
        db.session.add(Notification(
            user_id=tx.buyer_id, type='payment',
            title='Pagamento confirmado!',
            content=f'Seu pagamento de {brl(tx.gross_amount)} foi confirmado.',
            link=f'/transaction/{tx.uid}'))
        db.session.add(Notification(
            user_id=tx.seller_id, type='payment',
            title='Pagamento recebido!',
            content=f'O comprador pagou {brl(tx.gross_amount)}. Combine a entrega de "{tx.display_title[:50]}".',
            link=f'/transaction/{tx.uid}'))
        db.session.commit()
        app.logger.info(f'Asaas webhook: tx {tx.uid} em escrow.')
    return jsonify({'status': 'ok'}), 200


# ─── WEBHOOK MERCADO PAGO ───
@app.route('/mp-webhook', methods=['POST'])
@csrf.exempt
def mp_webhook():
    """
    Recebe notificações do Mercado Pago e atualiza o status do pagamento automaticamente.
    Configure esta URL no painel MP: https://seudominio.com.br/mp-webhook
    """
    data = request.get_json(silent=True) or {}
    topic = data.get('type') or request.args.get('topic', '')
    resource_id = str(data.get('data', {}).get('id') or request.args.get('id', ''))

    if topic not in ('payment', 'merchant_order') or not resource_id.isdigit():
        return jsonify({'status': 'ignored'}), 200

    if not mp.verificar_webhook(resource_id,
                                request.headers.get('x-signature', ''),
                                request.headers.get('x-request-id', '')):
        return jsonify({'status': 'assinatura invalida'}), 401

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
@csrf.exempt
def freight_estimate():
    data = request.get_json(silent=True) or {}
    try:
        distance_km = float(data.get('distance_km', 0))
        volume_m3 = float(data.get('volume_m3', 1))
    except (TypeError, ValueError):
        return jsonify({'error': 'Parâmetros inválidos'}), 400
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
    if not listing.price or listing.price <= 0:
        flash('Este lote está sem preço válido.', 'danger')
        return redirect(url_for('listing_detail', uid=uid))
    # reserva atômica (evita duas compras simultâneas)
    reservado = Listing.query.filter_by(id=listing.id, status='active').update({'status': 'reserved'})
    if not reservado:
        db.session.rollback()
        flash('Este lote acabou de ser reservado por outro comprador.', 'danger')
        return redirect(url_for('listing_detail', uid=uid))

    valor = listing.lot_total
    commission = valor * app.config['COMMISSION_RATE']
    tx = Transaction(
        listing_id=listing.id,
        buyer_id=current_user.id,
        seller_id=listing.seller_id,
        gross_amount=valor,
        commission=commission,
        net_amount=valor - commission,
        payment_status='pending',
        escrow_release_date=datetime.utcnow() + timedelta(days=app.config['ESCROW_RELEASE_DAYS'])
    )
    db.session.add(tx)
    db.session.flush()

    notif = Notification(
        user_id=listing.seller_id,
        type='transaction',
        title='Compra imediata recebida!',
        content=f'{current_user.name} comprou seu lote "{listing.title[:60]}" por {brl(listing.price)}.',
        link=f'/transaction/{tx.uid}'
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
    import hmac as _hmac
    cron_secret = app.config.get('CRON_TOKEN', '')
    if not cron_secret:
        abort(404)  # rota desabilitada até configurar CRON_TOKEN no ambiente
    token = request.headers.get('X-Cron-Token', '') or request.args.get('token', '')
    if not _hmac.compare_digest(token, cron_secret):
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


@app.route('/favoritos')
@login_required
def favorites_list():
    favs = Favorite.query.filter_by(user_id=current_user.id).order_by(Favorite.created_at.desc()).all()
    listings = [f.listing for f in favs if f.listing]
    return render_template('listings/favorites.html', listings=listings)


# ─── SOLANA PAY (hackathon) ───
BRL_PER_USDC = float(os.environ.get('BRL_PER_USDC', '5.0'))  # conversão demo BRL→USDC


@app.route('/transaction/<uid>/pay-solana', methods=['POST'])
@login_required
def transaction_pay_solana(uid):
    """Gera cobrança Solana Pay (USDC devnet) para a transação."""
    if not sol.habilitado():
        abort(404)
    tx = Transaction.query.filter_by(uid=uid, buyer_id=current_user.id, payment_status='pending').first_or_404()
    tx.sol_reference = sol.gerar_referencia()
    tx.sol_amount_usdc = round(tx.gross_amount / BRL_PER_USDC, 2)
    tx.payment_method = 'solana'
    tx.payment_status = 'awaiting_payment'
    db.session.commit()
    flash('Cobrança Solana gerada! Escaneie o QR com sua carteira (Phantom).', 'success')
    return redirect(url_for('transaction_detail', uid=uid))


@app.route('/solana/qr/<uid>.png')
@login_required
def solana_qr(uid):
    """QR Code do link Solana Pay da transação."""
    from flask import Response
    import io, segno
    tx = Transaction.query.filter_by(uid=uid).first_or_404()
    if current_user.id not in (tx.buyer_id, tx.seller_id) or not tx.sol_reference:
        abort(404)
    url = sol.link_pagamento(tx.sol_amount_usdc, tx.sol_reference,
                             mensagem=tx.display_title[:50])
    buf = io.BytesIO()
    segno.make(url, error='m').save(buf, kind='png', scale=6, dark='#14F195', light='#0b0b0c')
    return Response(buf.getvalue(), mimetype='image/png')


@app.route('/solana/status/<uid>')
@login_required
def solana_status(uid):
    """Polling: verifica na chain se o pagamento da transação confirmou."""
    tx = Transaction.query.filter_by(uid=uid).first_or_404()
    if current_user.id not in (tx.buyer_id, tx.seller_id):
        abort(403)
    if tx.payment_status == 'escrow' or tx.payment_status == 'released':
        return jsonify({'paid': True, 'signature': tx.sol_payment_sig,
                        'explorer': sol.link_explorer(tx.sol_payment_sig) if tx.sol_payment_sig else None})
    if not tx.sol_reference:
        return jsonify({'paid': False})
    try:
        assinatura = sol.verificar_pagamento(tx.sol_reference)
    except Exception as e:
        app.logger.warning(f'Solana verificar_pagamento: {e}')
        assinatura = None
    if not assinatura:
        return jsonify({'paid': False})
    # confirma: escrow on-chain
    tx.sol_payment_sig = assinatura
    tx.payment_status = 'escrow'
    tx.escrow_release_date = datetime.utcnow() + timedelta(days=app.config['ESCROW_RELEASE_DAYS'])
    listing = tx.listing_ref
    if listing and listing.status == 'active':
        listing.status = 'reserved'
    db.session.add(Notification(
        user_id=tx.seller_id, type='payment', title='Pagamento recebido on-chain!',
        content=f'O comprador pagou {tx.sol_amount_usdc} USDC via Solana. Combine a entrega.',
        link=f'/transaction/{tx.uid}'))
    db.session.commit()
    return jsonify({'paid': True, 'signature': assinatura, 'explorer': sol.link_explorer(assinatura)})


# ─── Mídia (imagens no banco) ───
@app.route('/media/<int:img_id>')
def media_image(img_id):
    from flask import Response
    img = ListingImage.query.get_or_404(img_id)
    if not img.data:
        return redirect('/static/img/no-image.svg')
    resp = Response(img.data, mimetype=img.mimetype or 'image/jpeg')
    resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp


# ─── Páginas institucionais ───
@app.route('/ajuda')
def page_ajuda():
    return render_template('pages/ajuda.html')

@app.route('/termos')
def page_termos():
    return render_template('pages/termos.html')

@app.route('/privacidade')
def page_privacidade():
    return render_template('pages/privacidade.html')

@app.route('/contato')
def page_contato():
    return render_template('pages/contato.html')


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


# ─── Auto-migration on startup ───
_AUTO_MIGRATE_STMTS = [
    "ALTER TABLE listings     ADD COLUMN IF NOT EXISTS venda_imediata      BOOLEAN      DEFAULT FALSE NOT NULL",
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS listing_id          INTEGER      REFERENCES listings(id)",
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS repasse_status      VARCHAR(20)  DEFAULT 'pendente'",
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS repasse_transfer_id VARCHAR(64)",
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS repasse_at          TIMESTAMP",
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS repasse_obs         VARCHAR(300)",
    "ALTER TABLE users        ADD COLUMN IF NOT EXISTS pix_key             VARCHAR(150)",
    "ALTER TABLE users        ADD COLUMN IF NOT EXISTS pix_key_type        VARCHAR(20)",
    "ALTER TABLE messages     ADD COLUMN IF NOT EXISTS listing_id          INTEGER      REFERENCES listings(id)",
    "ALTER TABLE listing_images ADD COLUMN IF NOT EXISTS data              BYTEA",
    "ALTER TABLE listing_images ADD COLUMN IF NOT EXISTS mimetype          VARCHAR(40)",
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS asaas_payment_id    VARCHAR(40)",
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS asaas_invoice_url   VARCHAR(300)",
    "ALTER TABLE users        ADD COLUMN IF NOT EXISTS solana_wallet       VARCHAR(64)",
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS sol_reference       VARCHAR(64)",
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS sol_amount_usdc     DOUBLE PRECISION",
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS sol_payment_sig     VARCHAR(120)",
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS sol_repasse_sig     VARCHAR(120)",
]

_DEFAULT_CATEGORIES = [
    ('Construção Civil', 'construcao-civil', '🧱'),
    ('Madeira', 'madeira', '🪵'),
    ('Metal', 'metal', '🔩'),
    ('Plástico', 'plastico', '♳'),
    ('Borracha', 'borracha', '⚫'),
    ('Eletrônicos', 'eletronicos', '💡'),
    ('Têxteis', 'texteis', '🧵'),
    ('Outros', 'outros', '📦'),
]
# categorias antigas -> nova (slug antigo: slug novo). Anúncios são remapeados e a antiga removida.
_CATEGORY_MERGE = {
    'metais-ferrosos': 'metal',
    'metais-nao-ferrosos': 'metal',
    'plasticos': 'plastico',
    'quimicos': 'outros',
}

def _run_auto_migrations():
    try:
        db.create_all()
        if not Category.query.first():
            for name, slug, icon in _DEFAULT_CATEGORIES:
                db.session.add(Category(name=name, slug=slug, icon=icon))
            db.session.commit()
            app.logger.info('Seed: categorias criadas.')
        else:
            changed = False
            for name, slug, icon in _DEFAULT_CATEGORIES:
                cat = Category.query.filter_by(slug=slug).first()
                if not cat:
                    db.session.add(Category(name=name, slug=slug, icon=icon))
                    changed = True
                elif cat.name != name:
                    cat.name = name
                    changed = True
            if changed:
                db.session.commit()
            for old_slug, new_slug in _CATEGORY_MERGE.items():
                old = Category.query.filter_by(slug=old_slug).first()
                new = Category.query.filter_by(slug=new_slug).first()
                if old and new and old.id != new.id:
                    n = Listing.query.filter_by(category_id=old.id).update({'category_id': new.id})
                    db.session.delete(old)
                    db.session.commit()
                    app.logger.info(f'Seed: categoria {old_slug} fundida em {new_slug} ({n} anúncios).')
        outros = Category.query.filter_by(slug='outros').first()
        if outros:
            sem_cat = Listing.query.filter_by(category_id=None).update({'category_id': outros.id})
            if sem_cat:
                db.session.commit()
                app.logger.info(f'Seed: {sem_cat} anúncios sem categoria movidos para Outros.')
    except Exception as e:
        db.session.rollback()
        app.logger.warning(f'Seed categorias warning: {e}')
    try:
        for stmt in _AUTO_MIGRATE_STMTS:
            db.session.execute(db.text(stmt))
        try:
            db.session.execute(db.text(
                "ALTER TABLE transactions ALTER COLUMN proposal_id DROP NOT NULL"
            ))
        except Exception:
            db.session.rollback()
        db.session.commit()
        app.logger.info('Auto-migration: OK')
    except Exception as e:
        db.session.rollback()
        app.logger.warning(f'Auto-migration warning: {e}')

with app.app_context():
    _run_auto_migrations()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
