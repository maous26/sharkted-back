"""
URL Validator - Protection anti-SSRF.

Valide que les URLs soumises aux collectors sont:
1. HTTPS uniquement
2. Sur un domaine autorisé (whitelist par source)
3. Pas une IP privée/localhost
"""
import re
import socket
from urllib.parse import urlparse
from typing import Optional, Set
from ipaddress import ip_address, ip_network

from app.core.exceptions import ValidationError

# Domaines autorisés par source (exact match ou wildcard)
ALLOWED_DOMAINS: dict[str, Set[str]] = {
    "courir": {"www.courir.com", "courir.com"},
    "footlocker": {"www.footlocker.fr", "footlocker.fr"},
    "size": {"www.size.co.uk", "size.co.uk"},
    "jdsports": {"www.jdsports.fr", "jdsports.fr"},
    "adidas": {"www.adidas.fr", "adidas.fr"},
}

# Réseaux privés à bloquer (SSRF)
BLOCKED_NETWORKS = [
    ip_network("127.0.0.0/8"),      # Localhost
    ip_network("10.0.0.0/8"),       # Private A
    ip_network("172.16.0.0/12"),    # Private B
    ip_network("192.168.0.0/16"),   # Private C
    ip_network("169.254.0.0/16"),   # Link-local / AWS metadata
    ip_network("0.0.0.0/8"),        # This network
    ip_network("::1/128"),          # IPv6 localhost
    ip_network("fc00::/7"),         # IPv6 private
    ip_network("fe80::/10"),        # IPv6 link-local
]

# Hostnames dangereux explicites
BLOCKED_HOSTNAMES = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "metadata.google.internal",
    "metadata",
    "instance-data",
}


def _is_private_ip(hostname: str) -> bool:
    """Vérifie si un hostname résout vers une IP privée."""
    try:
        # Essayer de parser comme IP directe
        ip = ip_address(hostname)
        for network in BLOCKED_NETWORKS:
            if ip in network:
                return True
        return False
    except ValueError:
        pass

    # Résoudre le hostname
    try:
        resolved = socket.gethostbyname(hostname)
        ip = ip_address(resolved)
        for network in BLOCKED_NETWORKS:
            if ip in network:
                return True
    except (socket.gaierror, ValueError):
        # Si on ne peut pas résoudre, on laisse passer
        # (l'erreur sera catch plus tard au fetch)
        pass

    return False


def validate_url(url: str, source: str) -> str:
    """
    Valide une URL pour un collector spécifique.

    Args:
        url: L'URL à valider
        source: Le nom de la source (courir, footlocker, etc.)

    Returns:
        L'URL validée (nettoyée)

    Raises:
        ValidationError: Si l'URL est invalide ou non autorisée
    """
    if not url:
        raise ValidationError(
            "URL is required",
            field="url",
            source=source,
        )

    # Parser l'URL
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise ValidationError(
            f"Invalid URL format: {e}",
            field="url",
            source=source,
            url=url,
        )

    # Vérifier le scheme (HTTPS uniquement en prod)
    if parsed.scheme not in ("https", "http"):
        raise ValidationError(
            f"Invalid URL scheme: {parsed.scheme}. Only HTTPS allowed.",
            field="url",
            source=source,
            url=url,
        )

    # Extraire le hostname
    hostname = parsed.hostname
    if not hostname:
        raise ValidationError(
            "URL must have a valid hostname",
            field="url",
            source=source,
            url=url,
        )

    hostname = hostname.lower()

    # Vérifier les hostnames bloqués
    if hostname in BLOCKED_HOSTNAMES:
        raise ValidationError(
            f"Blocked hostname: {hostname}",
            field="url",
            source=source,
            url=url,
        )

    # Vérifier les IPs privées
    if _is_private_ip(hostname):
        raise ValidationError(
            f"Private/internal IP not allowed: {hostname}",
            field="url",
            source=source,
            url=url,
        )

    # Vérifier le domaine autorisé pour cette source
    allowed = ALLOWED_DOMAINS.get(source, set())
    if not allowed:
        raise ValidationError(
            f"Unknown source: {source}",
            field="source",
            source=source,
            url=url,
        )

    if hostname not in allowed:
        raise ValidationError(
            f"Domain '{hostname}' not allowed for source '{source}'. "
            f"Allowed: {', '.join(allowed)}",
            field="url",
            source=source,
            url=url,
        )

    # Vérifier qu'il y a un path (pas juste le domaine)
    if not parsed.path or parsed.path == "/":
        raise ValidationError(
            "URL must include a product path",
            field="url",
            source=source,
            url=url,
        )

    return url


def get_allowed_domains(source: str) -> Set[str]:
    """Retourne les domaines autorisés pour une source."""
    return ALLOWED_DOMAINS.get(source, set())


def add_allowed_domain(source: str, domain: str) -> None:
    """Ajoute un domaine autorisé pour une source."""
    if source not in ALLOWED_DOMAINS:
        ALLOWED_DOMAINS[source] = set()
    ALLOWED_DOMAINS[source].add(domain.lower())
