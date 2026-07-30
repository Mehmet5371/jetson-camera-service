#!/usr/bin/env bash
#
# config.yaml, .env ve veritabanını (video dosyaları HARİÇ - onlar zaten
# SSD'de ve bu yedeğin kapsamı dışında) zaman damgalı bir arşive yedekler.
#
# Çalıştıran kullanıcı: emre (veya sudo yetkisi olan herhangi bir kullanıcı -
# .env okumak için dosya sahibiyle aynı kullanıcı olmak yeterlidir).
#
# Kullanım: ./scripts/backup-config.sh [hedef_dizin]

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${1:-$PROJECT_ROOT/backups}"
TIMESTAMP="$(date +%Y-%m-%d_%H-%M-%S)"
ARCHIVE_NAME="jetson-camera-config-backup_${TIMESTAMP}.tar.gz"
ARCHIVE_PATH="$BACKUP_DIR/$ARCHIVE_NAME"

mkdir -p "$BACKUP_DIR"

STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGING_DIR"' EXIT

[[ -f "$PROJECT_ROOT/config/config.yaml" ]] && cp "$PROJECT_ROOT/config/config.yaml" "$STAGING_DIR/"
[[ -f "$PROJECT_ROOT/.env" ]] && cp "$PROJECT_ROOT/.env" "$STAGING_DIR/"

# database_path YAML içinde iç içe (storage: altında) olduğu için basit
# grep ile GÜVENİLİR şekilde çıkarılamaz - venv Python'u ile gerçek YAML
# parse'ı yapılıyor.
VENV_PYTHON="$PROJECT_ROOT/backend/.venv/bin/python"
if [[ -x "$VENV_PYTHON" ]]; then
    DB_PATH="$(cd "$PROJECT_ROOT/backend" && JETSON_CAMERA_CONFIG="$PROJECT_ROOT/config/config.yaml" "$VENV_PYTHON" -c \
        'from app.core.config import load_config; print(load_config().storage.database_path)' 2>/dev/null || true)"
else
    DB_PATH=""
fi
if [[ -n "${DB_PATH:-}" && -f "$DB_PATH" ]]; then
    cp "$DB_PATH" "$STAGING_DIR/app.db"
else
    echo "UYARI: veritabanı dosyası bulunamadı, yedeğe dahil edilmiyor."
fi

tar -czf "$ARCHIVE_PATH" -C "$STAGING_DIR" .
chmod 600 "$ARCHIVE_PATH"

echo "Yedek oluşturuldu: $ARCHIVE_PATH"
echo "İçerik:"
tar -tzf "$ARCHIVE_PATH"
