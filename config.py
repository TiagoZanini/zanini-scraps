"""
Configuração do Zanini Scraps Marketplace
"""
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

def _db_url():
    url = os.environ.get('DATABASE_URL', '')
    # Railway usa postgres://. Força o driver psycopg2 (instalado): o SQLAlchemy 2.1+ passou a usar psycopg 3 por padrão.
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql+psycopg2://', 1)
    elif url.startswith('postgresql://'):
        url = url.replace('postgresql://', 'postgresql+psycopg2://', 1)
    return url or f'sqlite:///{os.path.join(BASE_DIR, "data", "zanini_scraps.db")}'

_IS_PROD = bool(os.environ.get('DATABASE_URL'))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', '')
    if not SECRET_KEY:
        if _IS_PROD:
            raise RuntimeError('SECRET_KEY não configurada no ambiente de produção.')
        SECRET_KEY = 'zanini-scraps-dev-key-2026'  # apenas dev local com SQLite
    SQLALCHEMY_DATABASE_URI = _db_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Cookies de sessão
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = _IS_PROD
    REMEMBER_COOKIE_SECURE = _IS_PROD
    REMEMBER_COOKIE_HTTPONLY = True

    # Cron de liberação de escrow (token dedicado; se ausente, rota desabilitada)
    CRON_TOKEN = os.environ.get('CRON_TOKEN', '')

    # Upload
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

    # Marketplace
    COMMISSION_RATE = 0.05  # 5% take rate
    ESCROW_RELEASE_DAYS = 2  # D+2
    PLATFORM_NAME = 'Zanini Scraps'

    # Logística
    FREIGHT_RATE_PER_KM = 2.30  # R$/km base

    # Paginação
    ITEMS_PER_PAGE = 12

    # Mercado Pago
    MP_ACCESS_TOKEN  = os.environ.get('MP_ACCESS_TOKEN', '')   # ak_live_... ou ak_test_...
    MP_WEBHOOK_URL   = os.environ.get('MP_WEBHOOK_URL', '')    # https://seudominio.com.br/mp-webhook
    MP_WEBHOOK_SECRET = os.environ.get('MP_WEBHOOK_SECRET', '')

    # E-mail (configure com seus dados SMTP)
    MAIL_SERVER   = os.environ.get('MAIL_SERVER',   'smtp.gmail.com')
    MAIL_PORT     = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS  = True
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')   # seu e-mail
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')   # senha de app
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@zaniniscraps.com.br')
    RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')       # envio por API HTTPS (Railway bloqueia SMTP)
    MAIL_ENABLED  = bool(RESEND_API_KEY or os.environ.get('MAIL_USERNAME', ''))
