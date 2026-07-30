#!/usr/bin/env bash
#
# jetson-camera-service'i sistemden kaldırır.
#
# VARSAYILAN DAVRANIŞ: video kayıtlarını (media/videos, media/photos),
# veritabanını, config.yaml'ı ve .env'i SİLMEZ - yalnızca systemd
# servisini, sudoers kuralını ve (onaylanırsa) servis kullanıcısını
# kaldırır. Verileri de silmek için --purge-data bayrağını kullanın.
#
# Çalıştıran kullanıcı: sudo yetkisi olan normal bir kullanıcı.
#
# Kullanım:
#   ./scripts/uninstall.sh                 # servisi kaldır, verileri koru
#   ./scripts/uninstall.sh --purge-data     # servisi kaldır VE tüm verileri sil

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSD_MOUNT="/mnt/recordings"
SERVICE_USER="jetcam"
SYSTEMD_UNIT_NAME="jetson-camera-dashboard.service"
RUNTIME_SUDOERS_FILE="/etc/sudoers.d/jetson-camera-service-runtime"

PURGE_DATA=false
if [[ "${1:-}" == "--purge-data" ]]; then
    PURGE_DATA=true
fi

log() { echo "[uninstall.sh] $*"; }

log "Servis durduruluyor ve devre dışı bırakılıyor (sudo ile)..."
sudo systemctl stop "$SYSTEMD_UNIT_NAME" 2>/dev/null || true
sudo systemctl disable "$SYSTEMD_UNIT_NAME" 2>/dev/null || true
sudo rm -f "/etc/systemd/system/$SYSTEMD_UNIT_NAME"
sudo systemctl daemon-reload

log "Çalışma zamanı sudoers kuralı kaldırılıyor..."
sudo rm -f "$RUNTIME_SUDOERS_FILE"

read -r -p "Servis kullanıcısı '$SERVICE_USER' de silinsin mi? [e/H] " remove_user
if [[ "${remove_user,,}" == "e" ]]; then
    sudo userdel "$SERVICE_USER" 2>/dev/null || log "Kullanıcı $SERVICE_USER zaten yok veya silinemedi."
    log "Kullanıcı $SERVICE_USER silindi."
else
    log "Kullanıcı $SERVICE_USER korundu."
fi

if [[ "$PURGE_DATA" == "true" ]]; then
    echo ""
    echo "UYARI: --purge-data belirtildi. Şunlar KALICI OLARAK silinecek:"
    echo "  - $SSD_MOUNT/media/videos (tüm video kayıtları)"
    echo "  - $SSD_MOUNT/media/photos"
    echo "  - $SSD_MOUNT/db (veritabanı)"
    echo "  - $PROJECT_ROOT/config/config.yaml"
    echo "  - $PROJECT_ROOT/.env"
    read -r -p "Bu geri alınamaz. Emin misiniz? 'SIL' yazın: " confirm
    if [[ "$confirm" == "SIL" ]]; then
        rm -rf "$SSD_MOUNT/media" "$SSD_MOUNT/db" "$SSD_MOUNT/run"
        rm -f "$PROJECT_ROOT/config/config.yaml" "$PROJECT_ROOT/.env"
        log "Veriler silindi."
    else
        log "Onay metni eşleşmedi, veri silme İPTAL edildi."
    fi
else
    log "Veriler korundu (varsayılan davranış). Silmek için: $0 --purge-data"
fi

log "Kaldırma işlemi tamamlandı."
log "Proje dosyaları ($PROJECT_ROOT) diskte bırakıldı; tamamen kaldırmak için manuel olarak silin."
