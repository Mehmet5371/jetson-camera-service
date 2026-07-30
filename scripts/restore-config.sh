#!/usr/bin/env bash
#
# backup-config.sh ile alınmış bir yedeği geri yükler. Mevcut config.yaml,
# .env ve veritabanının üzerine yazmadan ÖNCE onay ister ve mevcut
# dosyaları ".before-restore" uzantısıyla yanına yedekler (geri dönüş
# imkânı için).
#
# Çalıştıran kullanıcı: emre (veya sudo yetkisi olan herhangi bir kullanıcı).
# UYARI: Bu işlemden sonra servisin yeniden başlatılması GEREKİR:
#   sudo systemctl restart jetson-camera-dashboard.service
#
# Kullanım: ./scripts/restore-config.sh <yedek_arşivi.tar.gz>

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE_PATH="${1:-}"

if [[ -z "$ARCHIVE_PATH" || ! -f "$ARCHIVE_PATH" ]]; then
    echo "Kullanım: $0 <yedek_arşivi.tar.gz>" >&2
    exit 1
fi

echo "Bu işlem şunları geri yükleyecek: config.yaml, .env, veritabanı."
echo "Mevcut dosyalar '.before-restore' uzantısıyla yedeklenecek."
read -r -p "Devam edilsin mi? [e/H] " confirm
if [[ "${confirm,,}" != "e" ]]; then
    echo "İptal edildi."
    exit 0
fi

STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGING_DIR"' EXIT
tar -xzf "$ARCHIVE_PATH" -C "$STAGING_DIR"

if [[ -f "$STAGING_DIR/config.yaml" ]]; then
    [[ -f "$PROJECT_ROOT/config/config.yaml" ]] && cp "$PROJECT_ROOT/config/config.yaml" "$PROJECT_ROOT/config/config.yaml.before-restore"
    cp "$STAGING_DIR/config.yaml" "$PROJECT_ROOT/config/config.yaml"
    echo "config.yaml geri yüklendi."
fi

if [[ -f "$STAGING_DIR/.env" ]]; then
    [[ -f "$PROJECT_ROOT/.env" ]] && cp "$PROJECT_ROOT/.env" "$PROJECT_ROOT/.env.before-restore"
    cp "$STAGING_DIR/.env" "$PROJECT_ROOT/.env"
    chmod 600 "$PROJECT_ROOT/.env"
    echo ".env geri yüklendi."
fi

if [[ -f "$STAGING_DIR/app.db" ]]; then
    VENV_PYTHON="$PROJECT_ROOT/backend/.venv/bin/python"
    DB_PATH="$(cd "$PROJECT_ROOT/backend" && JETSON_CAMERA_CONFIG="$PROJECT_ROOT/config/config.yaml" "$VENV_PYTHON" -c \
        'from app.core.config import load_config; print(load_config().storage.database_path)')"
    if [[ -f "$DB_PATH" ]]; then
        cp "$DB_PATH" "${DB_PATH}.before-restore"
    fi
    mkdir -p "$(dirname "$DB_PATH")"
    cp "$STAGING_DIR/app.db" "$DB_PATH"
    echo "Veritabanı geri yüklendi: $DB_PATH"
fi

echo ""
echo "Geri yükleme tamamlandı. Servisi yeniden başlatın:"
echo "  sudo systemctl restart jetson-camera-dashboard.service"
