"""
Script de inicialização do banco de dados para produção.
Rode UMA VEZ após o deploy: python init_db.py
Cria as tabelas e o usuário admin.
"""
import os
from app import app, db
from modules.models import User, Category

ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'admin@zaniniscraps.com.br')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'TroqueEssaSenha2026!')
ADMIN_NAME = os.environ.get('ADMIN_NAME', 'Zanini Admin')

CATEGORIES = [
    'Metais Ferrosos', 'Metais Não-Ferrosos', 'Plásticos', 'Madeira',
    'Construção Civil', 'Eletrônicos', 'Têxteis', 'Químicos', 'Outros'
]

with app.app_context():
    db.create_all()
    print('✅ Tabelas criadas.')

    if not User.query.filter_by(email=ADMIN_EMAIL).first():
        admin = User(name=ADMIN_NAME, email=ADMIN_EMAIL, role='admin',
                     document_type='cnpj', document='00.000.000/0001-00',
                     city='Porto Alegre', state='RS')
        admin.set_password(ADMIN_PASSWORD)
        db.session.add(admin)
        db.session.commit()
        print(f'✅ Admin criado: {ADMIN_EMAIL}')
    else:
        print('ℹ️  Admin já existe.')

    for name in CATEGORIES:
        slug = name.lower().replace(' ', '-').replace('ã', 'a').replace('é', 'e').replace('-', '_')
        if not Category.query.filter_by(name=name).first():
            db.session.add(Category(name=name, slug=slug))
    db.session.commit()
    print('✅ Categorias criadas.')
    print('\nPronto! Sistema inicializado.')
