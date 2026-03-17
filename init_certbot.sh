#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# init_certbot.sh — Obtain or renew a Let's Encrypt certificate via webroot
# ─────────────────────────────────────────────────────────────────────────────
# Usage:
#   ./init_certbot.sh <your-domain> <your-email>
#
# Example:
#   ./init_certbot.sh crypto.example.com admin@example.com
#
# Prerequisites:
#   - certbot installed on the HOST  (sudo apt install certbot  OR  pip)
#   - The nginx container must be running (docker compose up -d nginx)
#   - Port 80 must be reachable from the internet
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DOMAIN="${1:?Usage: $0 <domain> <email>}"
EMAIL="${2:?Usage: $0 <domain> <email>}"

COMPOSE_PROJECT="cryptoprice"
WEBROOT_VOLUME="${COMPOSE_PROJECT}_certbot-webroot"
LETSENCRYPT_VOLUME="${COMPOSE_PROJECT}_letsencrypt"

# Resolve volume mount points on the host
webroot_mount=$(docker volume inspect "$WEBROOT_VOLUME" --format '{{ .Mountpoint }}')
letsencrypt_mount=$(docker volume inspect "$LETSENCRYPT_VOLUME" --format '{{ .Mountpoint }}')

echo "==> Requesting certificate for $DOMAIN ..."
echo "    Webroot:      $webroot_mount"
echo "    Letsencrypt:  $letsencrypt_mount"

sudo certbot certonly \
    --webroot \
    --webroot-path "$webroot_mount" \
    --config-dir "$letsencrypt_mount" \
    --work-dir /tmp/certbot-work \
    --logs-dir /tmp/certbot-logs \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    -d "$DOMAIN"

echo ""
echo "==> Certificate obtained! Reloading nginx inside the container..."
docker exec nginx nginx -s reload

echo ""
echo "==> Done. Your site should now be live at https://$DOMAIN"
echo ""
echo "To auto-renew, add a cron job (sudo crontab -e):"
echo "  0 3 * * * certbot renew --webroot --webroot-path $webroot_mount --config-dir $letsencrypt_mount --work-dir /tmp/certbot-work --logs-dir /tmp/certbot-logs --deploy-hook 'docker exec nginx nginx -s reload' >> /var/log/certbot-renew.log 2>&1"
