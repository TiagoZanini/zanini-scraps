"""Atualiza a imagem do anúncio 77 Pilares no banco de dados."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from app import app, db
from modules.models import Listing, ListingImage

with app.app_context():
    listing = Listing.query.filter_by(title='Lote de Armações de Pilares CA-50 — Sobra de Produção').first()
    if not listing:
        print("Anúncio não encontrado.")
    else:
        # Remove imagens antigas
        ListingImage.query.filter_by(listing_id=listing.id).delete()
        # Adiciona imagem correta
        img = ListingImage(
            listing_id=listing.id,
            path='/static/uploads/listings/armacao_pilares.jpg',
            is_main=True,
            order=0
        )
        db.session.add(img)
        db.session.commit()
        print(f"✅ Imagem atualizada no anúncio: {listing.title}")
