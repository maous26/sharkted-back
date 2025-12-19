"""
Discord Service - OAuth et synchronisation des roles.

Fonctionnalites:
1. OAuth Discord pour lier les comptes utilisateurs
2. Synchronisation automatique des roles selon le plan
3. Envoi d'alertes filtrees par tier aux webhooks

Configuration requise (variables d'environnement):
- DISCORD_CLIENT_ID: ID de l'application Discord
- DISCORD_CLIENT_SECRET: Secret de l'application Discord
- DISCORD_BOT_TOKEN: Token du bot Discord
- DISCORD_GUILD_ID: ID du serveur Discord
- DISCORD_REDIRECT_URI: URL de callback OAuth
"""
import os
import httpx
from typing import Optional, Dict, List
from loguru import logger

from app.db.session import SessionLocal
from app.models.user import User
from app.models.discord_webhook import DiscordWebhook
from app.core.subscription_tiers import FREE_SOURCES, PREMIUM_SOURCES


# Configuration Discord
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "https://sharkted.fr/auth/discord/callback")

# Role IDs par tier (a configurer dans Discord)
DISCORD_ROLE_IDS = {
    "freemium": os.getenv("DISCORD_ROLE_FREEMIUM", ""),
    "basic": os.getenv("DISCORD_ROLE_BASIC", ""),
    "premium": os.getenv("DISCORD_ROLE_PREMIUM", ""),
    "admin": os.getenv("DISCORD_ROLE_ADMIN", ""),
}

# Discord API URLs
DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_OAUTH_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = f"{DISCORD_API_BASE}/oauth2/token"


def get_oauth_url(state: str) -> str:
    """Genere l'URL OAuth Discord pour lier un compte."""
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds.join",
        "state": state,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{DISCORD_OAUTH_URL}?{query}"


async def exchange_code_for_token(code: str) -> Optional[Dict]:
    """Echange le code OAuth contre un token d'acces."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                DISCORD_TOKEN_URL,
                data={
                    "client_id": DISCORD_CLIENT_ID,
                    "client_secret": DISCORD_CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": DISCORD_REDIRECT_URI,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.status_code == 200:
                return response.json()
            logger.error(f"Discord token exchange failed: {response.text}")
            return None
        except Exception as e:
            logger.error(f"Discord token exchange error: {e}")
            return None


async def get_discord_user(access_token: str) -> Optional[Dict]:
    """Recupere les infos de l'utilisateur Discord."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{DISCORD_API_BASE}/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code == 200:
                return response.json()
            logger.error(f"Discord user fetch failed: {response.text}")
            return None
        except Exception as e:
            logger.error(f"Discord user fetch error: {e}")
            return None


async def add_user_to_guild(access_token: str, discord_user_id: str) -> bool:
    """Ajoute l'utilisateur au serveur Discord avec le bot."""
    if not DISCORD_BOT_TOKEN or not DISCORD_GUILD_ID:
        logger.warning("Discord bot token or guild ID not configured")
        return False

    async with httpx.AsyncClient() as client:
        try:
            response = await client.put(
                f"{DISCORD_API_BASE}/guilds/{DISCORD_GUILD_ID}/members/{discord_user_id}",
                headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"},
                json={"access_token": access_token},
            )
            return response.status_code in (200, 201, 204)
        except Exception as e:
            logger.error(f"Discord add to guild error: {e}")
            return False


async def set_user_role(discord_user_id: str, tier: str) -> bool:
    """Attribue le role correspondant au tier de l'utilisateur."""
    if not DISCORD_BOT_TOKEN or not DISCORD_GUILD_ID:
        logger.warning("Discord bot token or guild ID not configured")
        return False

    role_id = DISCORD_ROLE_IDS.get(tier)
    if not role_id:
        logger.warning(f"No Discord role configured for tier: {tier}")
        return False

    async with httpx.AsyncClient() as client:
        try:
            # Retirer tous les roles de tier d'abord
            for t, rid in DISCORD_ROLE_IDS.items():
                if rid and t != tier:
                    await client.delete(
                        f"{DISCORD_API_BASE}/guilds/{DISCORD_GUILD_ID}/members/{discord_user_id}/roles/{rid}",
                        headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"},
                    )

            # Ajouter le nouveau role
            response = await client.put(
                f"{DISCORD_API_BASE}/guilds/{DISCORD_GUILD_ID}/members/{discord_user_id}/roles/{role_id}",
                headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"},
            )
            success = response.status_code in (200, 201, 204)
            if success:
                logger.info(f"Discord role {tier} set for user {discord_user_id}")
            return success
        except Exception as e:
            logger.error(f"Discord set role error: {e}")
            return False


async def link_discord_account(user_id: int, code: str) -> Dict:
    """
    Lie un compte Discord a un utilisateur Sharkted.

    1. Echange le code OAuth contre un token
    2. Recupere les infos Discord de l'utilisateur
    3. Sauvegarde le discord_id dans la DB
    4. Ajoute l'utilisateur au serveur Discord
    5. Attribue le role correspondant a son plan
    """
    # Echanger le code
    token_data = await exchange_code_for_token(code)
    if not token_data:
        return {"success": False, "error": "Failed to exchange OAuth code"}

    access_token = token_data.get("access_token")
    if not access_token:
        return {"success": False, "error": "No access token received"}

    # Recuperer les infos Discord
    discord_user = await get_discord_user(access_token)
    if not discord_user:
        return {"success": False, "error": "Failed to fetch Discord user info"}

    discord_id = discord_user.get("id")
    discord_username = f"{discord_user.get('username')}#{discord_user.get('discriminator', '0')}"

    # Sauvegarder dans la DB
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            return {"success": False, "error": "User not found"}

        # Verifier si ce Discord est deja lie a un autre compte
        existing = session.query(User).filter(
            User.discord_id == discord_id,
            User.id != user_id
        ).first()
        if existing:
            return {"success": False, "error": "This Discord account is already linked to another user"}

        user.discord_id = discord_id
        user.discord_username = discord_username
        session.commit()

        # Determiner le tier
        plan = (user.plan or "free").lower()
        if plan in ("premium", "pro", "agency", "owner"):
            tier = "premium"
        elif plan == "basic":
            tier = "basic"
        elif plan in ("admin",) or user.email == "admin@sharkted.fr":
            tier = "admin"
        else:
            tier = "freemium"

        # Ajouter au serveur et attribuer le role
        await add_user_to_guild(access_token, discord_id)
        await set_user_role(discord_id, tier)

        return {
            "success": True,
            "discord_id": discord_id,
            "discord_username": discord_username,
            "tier": tier,
        }
    finally:
        session.close()


async def sync_user_discord_role(user_id: int) -> bool:
    """
    Synchronise le role Discord d'un utilisateur avec son plan actuel.
    Appelee quand le plan d'un utilisateur change.
    """
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if not user or not user.discord_id:
            return False

        # Determiner le tier
        plan = (user.plan or "free").lower()
        if plan in ("premium", "pro", "agency", "owner"):
            tier = "premium"
        elif plan == "basic":
            tier = "basic"
        elif plan in ("admin",) or user.email == "admin@sharkted.fr":
            tier = "admin"
        else:
            tier = "freemium"

        return await set_user_role(user.discord_id, tier)
    finally:
        session.close()


async def unlink_discord_account(user_id: int) -> bool:
    """Delie le compte Discord d'un utilisateur."""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            return False

        user.discord_id = None
        user.discord_username = None
        session.commit()
        return True
    finally:
        session.close()


# =============================================================================
# ALERTES DISCORD FILTREES PAR TIER
# =============================================================================

TIER_SOURCE_ACCESS = {
    "freemium": FREE_SOURCES,
    "basic": FREE_SOURCES,
    "premium": FREE_SOURCES | PREMIUM_SOURCES,
    "admin": FREE_SOURCES | PREMIUM_SOURCES,
}


def can_tier_see_deal(tier: str, deal_source: str) -> bool:
    """Verifie si un tier peut voir un deal d'une source donnee."""
    allowed_sources = TIER_SOURCE_ACCESS.get(tier, set())
    return deal_source.lower() in allowed_sources


def get_tier_webhooks() -> Dict[str, Optional[str]]:
    """Recupere tous les webhooks par tier."""
    session = SessionLocal()
    try:
        webhooks = session.query(DiscordWebhook).filter(
            DiscordWebhook.enabled == True
        ).all()

        result = {"freemium": None, "basic": None, "premium": None, "admin": None}
        for wh in webhooks:
            if wh.webhook_url:
                result[wh.tier] = wh.webhook_url
        return result
    finally:
        session.close()


def get_webhook_settings(tier: str) -> Dict:
    """Recupere les parametres du webhook pour un tier."""
    session = SessionLocal()
    try:
        webhook = session.query(DiscordWebhook).filter(
            DiscordWebhook.tier == tier
        ).first()

        if webhook:
            return {
                "min_score": webhook.min_score or 70,
                "send_after_scan": webhook.send_after_scan,
            }
        return {"min_score": 70, "send_after_scan": True}
    finally:
        session.close()


def get_score_color(score: float) -> int:
    """Retourne la couleur Discord selon le score."""
    if score >= 80:
        return 0x22c55e  # Green
    elif score >= 70:
        return 0x3b82f6  # Blue
    elif score >= 60:
        return 0xf59e0b  # Orange
    return 0xef4444  # Red


async def send_deal_alert(deal_data: Dict) -> Dict[str, int]:
    """
    Envoie une alerte de deal aux webhooks Discord selon les tiers.

    deal_data doit contenir:
    - title, url, price, source, brand, image_url
    - score (flip_score)
    - margin_euro, margin_pct (optionnel)
    - sizes_available (optionnel)
    """
    webhooks = get_tier_webhooks()
    sent_counts = {"freemium": 0, "basic": 0, "premium": 0, "admin": 0}

    deal_source = deal_data.get("source", "").lower()
    flip_score = deal_data.get("score", 0)

    for tier, webhook_url in webhooks.items():
        if not webhook_url:
            continue

        # Filtrage par source
        if not can_tier_see_deal(tier, deal_source):
            logger.debug(f"Tier {tier} cannot see deal from {deal_source}")
            continue

        # Filtrage par score minimum
        settings = get_webhook_settings(tier)
        if flip_score < settings["min_score"]:
            continue

        # Construire l'embed
        is_premium_source = deal_source in PREMIUM_SOURCES
        source_badge = "PREMIUM" if is_premium_source else "FREE"

        margin_text = "N/A"
        if deal_data.get("margin_euro"):
            margin_text = f"+{deal_data['margin_euro']:.0f}E ({deal_data.get('margin_pct', 0):.0f}%)"

        embed = {
            "title": deal_data.get("title", "Deal")[:80],
            "url": deal_data.get("url"),
            "color": get_score_color(flip_score),
            "thumbnail": {"url": deal_data.get("image_url")} if deal_data.get("image_url") else None,
            "fields": [
                {"name": "Prix", "value": f"**{deal_data.get('price', 0):.2f}E**", "inline": True},
                {"name": "Marge", "value": margin_text, "inline": True},
                {"name": "SharkScore", "value": f"**{flip_score}**/100", "inline": True},
                {"name": "Marque", "value": deal_data.get("brand", "N/A"), "inline": True},
                {"name": "Source", "value": f"{deal_source.capitalize()} {source_badge}", "inline": True},
            ],
            "footer": {"text": f"Sharkted | {tier.upper()}"},
        }

        # Envoyer
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(webhook_url, json={"embeds": [embed]})
                if response.status_code == 204:
                    sent_counts[tier] += 1
                    logger.info(f"Alert sent to {tier}: {deal_data.get('title', '')[:30]}")
        except Exception as e:
            logger.error(f"Discord webhook error for {tier}: {e}")

    return sent_counts
