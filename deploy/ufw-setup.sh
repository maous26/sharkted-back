#!/bin/bash
# =============================================================================
# Sharkted VPS - UFW Firewall Setup
# =============================================================================
#
# Ce script configure le firewall UFW pour:
# - Autoriser SSH (22)
# - Autoriser HTTP/HTTPS (80/443) pour Caddy
# - Bloquer l'accès direct au port 3000 (API)
#
# Usage:
#   chmod +x deploy/ufw-setup.sh
#   sudo ./deploy/ufw-setup.sh
#
# =============================================================================

set -e

echo "=== Sharkted VPS - UFW Setup ==="

# Reset UFW (optionnel - décommenter si besoin)
# echo "Resetting UFW..."
# sudo ufw --force reset

# Default policies
echo "Setting default policies..."
sudo ufw default deny incoming
sudo ufw default allow outgoing

# SSH (important: ne pas se bloquer!)
echo "Allowing SSH (22)..."
sudo ufw allow 22/tcp comment 'SSH'

# HTTP/HTTPS pour Caddy
echo "Allowing HTTP (80)..."
sudo ufw allow 80/tcp comment 'HTTP (Caddy)'

echo "Allowing HTTPS (443)..."
sudo ufw allow 443/tcp comment 'HTTPS (Caddy)'

# Bloquer le port 3000 depuis l'extérieur
# (l'API ne doit être accessible que via Caddy)
echo "Denying direct access to port 3000..."
sudo ufw deny 3000/tcp comment 'Block direct API access'

# Activer UFW
echo "Enabling UFW..."
sudo ufw --force enable

# Status
echo ""
echo "=== UFW Status ==="
sudo ufw status verbose

echo ""
echo "Done! Caddy should now be the only way to access the API."
echo ""
echo "Next steps:"
echo "1. Install Caddy: sudo apt install caddy"
echo "2. Copy Caddyfile: sudo cp deploy/Caddyfile /etc/caddy/Caddyfile"
echo "3. Edit domain: sudo nano /etc/caddy/Caddyfile"
echo "4. Reload Caddy: sudo caddy reload --config /etc/caddy/Caddyfile"
