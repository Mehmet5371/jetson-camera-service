#!/usr/bin/env bash
#
# Dashboard portunu (varsayılan 8080) yalnızca Tailscale ağı, yerel LAN
# ve açıkça izin verilen IP'lerden erişilebilir kılar (şartname böl. 14).
# Port yönlendirme YOKTUR ve önerilmez.
#
# GÜVENLİK UYARISI: Bu script ufw'yi (varsayılan reddet / incoming)
# etkinleştirir. SSH (22) portu HER ZAMAN ve KOŞULSUZ olarak önce
# açılır - cihazdan kilitlenmeyi önlemek için. Yine de uzaktan
# çalıştırıyorsanız SSH bağlantınızın kopmayacağından emin olduktan
# sonra devam edin (yerel konsol erişiminiz yoksa dikkatli olun).
#
# Çalıştıran kullanıcı: sudo yetkisi olan kullanıcı.
#
# Kullanım:
#   ./scripts/configure-firewall.sh                         # yalnızca LAN + Tailscale
#   ./scripts/configure-firewall.sh 203.0.113.5 203.0.113.6  # + açıkça izin verilen IP'ler

set -euo pipefail

DASHBOARD_PORT=8080
SSH_PORT=22

log() { echo "[configure-firewall.sh] $*"; }

if ! command -v ufw &>/dev/null; then
    log "ufw kuruluyor (sudo ile)..."
    sudo apt-get update -qq
    sudo apt-get install -y ufw
fi

log "Varsayılan politika: gelen reddet, giden izin ver..."
sudo ufw default deny incoming
sudo ufw default allow outgoing

log "SSH ($SSH_PORT) HER ZAMAN açık tutuluyor (kilitlenmeyi önlemek için)..."
sudo ufw allow "$SSH_PORT"/tcp comment 'SSH - always allowed'

LOCAL_SUBNET="$(ip -4 route show scope link 2>/dev/null | awk '{print $1}' | grep -v '^169\.254' | head -1)"
if [[ -n "$LOCAL_SUBNET" ]]; then
    log "Yerel LAN alt ağı tespit edildi: $LOCAL_SUBNET"
    sudo ufw allow from "$LOCAL_SUBNET" to any port "$DASHBOARD_PORT" proto tcp comment 'jetson-camera-service LAN'
else
    log "UYARI: yerel LAN alt ağı otomatik tespit edilemedi - LAN erişimi eklenmedi, manuel ekleyin:"
    log "  sudo ufw allow from <subnet> to any port $DASHBOARD_PORT proto tcp"
fi

if ip link show tailscale0 &>/dev/null; then
    log "Tailscale arayüzü (tailscale0) bulundu, Tailscale ağından erişime izin veriliyor..."
    sudo ufw allow in on tailscale0 to any port "$DASHBOARD_PORT" proto tcp comment 'jetson-camera-service Tailscale'
else
    log "UYARI: tailscale0 arayüzü bulunamadı (önce ./scripts/setup-tailscale.sh çalıştırın)."
    log "Yine de Tailscale'ın CGNAT aralığından (100.64.0.0/10) izin veriliyor, arayüz sonra oluşacak:"
    sudo ufw allow from 100.64.0.0/10 to any port "$DASHBOARD_PORT" proto tcp comment 'jetson-camera-service Tailscale CGNAT'
fi

for extra_ip in "$@"; do
    log "Ek olarak izin veriliyor: $extra_ip"
    sudo ufw allow from "$extra_ip" to any port "$DASHBOARD_PORT" proto tcp comment 'jetson-camera-service explicit allow'
done

log "ufw etkinleştiriliyor..."
sudo ufw --force enable

echo ""
log "Güncel durum:"
sudo ufw status verbose
