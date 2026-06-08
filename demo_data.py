"""
Zanini Scraps - Dados de Demonstração
Popula o banco com dados realistas para testes e apresentações.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app import app, db
from modules.models import User, Category, Listing, ListingImage, Proposal, Message, Transaction, Notification, Favorite
from datetime import datetime, timedelta
import random

def create_demo_data():
    with app.app_context():
        # Limpar dados existentes
        db.drop_all()
        db.create_all()

        print("📦 Criando categorias...")
        categories = [
            Category(name='Metais Ferrosos', slug='metais-ferrosos', icon='🔩'),
            Category(name='Metais Não-Ferrosos', slug='metais-nao-ferrosos', icon='🥇'),
            Category(name='Plásticos', slug='plasticos', icon='♳'),
            Category(name='Madeira', slug='madeira', icon='🪵'),
            Category(name='Construção Civil', slug='construcao-civil', icon='🧱'),
            Category(name='Eletrônicos', slug='eletronicos', icon='💻'),
            Category(name='Papel e Papelão', slug='papel-papelao', icon='📦'),
            Category(name='Têxtil', slug='textil', icon='🧵'),
            Category(name='Vidro', slug='vidro', icon='🪟'),
            Category(name='Químicos', slug='quimicos', icon='🧪'),
            Category(name='Borracha', slug='borracha', icon='⚫'),
            Category(name='Outros', slug='outros', icon='📋'),
        ]
        db.session.add_all(categories)
        db.session.flush()

        print("👥 Criando usuários...")
        admin = User(
            name='Zanini Admin',
            email='admin@zaniniscraps.com',
            role='admin',
            document_type='cnpj',
            document='12.345.678/0001-00',
            phone='(51) 99999-0000',
            city='Porto Alegre',
            state='RS',
            is_verified=True
        )
        admin.set_password('admin123')

        sellers = []
        seller_data = [
            ('MetalSul Reciclagem LTDA', 'metalsul@email.com', '11.111.111/0001-01', 'Porto Alegre', 'RS', '(51) 98888-1111'),
            ('AçoBras Indústria S.A.', 'acobras@email.com', '22.222.222/0001-02', 'São Paulo', 'SP', '(11) 97777-2222'),
            ('PlastiNorte Comércio', 'plastinorte@email.com', '33.333.333/0001-03', 'Manaus', 'AM', '(92) 96666-3333'),
            ('MadeiraViva Sustentável', 'madeiraviva@email.com', '44.444.444/0001-04', 'Curitiba', 'PR', '(41) 95555-4444'),
            ('ConstruMax Materiais', 'construmax@email.com', '55.555.555/0001-05', 'Belo Horizonte', 'MG', '(31) 94444-5555'),
            ('TecnoScrap Industrial', 'tecnoscrap@email.com', '66.666.666/0001-06', 'Joinville', 'SC', '(47) 93333-6666'),
            ('ReciclaRio Comércio', 'reciclario@email.com', '77.777.777/0001-07', 'Rio de Janeiro', 'RJ', '(21) 92222-7777'),
            ('EcoMetal Nordeste', 'ecometal@email.com', '88.888.888/0001-08', 'Recife', 'PE', '(81) 91111-8888'),
        ]
        for name, email, doc, city, state, phone in seller_data:
            u = User(name=name, email=email, role='fornecedor', document_type='cnpj',
                     document=doc, phone=phone, city=city, state=state, is_verified=True)
            u.set_password('123456')
            sellers.append(u)

        buyers = []
        buyer_data = [
            ('Indústria Gaúcha de Peças', 'igp@email.com', '91.111.111/0001-09', 'Caxias do Sul', 'RS', '(54) 99000-1111'),
            ('Construtora Paulista LTDA', 'cpaulista@email.com', '92.222.222/0001-10', 'Campinas', 'SP', '(19) 99000-2222'),
            ('AutoPeças Centro-Oeste', 'autocentro@email.com', '93.333.333/0001-11', 'Goiânia', 'GO', '(62) 99000-3333'),
            ('Reciclagem Nacional S.A.', 'recinacional@email.com', '94.444.444/0001-12', 'Salvador', 'BA', '(71) 99000-4444'),
            ('FabMetal Sudeste', 'fabmetal@email.com', '95.555.555/0001-13', 'Vitória', 'ES', '(27) 99000-5555'),
            ('João da Silva MEI', 'joao@email.com', '123.456.789-00', 'Florianópolis', 'SC', '(48) 99000-6666'),
        ]
        for name, email, doc, city, state, phone in buyer_data:
            dtype = 'cpf' if len(doc) <= 14 else 'cnpj'
            u = User(name=name, email=email, role='comprador', document_type=dtype,
                     document=doc, phone=phone, city=city, state=state, is_verified=True)
            u.set_password('123456')
            buyers.append(u)

        db.session.add(admin)
        db.session.add_all(sellers)
        db.session.add_all(buyers)
        db.session.flush()

        print("📋 Criando anúncios...")
        listings_data = [
            # (seller_idx, cat_idx, title, material, condition, qty, unit, price, price_type, city, state, featured, description)
            (0, 0, 'Lote de Chapas de Aço Inox 304 - 2mm', 'Aço Inox 304', 'seminovo', 5000, 'kg', 45000, 'total', 'Porto Alegre', 'RS', True,
             'Chapas de aço inoxidável 304, espessura 2mm, sobra de produção de tanques industriais. Material de alta qualidade, sem oxidação. Dimensões variadas de 1m x 2m a 1.5m x 3m. Nota fiscal de origem disponível.'),
            (1, 0, 'Tubos de Aço Carbono SCH40 - 6"', 'Aço Carbono', 'usado', 2000, 'kg', 18000, 'total', 'São Paulo', 'SP', True,
             'Tubos de aço carbono schedule 40, diâmetro 6 polegadas, comprimentos de 3m a 6m. Remanescente de obra industrial. Sem amassados significativos.'),
            (0, 1, 'Alumínio Liga 6061-T6 Barras Chatas', 'Alumínio 6061', 'novo', 800, 'kg', 32000, 'total', 'Porto Alegre', 'RS', True,
             'Barras chatas de alumínio liga 6061-T6, diversas dimensões. Material novo, sobra de corte CNC. Ideal para usinagem e estruturas leves.'),
            (2, 2, 'Lote de PEAD Granulado - Branco Virgem', 'PEAD', 'novo', 10000, 'kg', 65000, 'total', 'Manaus', 'AM', True,
             'Polietileno de alta densidade granulado, cor branca, material virgem. Sobra de produção de embalagens. Qualidade premium.'),
            (3, 3, 'Vigas de Eucalipto Tratado 15x15cm', 'Eucalipto Tratado', 'seminovo', 200, 'un', 14000, 'total', 'Curitiba', 'PR', False,
             'Vigas de eucalipto autoclavado, seção 15x15cm, comprimentos de 3m a 4m. Remanescente de obra de galpão. Tratamento CCA.'),
            (4, 4, 'Tijolos Cerâmicos 6 Furos - Pallets', 'Cerâmica', 'novo', 15000, 'un', 9000, 'total', 'Belo Horizonte', 'MG', False,
             'Tijolos cerâmicos 6 furos, dimensão 9x14x19cm. Material novo, sobra de obra. 15 pallets disponíveis.'),
            (5, 0, 'Chapas de Cobre Eletrolítico 3mm', 'Cobre Eletrolítico', 'seminovo', 500, 'kg', 85000, 'total', 'Joinville', 'SC', True,
             'Chapas de cobre eletrolítico, espessura 3mm. Remanescente de fabricação de painéis elétricos. Material de altíssima pureza (99.9%).'),
            (1, 0, 'Perfis Estruturais I 200x200 - Aço A36', 'Aço A36', 'usado', 8000, 'kg', 52000, 'total', 'São Paulo', 'SP', False,
             'Perfis I de aço ASTM A36, seção 200x200mm, diversos comprimentos. Desmontagem de estrutura metálica de galpão industrial.'),
            (6, 2, 'Polipropileno Reciclado PP - Preto', 'Polipropileno', 'usado', 5000, 'kg', 12500, 'total', 'Rio de Janeiro', 'RJ', False,
             'Polipropileno reciclado de alta qualidade, cor preta, granulado. Ideal para injeção de peças não-aparentes.'),
            (7, 1, 'Fio de Cobre Esmaltado AWG 18', 'Cobre Esmaltado', 'novo', 1200, 'kg', 96000, 'total', 'Recife', 'PE', True,
             'Fio de cobre esmaltado AWG 18, em bobinas de 25kg. Sobra de produção de transformadores. Material novo, com certificado.'),
            (0, 0, 'Cantoneiras de Aço Galvanizado 50x50x5mm', 'Aço Galvanizado', 'novo', 3000, 'kg', 21000, 'total', 'Canoas', 'RS', False,
             'Cantoneiras de aço galvanizado a quente, dimensão 50x50x5mm, barras de 6m. Sobra de fabricação de torres.'),
            (3, 3, 'Compensado Naval 18mm - Chapas Inteiras', 'Compensado Naval', 'novo', 100, 'un', 8500, 'total', 'Curitiba', 'PR', False,
             'Chapas de compensado naval 18mm, dimensão 2.20m x 1.60m. Material novo, sobra de projeto náutico.'),
            (2, 2, 'PVC Rígido Tubos Brancos - Sobra Industrial', 'PVC Rígido', 'novo', 2000, 'kg', 8000, 'total', 'Manaus', 'AM', False,
             'Tubos e conexões de PVC rígido, cor branca, diversas medidas. Sobra de produção. Material de primeira linha.'),
            (4, 4, 'Vergalhões CA-50 10mm - 12m', 'Aço CA-50', 'novo', 6000, 'kg', 30000, 'total', 'Betim', 'MG', False,
             'Vergalhões de aço CA-50, diâmetro 10mm, barras de 12m. Sobra de obra de fundação. Material com laudo técnico.'),
            (5, 5, 'Placas de Circuito Impresso - Sucata Eletrônica', 'PCB', 'sucata', 500, 'kg', 25000, 'total', 'Joinville', 'SC', False,
             'Placas de circuito impresso de equipamentos industriais. Contém metais preciosos (ouro, prata, paládio). Ideal para recuperação.'),
            (1, 0, 'Aço Inox 316L - Sobras de Corte Laser', 'Aço Inox 316L', 'novo', 1500, 'kg', 67500, 'total', 'Guarulhos', 'SP', True,
             'Sobras de corte laser em aço inox 316L. Peças irregulares de 5kg a 50kg. Material novo, sem contaminação.'),
            (6, 6, 'Caixas de Papelão Ondulado - Grandes', 'Papelão Ondulado', 'usado', 3000, 'kg', 4500, 'total', 'Rio de Janeiro', 'RJ', False,
             'Caixas de papelão ondulado duplo, diversas dimensões. Material limpo e seco, pronto para reciclagem.'),
            (7, 0, 'Latão Amarelo - Recortes e Barras', 'Latão', 'seminovo', 800, 'kg', 40000, 'total', 'Recife', 'PE', False,
             'Recortes e barras de latão amarelo (Cu65/Zn35). Sobra de fabricação de válvulas industriais. Alta qualidade.'),
            (0, 0, 'Ferro Fundido Cinzento - Peças Diversas', 'Ferro Fundido', 'usado', 4000, 'kg', 16000, 'total', 'Gravataí', 'RS', False,
             'Peças de ferro fundido cinzento, diversas formas e pesos. Remanescente de renovação de maquinário industrial.'),
            (3, 3, 'Pallets de Madeira PBR - Usados', 'Madeira Pinus', 'usado', 500, 'un', 7500, 'total', 'Londrina', 'PR', False,
             'Pallets padrão PBR (1.20m x 1.00m), em bom estado de conservação. Ideal para logística ou projetos de reuso.'),
        ]

        all_listings = []
        for data in listings_data:
            s_idx, c_idx, title, mat, cond, qty, unit, price, ptype, city, state, feat, desc = data
            l = Listing(
                seller_id=sellers[s_idx].id,
                category_id=categories[c_idx].id,
                title=title,
                material_type=mat,
                condition=cond,
                quantity=qty,
                unit=unit,
                price=price,
                price_type=ptype,
                city=city,
                state=state,
                is_featured=feat,
                description=desc,
                delivery_type=random.choice(['retirada', 'entrega', 'ambos']),
                origin_declaration='Material de origem lícita, proveniente de processo industrial regular.',
                has_invoice=random.choice([True, True, False]),
                views=random.randint(10, 500),
                favorites=random.randint(0, 30),
                status='active',
                created_at=datetime.utcnow() - timedelta(days=random.randint(0, 30))
            )
            all_listings.append(l)
            db.session.add(l)
        db.session.flush()

        print("🤝 Criando propostas...")
        proposals = []
        for i in range(15):
            listing = random.choice(all_listings)
            buyer = random.choice(buyers)
            discount = random.uniform(0.85, 1.05)
            amount = round(listing.price * discount, 2)
            status = random.choice(['pending', 'pending', 'accepted', 'rejected', 'countered'])

            p = Proposal(
                listing_id=listing.id,
                buyer_id=buyer.id,
                seller_id=listing.seller_id,
                amount=amount,
                quantity=listing.quantity,
                message=random.choice([
                    'Tenho interesse no material. Posso retirar esta semana.',
                    'Gostaria de negociar o preço para compra do lote completo.',
                    'Material atende nossas especificações. Quando posso buscar?',
                    'Preciso de laudo técnico antes de fechar. Disponível?',
                    'Compramos acima de 5 toneladas com frequência. Parceria?',
                ]),
                status=status,
                created_at=datetime.utcnow() - timedelta(days=random.randint(0, 15))
            )
            if status == 'countered':
                p.counter_amount = round(listing.price * random.uniform(0.92, 1.0), 2)
                p.counter_message = 'Podemos fechar nesse valor. O que acha?'
            proposals.append(p)
            db.session.add(p)
        db.session.flush()

        print("💬 Criando mensagens...")
        for p in proposals[:8]:
            for j in range(random.randint(1, 4)):
                sender = p.buyer_id if j % 2 == 0 else p.seller_id
                receiver = p.seller_id if j % 2 == 0 else p.buyer_id
                msg = Message(
                    sender_id=sender,
                    receiver_id=receiver,
                    proposal_id=p.id,
                    content=random.choice([
                        'Olá, tenho interesse no material.',
                        'Podemos negociar o preço?',
                        'Quando podemos agendar a retirada?',
                        'O material está disponível para visita?',
                        'Aceito sua proposta. Vamos fechar!',
                        'Preciso de mais informações sobre a qualidade.',
                        'Posso enviar transporte na próxima semana.',
                    ]),
                    created_at=datetime.utcnow() - timedelta(hours=random.randint(1, 200))
                )
                db.session.add(msg)

        print("💰 Criando transações...")
        accepted = [p for p in proposals if p.status == 'accepted']
        for p in accepted:
            commission = round(p.amount * 0.05, 2)
            tx = Transaction(
                proposal_id=p.id,
                buyer_id=p.buyer_id,
                seller_id=p.seller_id,
                gross_amount=p.amount,
                commission=commission,
                net_amount=round(p.amount - commission, 2),
                payment_method=random.choice(['pix', 'cartao']),
                payment_status=random.choice(['escrow', 'released', 'released']),
                logistics_status=random.choice(['scheduled', 'delivered', 'confirmed']),
                pickup_date=datetime.utcnow() + timedelta(days=random.randint(1, 7)),
                created_at=p.created_at + timedelta(hours=random.randint(1, 48))
            )
            if tx.payment_status == 'released':
                tx.delivery_date = tx.created_at + timedelta(days=random.randint(2, 5))
            db.session.add(tx)

        print("🔔 Criando notificações...")
        for p in proposals[:5]:
            db.session.add(Notification(
                user_id=p.seller_id,
                type='proposal',
                title='Nova proposta recebida',
                content=f'Proposta de R$ {p.amount:,.2f} recebida',
                is_read=random.choice([True, False]),
                created_at=p.created_at
            ))

        print("❤ Criando favoritos...")
        for buyer in buyers:
            for _ in range(random.randint(1, 5)):
                listing = random.choice(all_listings)
                existing = Favorite.query.filter_by(user_id=buyer.id, listing_id=listing.id).first()
                if not existing:
                    db.session.add(Favorite(user_id=buyer.id, listing_id=listing.id))

        db.session.commit()

        # Stats
        print("\n" + "="*50)
        print("✅ DADOS DEMO CRIADOS COM SUCESSO!")
        print("="*50)
        print(f"👤 Usuários: {User.query.count()} (1 admin + {len(sellers)} fornecedores + {len(buyers)} compradores)")
        print(f"📂 Categorias: {Category.query.count()}")
        print(f"📋 Anúncios: {Listing.query.count()} ({Listing.query.filter_by(is_featured=True).count()} destaques)")
        print(f"🤝 Propostas: {Proposal.query.count()}")
        print(f"💬 Mensagens: {Message.query.count()}")
        print(f"💰 Transações: {Transaction.query.count()}")
        print(f"🔔 Notificações: {Notification.query.count()}")
        print(f"❤ Favoritos: {Favorite.query.count()}")
        print()
        print("🔑 LOGINS DISPONÍVEIS:")
        print("─" * 40)
        print("  Admin:      admin@zaniniscraps.com / admin123")
        print("  Fornecedor: metalsul@email.com / 123456")
        print("  Comprador:  igp@email.com / 123456")
        print()
        print("🌐 Acesse: http://localhost:5000")
        print()
        print("Desenvolvido por Grupo Zanini S.A.")
        print("Direitos reservados à Zanini Business")
        print("Inventor Tiago Lourenço Zanini")


if __name__ == '__main__':
    create_demo_data()
