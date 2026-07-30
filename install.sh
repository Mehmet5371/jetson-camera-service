#!/usr/bin/env bash
#
# jetson-camera-service kurulum betiği.
# Ubuntu 22.04 + JetPack 6.2.1 (L4T R36.4.4) için doğrulanmıştır.
#
# İDEMPOTENT'tir: tekrar çalıştırıldığında mevcut config.yaml, .env,
# veritabanı veya kayıtları SİLMEZ/ÜZERİNE YAZMAZ - yalnızca eksik
# olanları oluşturur ve servisi güncel koda göre yeniden başlatır.
#
# Çalıştıran kullanıcı: sudo yetkisi olan normal bir kullanıcı (emre gibi).
# root olarak DOĞRUDAN çalıştırılmamalıdır (gerekli yerlerde kendi sudo
# çağırır).
#
# Kullanım: ./install.sh

set -euo pipefail

# --- Sabitler ---
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
VENV_DIR="$BACKEND_DIR/.venv"
SSD_MOUNT="/mnt/recordings"
SERVICE_USER="jetcam"
SERVICE_GROUP="jetcam"
SYSTEMD_UNIT_NAME="jetson-camera-dashboard.service"
SYSTEMD_UNIT_SRC="$PROJECT_ROOT/systemd/$SYSTEMD_UNIT_NAME"
SYSTEMD_UNIT_DST="/etc/systemd/system/$SYSTEMD_UNIT_NAME"
RUNTIME_SUDOERS_FILE="/etc/sudoers.d/jetson-camera-service-runtime"
REQUIRED_APT_PACKAGES=(python3.10-venv python3-pip ffmpeg v4l-utils i2c-tools gstreamer1.0-tools)

log() { echo "[install.sh] $*"; }
die() { echo "[install.sh] HATA: $*" >&2; exit 1; }

# --- 1) Sistem uyumluluğu kontrolü (çalıştıran kullanıcı: emre/mevcut kullanıcı) ---
check_compatibility() {
    log "Sistem uyumluluğu kontrol ediliyor..."

    if [[ "$EUID" -eq 0 ]]; then
        die "Bu betiği root olarak DOĞRUDAN çalıştırmayın; sudo yetkisi olan normal bir kullanıcıyla çalıştırın."
    fi

    if [[ "$(uname -m)" != "aarch64" ]]; then
        die "Bu betik yalnızca aarch64 (Jetson) için tasarlandı, bulunan: $(uname -m)"
    fi

    if [[ -f /etc/os-release ]]; then
        # shellcheck source=/dev/null
        source /etc/os-release
        if [[ "${VERSION_ID:-}" != "22.04" ]]; then
            log "UYARI: Ubuntu 22.04 bekleniyordu, bulunan: ${VERSION_ID:-bilinmiyor}. Devam ediliyor ama doğrulanmamış."
        fi
    fi

    if [[ -f /proc/device-tree/model ]]; then
        model="$(tr -d '\0' < /proc/device-tree/model)"
        log "Jetson modeli: $model"
        if [[ "$model" != *Jetson* ]]; then
            log "UYARI: /proc/device-tree/model 'Jetson' içermiyor - bu bir Jetson cihazı olmayabilir."
        fi
    else
        log "UYARI: /proc/device-tree/model bulunamadı, model doğrulanamadı."
    fi

    log "Uyumluluk kontrolü tamamlandı."
}

# --- 2) SSD mount kontrolü (emre) ---
check_ssd_mounted() {
    log "SSD mount durumu kontrol ediliyor ($SSD_MOUNT)..."
    if ! mountpoint -q "$SSD_MOUNT"; then
        die "$SSD_MOUNT bir mount noktası değil. Kuruluma devam edilmiyor (SD karta yanlışlıkla veri yazılmasını önlemek için)."
    fi
    if [[ ! -w "$SSD_MOUNT" ]]; then
        die "$SSD_MOUNT yazılabilir değil. İzinleri kontrol edin."
    fi
    log "SSD mount doğrulandı."
}

# --- 3) apt paketleri (sudo/root) ---
install_apt_packages() {
    log "Gerekli apt paketleri kuruluyor (sudo ile): ${REQUIRED_APT_PACKAGES[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y "${REQUIRED_APT_PACKAGES[@]}"
    log "apt paketleri hazır."
}

# --- 4) Servis kullanıcısı (sudo/root) ---
create_service_user() {
    log "Servis kullanıcısı ($SERVICE_USER) hazırlanıyor (sudo ile)..."
    if ! id -u "$SERVICE_USER" &>/dev/null; then
        sudo useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
        log "Kullanıcı $SERVICE_USER oluşturuldu."
    else
        log "Kullanıcı $SERVICE_USER zaten var, atlanıyor."
    fi
    # Kamera (/dev/video0) ve I2C (odak motoru) erişimi için gerekli gruplar.
    sudo usermod -aG video,i2c "$SERVICE_USER"
    log "$SERVICE_USER, video ve i2c gruplarına eklendi."
}

# --- 5) Dizinler ve izinler (sudo/root) ---
create_directories() {
    log "Veri dizinleri oluşturuluyor (sudo ile)..."
    sudo mkdir -p \
        "$SSD_MOUNT/media/videos" \
        "$SSD_MOUNT/media/photos" \
        "$SSD_MOUNT/db" \
        "$SSD_MOUNT/run"
    mkdir -p "$PROJECT_ROOT/logs"

    sudo chown -R "$SERVICE_USER:$SERVICE_GROUP" \
        "$SSD_MOUNT/media" "$SSD_MOUNT/db" "$SSD_MOUNT/run"
    sudo chown -R "$SERVICE_USER:$SERVICE_GROUP" "$PROJECT_ROOT/logs"

    # config/ dizini dashboard'dan ayar değişikliği (PUT /api/settings)
    # yazabilsin diye servis grubuna g+w veriliyor; kodun geri kalanı
    # (backend/frontend/migrations) yalnızca OKUNABİLİR kalıyor (o+rX).
    sudo chgrp -R "$SERVICE_GROUP" "$PROJECT_ROOT/config"
    sudo chmod -R g+w "$PROJECT_ROOT/config"
    sudo chmod -R o+rX "$PROJECT_ROOT/backend" "$PROJECT_ROOT/frontend" "$PROJECT_ROOT/migrations"

    log "Dizinler ve izinler hazırlandı."
}

# --- 6) Python venv + bağımlılıklar (emre - root GEREKMEZ) ---
create_venv_and_install_deps() {
    if [[ ! -d "$VENV_DIR" ]]; then
        log "Python venv oluşturuluyor: $VENV_DIR"
        python3 -m venv "$VENV_DIR"
    else
        log "venv zaten var, atlanıyor: $VENV_DIR"
    fi

    log "Backend bağımlılıkları kuruluyor (pip)..."
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install -r "$BACKEND_DIR/requirements.txt" -q
    log "Bağımlılıklar hazır."
}

# --- 7) config.yaml (emre - yalnızca yoksa oluşturur) ---
create_config_file() {
    local config_yaml="$PROJECT_ROOT/config/config.yaml"
    local config_example="$PROJECT_ROOT/config/config.example.yaml"
    if [[ -f "$config_yaml" ]]; then
        log "config.yaml zaten var, korunuyor (üzerine yazılmıyor)."
    else
        cp "$config_example" "$config_yaml"
        log "config.yaml, config.example.yaml'dan oluşturuldu."
    fi
}

# --- 8) .env (emre - yalnızca yoksa oluşturur, rastgele kimlik bilgileri üretir) ---
create_env_file() {
    local env_file="$PROJECT_ROOT/.env"
    if [[ -f "$env_file" ]]; then
        log ".env zaten var, korunuyor (üzerine yazılmıyor, kimlik bilgileri değişmiyor)."
        return
    fi

    log ".env oluşturuluyor: yeni SECRET_KEY ve admin parolası üretiliyor..."
    local secret_key admin_password admin_password_hash
    secret_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
    admin_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"
    admin_password_hash="$("$VENV_DIR/bin/python" - "$admin_password" <<'PYEOF'
import sys
sys.path.insert(0, "backend")
from app.security.passwords import hash_password
print(hash_password(sys.argv[1]))
PYEOF
)"

    cat > "$env_file" <<EOF
JETSON_CAMERA_SECRET_KEY=$secret_key
JETSON_CAMERA_ADMIN_USERNAME=admin
JETSON_CAMERA_ADMIN_PASSWORD_HASH=$admin_password_hash
EOF
    chmod 600 "$env_file"

    echo ""
    echo "=================================================================="
    echo " İLK KURULUM - VARSAYILAN GİRİŞ BİLGİLERİ (bir daha gösterilmeyecek)"
    echo "   Kullanıcı adı : admin"
    echo "   Parola        : $admin_password"
    echo " İlk girişte bu parolayı değiştirmeniz istenecektir."
    echo "=================================================================="
    echo ""
}

# --- 9) systemd birimi (sudo/root) ---
install_systemd_unit() {
    log "systemd birimi kuruluyor (sudo ile): $SYSTEMD_UNIT_DST"
    sudo cp "$SYSTEMD_UNIT_SRC" "$SYSTEMD_UNIT_DST"
    sudo systemctl daemon-reload
    log "systemd birimi kuruldu ve daemon-reload çalıştırıldı."
}

# --- 10) Çalışma zamanı sudoers kuralı (sudo/root) ---
setup_runtime_sudoers() {
    log "Kamera servisi yeniden başlatma için dar kapsamlı sudo kuralı kuruluyor..."
    local tmp_file
    tmp_file="$(mktemp)"
    echo "$SERVICE_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart nvargus-daemon" > "$tmp_file"
    sudo visudo -c -f "$tmp_file" || die "Üretilen sudoers dosyası geçersiz, iptal ediliyor."
    sudo install -m 0440 -o root -g root "$tmp_file" "$RUNTIME_SUDOERS_FILE"
    rm -f "$tmp_file"
    log "Sudoers kuralı kuruldu: $RUNTIME_SUDOERS_FILE"
}

# --- 11) Veritabanı migration'ları (emre - root GEREKMEZ) ---
run_migrations() {
    log "Veritabanı migration'ları çalıştırılıyor..."
    (
        cd "$PROJECT_ROOT/migrations"
        JETSON_CAMERA_CONFIG="$PROJECT_ROOT/config/config.yaml" "$VENV_DIR/bin/alembic" upgrade head
    )
    log "Migration'lar tamamlandı."
}

# --- 12) Servisi etkinleştir ve başlat (sudo/root) ---
enable_and_start_service() {
    log "Servis etkinleştiriliyor ve (yeniden) başlatılıyor (sudo ile)..."
    sudo systemctl enable "$SYSTEMD_UNIT_NAME"
    sudo systemctl restart "$SYSTEMD_UNIT_NAME"
    log "Servis başlatıldı."
}

# --- 13) Health check (emre) ---
health_check() {
    log "Servis sağlık kontrolü yapılıyor..."
    local attempt
    for attempt in $(seq 1 15); do
        if curl -fsS "http://127.0.0.1:8080/api/health" >/dev/null 2>&1; then
            log "Servis sağlıklı şekilde yanıt veriyor."
            return 0
        fi
        sleep 1
    done
    log "UYARI: Servis 15 saniye içinde sağlıklı yanıt vermedi."
    log "Kontrol edin: sudo systemctl status $SYSTEMD_UNIT_NAME"
    log "Loglar için: sudo journalctl -u $SYSTEMD_UNIT_NAME -n 100 --no-pager"
    return 1
}

# --- 14) Özet (emre) ---
print_summary() {
    local ip_addr
    ip_addr="$(hostname -I 2>/dev/null | awk '{print $1}')"
    echo ""
    echo "=================================================================="
    echo " Kurulum tamamlandı."
    echo " Dashboard (yerel ağ): http://${ip_addr:-<jetson-ip>}:8080"
    echo " Dashboard (bu cihazda): http://127.0.0.1:8080"
    echo ""
    echo " Uzaktan (internet üzerinden) güvenli erişim için Tailscale kurulumu"
    echo " gereklidir - bkz. docs/REMOTE_ACCESS.md (Faz 8)."
    echo ""
    echo " Servis durumu : sudo systemctl status $SYSTEMD_UNIT_NAME"
    echo " Loglar        : sudo journalctl -u $SYSTEMD_UNIT_NAME -f"
    echo " Servisi durdur: sudo systemctl stop $SYSTEMD_UNIT_NAME"
    echo "=================================================================="
}

main() {
    check_compatibility
    check_ssd_mounted
    install_apt_packages
    create_service_user
    create_directories
    create_venv_and_install_deps
    create_config_file
    create_env_file
    run_migrations
    install_systemd_unit
    setup_runtime_sudoers
    enable_and_start_service
    health_check || true
    print_summary
}

main "$@"
