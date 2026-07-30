#!/usr/bin/env bash
#
# Dashboard servisini TEK KOMUTLA başlatır (geliştirme/elle çalıştırma modu).
# systemd servisi kurulu değilken (install.sh çalıştırılmadıysa) projeyi
# ayağa kaldırmanın en kısa yolu: VS Code, venv aktivasyonu veya elle
# environment değişkeni ayarlamak GEREKMEZ.
#
# Çalıştıran kullanıcı: video ve i2c gruplarında olan bir kullanıcı (emre).
#
# Kullanım:
#   ./scripts/start-dashboard.sh              # ön planda çalışır, Ctrl+C ile durur
#   ./scripts/start-dashboard.sh --background # arka planda çalışır, terminal kapanabilir
#
# install.sh çalıştırıldıktan SONRA bu betik yerine systemd kullanılmalıdır:
#   sudo systemctl start jetson-camera-dashboard.service

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
VENV_PYTHON="$BACKEND_DIR/.venv/bin/python"
CONFIG_FILE="$PROJECT_DIR/config/config.yaml"
ENV_FILE="$PROJECT_DIR/.env"
LOG_FILE="$PROJECT_DIR/logs/dashboard-stdout.log"

fail() {
    echo "HATA: $1" >&2
    exit 1
}

# --- Ön koşullar: sessizce yanlış çalışmak yerine net hata ver -------------

# Tüm veri (kod, venv, DB, kayıtlar) SSD'de; mount yoksa kayıt başlamaz ve
# servis de zaten anlamsız olur.
mountpoint -q /mnt/recordings || fail "/mnt/recordings mount edilmemiş. 'sudo mount -a' deneyin."
[ -x "$VENV_PYTHON" ] || fail "Python venv bulunamadı: $VENV_PYTHON (kurulum: ./install.sh)"
[ -f "$CONFIG_FILE" ] || fail "config.yaml yok: $CONFIG_FILE (örnek: config/config.example.yaml)"
[ -f "$ENV_FILE" ] || fail ".env yok: $ENV_FILE (install.sh üretir; SECRET_KEY olmadan servis başlamaz)"

# Port, config.yaml'daki tek doğru kaynaktan okunur (burada tekrar
# sabitlenmez - kullanıcı Ayarlar'dan portu değiştirirse betik de uyar).
export BACKEND_DIR CONFIG_FILE
PORT="$("$VENV_PYTHON" - <<'PYEOF'
import os, sys
sys.path.insert(0, os.environ["BACKEND_DIR"])
from app.core.config import load_config
print(load_config(os.environ["CONFIG_FILE"]).server.port)
PYEOF
)"

# Aynı port zaten dinleniyorsa ikinci bir örnek başlatmak ZARARLI: Argus
# tek oturumlu (iki servis kameraya aynı anda erişemez) ve SQLite tek
# yazıcıyla varsayılmış.
if ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN; then
    fail "Port $PORT zaten dinleniyor - servis muhtemelen çalışıyor.
       Kontrol: curl -s http://127.0.0.1:$PORT/api/health
       Durdurmak için: pkill -f 'uvicorn app.main:app'"
fi

# systemd servisi kuruluysa ve çalışıyorsa elle ikinci örnek başlatma.
if systemctl is-active --quiet jetson-camera-dashboard.service 2>/dev/null; then
    fail "jetson-camera-dashboard.service systemd üzerinden ÇALIŞIYOR.
       Elle başlatmak yerine: sudo systemctl restart jetson-camera-dashboard.service"
fi

# --- Başlat ---------------------------------------------------------------

mkdir -p "$PROJECT_DIR/logs"
cd "$BACKEND_DIR"
export JETSON_CAMERA_CONFIG="$CONFIG_FILE"
export JETSON_CAMERA_ENV_FILE="$ENV_FILE"

HOST_IP="$(hostname -I | awk '{print $1}')"
echo "Dashboard başlatılıyor:  http://${HOST_IP:-127.0.0.1}:$PORT  (yerel: http://127.0.0.1:$PORT)"

if [ "${1:-}" = "--background" ]; then
    # setsid: terminal kapanınca süreç ölmesin (nohup'a ek olarak yeni oturum).
    setsid nohup "$VENV_PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" \
        >> "$LOG_FILE" 2>&1 &
    sleep 6
    if curl -fsS -m 5 "http://127.0.0.1:$PORT/api/health" > /dev/null; then
        echo "Arka planda çalışıyor. Log: $LOG_FILE"
        echo "Durdurmak için: pkill -f 'uvicorn app.main:app'"
    else
        fail "Servis 6 saniye içinde sağlık kontrolünden geçmedi. Log: $LOG_FILE"
    fi
else
    echo "(Ctrl+C durdurur. Arka planda çalıştırmak için: --background)"
    # exec: uvicorn bu kabuğun yerine geçer, böylece Ctrl+C doğrudan ona gider
    # ve aktif kayıt SIGINT ile düzgün finalize edilir (bkz. recording/manager.py).
    exec "$VENV_PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
fi
