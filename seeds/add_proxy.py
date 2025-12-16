
"""
Script de seed pour ajouter/mettre à jour le proxy Bright Data Web Unlocker.
Lancer avec: python seeds/add_proxy.py
"""
import sys
import os

# Ajouter le chemin racine au PYTHONPATH
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import SessionLocal
from app.models.proxy_settings import ProxySettings
from loguru import logger

def seed_web_unlocker():
    db = SessionLocal()
    try:
        # Configuration fournie
        HOST = "brd.superproxy.io"
        PORT = 33335
        USER = "brd-customer-hl_cb216abc-zone-web_unlocker1"
        PASS = "f3builbiy0xl"
        NAME = "BrightData Web Unlocker"
        
        logger.info(f"Checking for existing proxy: {NAME}")
        
        existing = db.query(ProxySettings).filter(ProxySettings.name == NAME).first()
        
        if existing:
            logger.info("Updating existing proxy...")
            existing.host = HOST
            existing.port = PORT
            existing.username = USER
            existing.password = PASS
            existing.proxy_type = "web_unlocker"
            existing.is_default = True
            existing.enabled = True
        else:
            logger.info("Creating new proxy...")
            proxy = ProxySettings(
                name=NAME,
                provider="brightdata",
                proxy_type="web_unlocker",
                host=HOST,
                port=PORT,
                username=USER,
                password=PASS,
                is_default=True,
                enabled=True
            )
            db.add(proxy)
            
        db.commit()
        logger.success("Web Unlocker proxy configured successfully!")
        
    except Exception as e:
        logger.error(f"Error checking/adding proxy: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_web_unlocker()
