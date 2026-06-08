"""
Adiciona 77 Pilares como usuário e cria anúncio de teste
com a imagem de armação de pilares.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app import app, db
from modules.models import User, Listing, ListingImage, Category
from datetime import datetime

def add_77pilares():
    with app.app_context():
        # Verificar se já existe
        existing = User.query.filter_by(email='contato@77pilares.com.br').first()
        if existing:
            print("Usuário 77 Pilares já existe.")
            user = existing
        else:
            user = User(
                name='77 Pilares Pré-Fabricados',
                email='contato@77pilares.com.br',
                role='fornecedor',
                document_type='cnpj',
                document='00.000.000/0001-77',  # placeholder
                phone='(00) 00000-0000',
                city='Ribeirão Preto',
                state='SP',
                street='Área Industrial',
                is_verified=True,
                plan='pro'
            )
            user.set_password('77pilares2026')
            db.session.add(user)
            db.session.flush()
            print(f"✅ Usuário criado: {user.name} (ID {user.id})")

        # Categoria construção civil
        cat = Category.query.filter_by(slug='construcao-civil').first()
        if not cat:
            cat = Category.query.filter_by(slug='metais-ferrosos').first()

        # Criar anúncio
        listing = Listing(
            seller_id=user.id,
            category_id=cat.id if cat else None,
            title='Lote de Armações de Pilares CA-50 — Sobra de Produção',
            description=(
                'Lote de gaiolas de armação para pilares estruturais, '
                'confeccionadas em aço CA-50 (vergalhão nervurado). '
                'Material proveniente de sobra de produção da linha de '
                'pré-fabricados de concreto.\n\n'
                'Especificações:\n'
                '- Aço CA-50, diâmetro 8mm e 10mm\n'
                '- Armações montadas (gaiolas prontas para concretagem)\n'
                '- Seção aproximada: 20x20cm\n'
                '- Comprimento médio: 80cm por gaiola\n'
                '- Quantidade: aproximadamente 30 a 50 unidades\n'
                '- Atadas com arame recozido\n\n'
                'Material com leve oxidação superficial (normal para aço CA-50 '
                'em estoque), sem comprometimento estrutural.\n\n'
                'Empresa: 77 Pilares Pré-Fabricados — especialista em '
                'estruturas pré-moldadas de concreto para segmentos '
                'industrial, comercial e residencial.'
            ),
            material_type='Aço CA-50',
            condition='seminovo',
            quantity=40,
            unit='un',
            price=3200.00,
            price_type='total',
            city='Ribeirão Preto',
            state='SP',
            delivery_type='retirada',
            origin_declaration=(
                'Sobra de produção da linha de pré-fabricados de concreto. '
                'Material interno, gerado no processo de armação de pilares. '
                'Origem lícita, sem impedimentos legais.'
            ),
            has_invoice=True,
            status='active',
            is_featured=True,
            views=1,
            favorites=0,
            created_at=datetime.utcnow()
        )
        db.session.add(listing)
        db.session.flush()

        # Imagem: copiar o arquivo de foto se existir, senão usar placeholder
        img_src = '/sessions/adoring-vigilant-ramanujan/mnt/uploads/armacao_pilares.jpg'
        img_dst_name = f'77pilares_armacao_{listing.id}.jpg'
        img_dst = f'/sessions/adoring-vigilant-ramanujan/zanini-scraps/static/uploads/listings/{img_dst_name}'

        if os.path.exists(img_src):
            import shutil
            shutil.copy(img_src, img_dst)
            img_path = f'/static/uploads/listings/{img_dst_name}'
            print(f"✅ Imagem copiada: {img_dst_name}")
        else:
            img_path = '/static/img/no-image.svg'
            print("⚠️  Imagem não encontrada — usando placeholder")

        db.session.add(ListingImage(
            listing_id=listing.id,
            path=img_path,
            is_main=True,
            order=0
        ))

        db.session.commit()

        print(f"\n✅ Anúncio criado com sucesso!")
        print(f"   Título: {listing.title}")
        print(f"   Vendedor: {user.name}")
        print(f"   Preço: R$ {listing.price:,.2f}")
        print(f"   URL: http://localhost:5000/listing/{listing.uid}")
        print(f"\n🔑 Login 77 Pilares:")
        print(f"   E-mail: contato@77pilares.com.br")
        print(f"   Senha: 77pilares2026")

if __name__ == '__main__':
    add_77pilares()
