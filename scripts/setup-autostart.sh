#!/usr/bin/env bash
#
# "Güç tuşuna bas, dashboard gelsin" kurulumu.
#
# İki parçayı kurar:
#   1) systemd sistem birimi  -> backend açılışta otomatik başlar (giriş
#      yapılmasa bile), çökerse yeniden başlar.
#   2) ~/.config/autostart/   -> masaüstü açılınca dashboard tam ekran
#      tarayıcıda açılır (bkz. scripts/dashboard-kiosk.sh).
#
# install.sh'DAN FARKI: install.sh üretim kurulumudur - ayrı bir `jetcam`
# sistem kullanıcısı açar ve tüm proje dosyalarının sahipliğini ona verir.
# Bu betik ise projeyi ZATEN çalıştıran masaüstü kullanıcısının (emre)
# kimliğiyle kurar; dosya sahipliğine ve mevcut kuruluma dokunmaz. Tek
# kullanıcılı masaüstü senaryosu için doğru olan budur.
#
# Çalıştıran kullanıcı: projeyi çalıştıran, sudo yetkisi olan kullanıcı.
#
# Kullanım:
#   ./scripts/setup-autostart.sh
#   ./scripts/setup-autostart.sh --uninstall

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
VENV_PYTHON="$BACKEND_DIR/.venv/bin/python"
CONFIG_FILE="$PROJECT_DIR/config/config.yaml"
ENV_FILE="$PROJECT_DIR/.env"

SERVICE_NAME="jetson-camera-dashboard.service"
UNIT_TEMPLATE="$PROJECT_DIR/systemd/$SERVICE_NAME"
UNIT_TARGET="/etc/systemd/system/$SERVICE_NAME"

AUTOSTART_DIR="$HOME/.config/autostart"
AUTOSTART_FILE="$AUTOSTART_DIR/jetson-camera-dashboard.desktop"
KIOSK_SCRIPT="$PROJECT_DIR/scripts/dashboard-kiosk.sh"

SERVICE_USER="$(id -un)"
SERVICE_GROUP="$(id -gn)"

log() { echo "[setup-autostart.sh] $*"; }
die() { echo "[setup-autostart.sh] HATA: $*" >&2; exit 1; }

# --- Kaldırma -------------------------------------------------------------
if [ "${1:-}" = "--uninstall" ]; then
    log "Otomatik başlatma kaldırılıyor..."
    sudo systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
    sudo rm -f "$UNIT_TARGET"
    sudo systemctl daemon-reload
    rm -f "$AUTOSTART_FILE"
    log "Kaldırıldı. Servis ve kiosk artık açılışta başlamayacak."
    exit 0
fi

# --- Ön koşullar ----------------------------------------------------------
# Eksik parçayla kurup açılışta sessizce patlamaktansa burada net hata ver.
[ "$SERVICE_USER" != "root" ] || die "Bu betiği root olarak değil, masaüstü kullanıcısıyla çalıştırın."
mountpoint -q /mnt/recordings || die "/mnt/recordings mount edilmemiş."
[ -x "$VENV_PYTHON" ] || die "Python venv yok: $VENV_PYTHON (önce ./install.sh)"
[ -f "$CONFIG_FILE" ] || die "config.yaml yok: $CONFIG_FILE"
[ -f "$ENV_FILE" ] || die ".env yok: $ENV_FILE (SECRET_KEY olmadan servis başlamaz)"
[ -f "$UNIT_TEMPLATE" ] || die "systemd şablonu yok: $UNIT_TEMPLATE"
[ -x "$KIOSK_SCRIPT" ] || die "Kiosk betiği çalıştırılabilir değil: $KIOSK_SCRIPT"

# Şablondaki ExecStart mutlak yol içeriyor; proje başka bir dizine
# taşındıysa birim sessizce yanlış yola bakar.
grep -q "$PROJECT_DIR/backend" "$UNIT_TEMPLATE" \
    || die "systemd şablonundaki yollar bu dizinle uyuşmuyor ($PROJECT_DIR). Şablonu güncelleyin."

# Kamera erişimi grup üyeliğiyle olur; servis bu kullanıcı olarak çalışacağı
# için üyelik yoksa kayıt başlamaz.
for grp in video i2c; do
    id -nG "$SERVICE_USER" | tr ' ' '\n' | grep -qx "$grp" \
        || die "$SERVICE_USER kullanıcısı '$grp' grubunda değil: sudo usermod -aG $grp $SERVICE_USER"
done

# --- 1) systemd birimi ----------------------------------------------------
log "systemd birimi kuruluyor ($SERVICE_NAME, kullanıcı: $SERVICE_USER)..."

# Çalışan bir örnek varsa port çakışır (Argus tek oturumlu, SQLite tek
# yazıcı varsayımı) - önce onu durdur. Betik yeniden çalıştırıldığında
# süreç systemd'ye ait olabilir; bu yüzden önce servisi düzgünce durdur
# (TimeoutStopSec sayesinde aktif kayıt finalize edilir), pkill yalnızca
# elle başlatılmış artıklar için.
if [ -f "$UNIT_TARGET" ]; then
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
fi
if pgrep -f 'uvicorn app.main:app' > /dev/null 2>&1; then
    log "Elle başlatılmış uvicorn bulundu, durduruluyor (systemd devralacak)..."
    # SIGINT: RecordingManager.shutdown() aktif kaydı EOS ile finalize eder.
    pkill -INT -f 'uvicorn app.main:app' || true
    for _ in $(seq 30); do
        pgrep -f 'uvicorn app.main:app' > /dev/null 2>&1 || break
        sleep 1
    done
    pkill -f 'uvicorn app.main:app' 2>/dev/null || true
fi

tmp_unit="$(mktemp)"
trap 'rm -f "$tmp_unit"' EXIT
sed -e "s/^User=.*/User=$SERVICE_USER/" \
    -e "s/^Group=.*/Group=$SERVICE_GROUP/" \
    "$UNIT_TEMPLATE" > "$tmp_unit"

# ProtectHome=true ev dizinini gizler. Proje /mnt/recordings altında olduğu
# için backend'in eve ihtiyacı yok, ama birim artık gerçek bir masaüstü
# kullanıcısı olarak çalışıyor - yanlışlıkla ~ altına yazma denemesi
# olursa erken ve gürültülü patlaması iyidir, bu yüzden korunuyor.
grep -q '^User=' "$tmp_unit" || die "Birim şablonunda User= satırı yok."

sudo install -m 0644 -o root -g root "$tmp_unit" "$UNIT_TARGET"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

log "Servis sağlık kontrolü bekleniyor..."
PORT="$(grep -oP '(?<=--port )\d+' "$UNIT_TARGET" | head -1)"
PORT="${PORT:-8080}"
healthy=0
for _ in $(seq 30); do
    if curl -fsS -m 2 "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
        healthy=1
        break
    fi
    sleep 1
done
[ "$healthy" -eq 1 ] || die "Servis 30 sn içinde yanıt vermedi. Log: journalctl -u $SERVICE_NAME -n 50"
log "Backend çalışıyor: http://127.0.0.1:$PORT/"

# --- 2) Masaüstü autostart girdisi ---------------------------------------
log "Masaüstü autostart girdisi yazılıyor: $AUTOSTART_FILE"
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Jetson Camera Dashboard
Comment=Acilista dashboard'u tam ekran tarayicida acar
Exec=$KIOSK_SCRIPT
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
chmod 644 "$AUTOSTART_FILE"

# --- 3) Masaüstü otomatik girişi (yalnızca rapor) -------------------------
# Autologin kapalıysa güç tuşundan sonra Ubuntu parola sorar ve "tek tuş"
# akışı bozulur. Bu betik GDM ayarını KENDİLİĞİNDEN DEĞİŞTİRMEZ - parola
# sormayı kapatmak güvenlik kararıdır, kullanıcıya bırakılır.
if grep -qE '^\s*AutomaticLoginEnable\s*=\s*[Tt]rue' /etc/gdm3/custom.conf 2>/dev/null; then
    autologin_user="$(grep -oP '(?<=^AutomaticLogin=).*' /etc/gdm3/custom.conf 2>/dev/null | head -1)"
    log "GDM otomatik girişi AÇIK (kullanıcı: ${autologin_user:-?}) - açılışta parola sorulmayacak."
else
    log "UYARI: GDM otomatik girişi KAPALI. Açılışta önce Ubuntu parolası sorulacak,"
    log "       dashboard ancak masaüstü açıldıktan sonra gelecek."
    log "       Parolasız açılış isterseniz /etc/gdm3/custom.conf içinde:"
    log "         [daemon]"
    log "         AutomaticLoginEnable=true"
    log "         AutomaticLogin=$SERVICE_USER"
fi

log ""
log "TAMAM. Bundan sonra güç tuşuna basmanız yeterli."
log "  Servis durumu : systemctl status $SERVICE_NAME"
log "  Servis logu   : journalctl -u $SERVICE_NAME -f"
log "  Kiosk logu    : $PROJECT_DIR/logs/kiosk.log"
log "  Geri alma     : ./scripts/setup-autostart.sh --uninstall"
