#!/usr/bin/env bash
#
# Tailscale'ı kurar ve bu cihazı Tailscale ağına bağlar. Şartname böl. 14:
# dashboard'un varsayılan uzaktan erişim yöntemi Tailscale'dir; port
# yönlendirme (port forwarding) KULLANILMAZ.
#
# Resmi apt deposu üzerinden kurar (curl|sh yerine) - hangi paketin
# kurulduğu `apt` ile denetlenebilir.
#
# Çalıştıran kullanıcı: sudo yetkisi olan kullanıcı.
#
# Kullanım:
#   ./scripts/setup-tailscale.sh                        # interaktif (tarayıcıda auth linki açılır)
#   TS_AUTHKEY=tskey-... ./scripts/setup-tailscale.sh    # headless (Tailscale admin panelinden üretilen auth key ile)

set -euo pipefail

log() { echo "[setup-tailscale.sh] $*"; }
die() { echo "[setup-tailscale.sh] HATA: $*" >&2; exit 1; }

UBUNTU_CODENAME="jammy"  # Ubuntu 22.04

if command -v tailscale &>/dev/null; then
    log "Tailscale zaten kurulu: $(tailscale --version | head -1)"
else
    log "Tailscale apt deposu ekleniyor (sudo ile)..."
    curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${UBUNTU_CODENAME}.noarmor.gpg" \
        | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
    curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${UBUNTU_CODENAME}.tailscale-keyring.list" \
        | sudo tee /etc/apt/sources.list.d/tailscale.list >/dev/null

    log "Tailscale kuruluyor (sudo ile)..."
    sudo apt-get update -qq
    sudo apt-get install -y tailscale
fi

log "tailscaled servisi etkinleştiriliyor..."
sudo systemctl enable --now tailscaled

if tailscale status &>/dev/null; then
    log "Bu cihaz zaten Tailscale ağına bağlı."
else
    if [[ -n "${TS_AUTHKEY:-}" ]]; then
        log "Headless kimlik doğrulama (TS_AUTHKEY ile) yapılıyor..."
        sudo tailscale up --authkey="$TS_AUTHKEY"
    else
        log "Tarayıcı tabanlı kimlik doğrulama başlatılıyor."
        log "Aşağıda çıkan linki bir tarayıcıda açıp Tailscale hesabınızla onaylayın:"
        sudo tailscale up
    fi
fi

echo ""
log "Durum:"
tailscale status || true

TS_IP="$(tailscale ip -4 2>/dev/null || true)"
if [[ -n "$TS_IP" ]]; then
    echo ""
    log "Bu cihazın Tailscale IP'si: $TS_IP"
    log "Dashboard'a Tailscale üzerinden erişim: http://$TS_IP:8080"
    log "Sonraki adım: dashboard portunu Tailscale/LAN dışına kapatmak için ./scripts/configure-firewall.sh"
else
    log "UYARI: Tailscale IP alınamadı. 'tailscale status' ile bağlantıyı kontrol edin."
fi
