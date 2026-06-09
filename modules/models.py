"""
Models do Zanini Scraps Marketplace
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import uuid

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(36), unique=True, default=lambda: str(uuid.uuid4()))
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='fornecedor')  # fornecedor, comprador, admin
    is_verified = db.Column(db.Boolean, default=False)
    is_active_user = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Dados pessoais/empresa
    name = db.Column(db.String(200), nullable=False)
    document_type = db.Column(db.String(10), default='cnpj')  # cpf ou cnpj
    document = db.Column(db.String(18), unique=True)
    phone = db.Column(db.String(20))
    avatar = db.Column(db.String(255))

    # Endereço
    cep = db.Column(db.String(10))
    street = db.Column(db.String(200))
    number = db.Column(db.String(20))
    complement = db.Column(db.String(100))
    neighborhood = db.Column(db.String(100))
    city = db.Column(db.String(100))
    state = db.Column(db.String(2))
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)

    # Plano
    plan = db.Column(db.String(20), default='free')  # free, pro, enterprise
    plan_expires = db.Column(db.DateTime)

    # Recebimento
    pix_key      = db.Column(db.String(150))   # chave PIX para repasse automático
    pix_key_type = db.Column(db.String(20))    # cpf, cnpj, email, telefone, aleatoria

    # Relationships
    listings = db.relationship('Listing', backref='seller', lazy='dynamic', foreign_keys='Listing.seller_id')
    proposals_sent = db.relationship('Proposal', backref='buyer', lazy='dynamic', foreign_keys='Proposal.buyer_id')
    proposals_received = db.relationship('Proposal', backref='seller_user', lazy='dynamic', foreign_keys='Proposal.seller_id')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def display_name(self):
        return self.name.split()[0] if self.name else self.email.split('@')[0]

    @property
    def is_seller(self):
        return self.role in ('fornecedor', 'admin')

    @property
    def is_buyer(self):
        return self.role in ('comprador', 'admin')

    @property
    def is_admin(self):
        return self.role == 'admin'


class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    icon = db.Column(db.String(50))
    listings = db.relationship('Listing', backref='category', lazy='dynamic')


class Listing(db.Model):
    __tablename__ = 'listings'
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(36), unique=True, default=lambda: str(uuid.uuid4()))
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), index=True)

    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    material_type = db.Column(db.String(100))  # Aço, Alumínio, Madeira, Plástico, etc.
    condition = db.Column(db.String(50))  # novo, seminovo, usado, sucata
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), default='kg')  # kg, ton, un, m², m³
    price = db.Column(db.Float, nullable=False)
    price_type = db.Column(db.String(20), default='total')  # total, por_unidade
    min_order = db.Column(db.Float, default=0)

    # Localização
    city = db.Column(db.String(100))
    state = db.Column(db.String(2))
    cep = db.Column(db.String(10))
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)

    # Logística
    delivery_type = db.Column(db.String(20), default='retirada')  # retirada, entrega, ambos
    freight_included = db.Column(db.Boolean, default=False)

    # Status
    status = db.Column(db.String(20), default='active', index=True)  # draft, active, reserved, sold, expired
    is_featured = db.Column(db.Boolean, default=False)
    views = db.Column(db.Integer, default=0)
    favorites = db.Column(db.Integer, default=0)

    # Compliance e confiança comercial
    origin_declaration = db.Column(db.Text)
    has_invoice        = db.Column(db.Boolean, default=False)
    availability       = db.Column(db.String(200))  # Ex: "Disponível a partir de 15/07"
    observations       = db.Column(db.Text)          # Observações adicionais para o comprador
    who_picks_up       = db.Column(db.String(100), default='comprador')  # comprador, vendedor, negociavel

    # Venda Imediata
    venda_imediata     = db.Column(db.Boolean, default=False)

    # Datas
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = db.Column(db.DateTime, default=lambda: datetime.utcnow() + timedelta(days=30))

    # Relationships
    images = db.relationship('ListingImage', backref='listing', lazy='dynamic', cascade='all, delete-orphan')
    proposals = db.relationship('Proposal', backref='listing', lazy='dynamic', cascade='all, delete-orphan')

    @property
    def main_image(self):
        img = self.images.filter_by(is_main=True).first()
        if not img:
            img = self.images.first()
        return img.path if img else '/static/img/no-image.svg'

    @property
    def total_value(self):
        if self.price_type == 'por_unidade':
            return self.price * self.quantity
        return self.price

    @property
    def commission_value(self):
        from flask import current_app
        return self.total_value * current_app.config.get('COMMISSION_RATE', 0.05)


class ListingImage(db.Model):
    __tablename__ = 'listing_images'
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey('listings.id'), nullable=False)
    path = db.Column(db.String(255), nullable=False)
    is_main = db.Column(db.Boolean, default=False)
    order = db.Column(db.Integer, default=0)


class Proposal(db.Model):
    __tablename__ = 'proposals'
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(36), unique=True, default=lambda: str(uuid.uuid4()))
    listing_id = db.Column(db.Integer, db.ForeignKey('listings.id'), nullable=False, index=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    amount = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.Float)
    message = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending', index=True)  # pending, accepted, rejected, countered, expired, cancelled
    counter_amount = db.Column(db.Float)
    counter_message = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = db.Column(db.DateTime, default=lambda: datetime.utcnow() + timedelta(days=3))

    # Payment link
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id'))


class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    proposal_id = db.Column(db.Integer, db.ForeignKey('proposals.id'), nullable=True)
    content = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    sender = db.relationship('User', foreign_keys=[sender_id], backref='messages_sent')
    receiver = db.relationship('User', foreign_keys=[receiver_id], backref='messages_received')


class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(36), unique=True, default=lambda: str(uuid.uuid4()))
    proposal_id = db.Column(db.Integer, db.ForeignKey('proposals.id'), nullable=True)
    listing_id  = db.Column(db.Integer, db.ForeignKey('listings.id'),  nullable=True)   # venda imediata
    buyer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    gross_amount = db.Column(db.Float, nullable=False)
    commission = db.Column(db.Float, nullable=False)
    net_amount = db.Column(db.Float, nullable=False)
    freight_cost = db.Column(db.Float, default=0)

    payment_method = db.Column(db.String(20))  # pix, boleto, cartao
    payment_status = db.Column(db.String(20), default='pending')  # pending, awaiting_payment, escrow, released, refunded, disputed
    escrow_release_date = db.Column(db.DateTime)

    # Mercado Pago
    mp_payment_id  = db.Column(db.String(64))
    mp_qr_code     = db.Column(db.Text)       # PIX copia-e-cola
    mp_qr_base64   = db.Column(db.Text)       # imagem base64 do QR Code
    mp_boleto_url  = db.Column(db.String(500))
    mp_barcode     = db.Column(db.String(200))

    # Logística
    logistics_status = db.Column(db.String(20), default='pending')  # pending, scheduled, in_transit, delivered, confirmed
    pickup_date = db.Column(db.DateTime)
    delivery_date = db.Column(db.DateTime)

    # Repasse ao vendedor
    repasse_status     = db.Column(db.String(20), default='pendente')  # pendente, enviado, falhou, manual
    repasse_transfer_id = db.Column(db.String(64))
    repasse_at         = db.Column(db.DateTime)
    repasse_obs        = db.Column(db.String(300))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    buyer = db.relationship('User', foreign_keys=[buyer_id])
    seller = db.relationship('User', foreign_keys=[seller_id])
    proposal = db.relationship('Proposal', foreign_keys=[proposal_id], backref='transaction_ref')


class Favorite(db.Model):
    __tablename__ = 'favorites'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    listing_id = db.Column(db.Integer, db.ForeignKey('listings.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'listing_id'),)


class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    type = db.Column(db.String(50))  # proposal, message, transaction, system
    title = db.Column(db.String(200))
    content = db.Column(db.Text)
    link = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='notifications')
