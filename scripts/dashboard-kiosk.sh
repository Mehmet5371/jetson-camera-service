#!/usr/bin/env bash
#
# Masaüstü oturumu açılınca dashboard'u tam ekran tarayıcıda açar.
# ~/.config/autostart/ içindeki .desktop girdisi tarafından çağrılır
# (bkz. scripts/setup-autostart.sh).
#
# Sunucuyu BU BETİK BAŞLATMAZ - backend systemd tarafından açılışta
# ayağa kaldırılır. Burada yalnızca servis sağlıklı yanıt verene kadar
# beklenir; aksi halde tarayıcı "bağlanılamadı" hatasıyla açılırdı
# (uvicorn + kamera tespiti masaüstünden birkaç saniye sonra hazır olur).
#
# Çalıştıran kullanıcı: masaüstü oturumunu açan kullanıcı (emre).

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
VENV_PYTHON="$BACKEND_DIR/.venv/bin/python"
CONFIG_FILE="$PROJECT_DIR/config/config.yaml"
LOG_FILE="$PROJECT_DIR/logs/kiosk.log"

# Servisin hazır olması için beklenecek azami süre. Soğuk açılışta SSD
# mount + uvicorn + ilk kamera taraması toplamda ~15-20 sn sürebiliyor;
# 90 sn bunun rahat üstünde ama sonsuza kadar da beklemez.
MAX_WAIT_SECONDS=90

mkdir -p "$PROJECT_DIR/logs"
exec >> "$LOG_FILE" 2>&1
log() { echo "[$(date '+%F %T')] [kiosk] $*"; }

# --- Port: tek doğru kaynak config.yaml -----------------------------------
# Burada sabitlenmez; kullanıcı Ayarlar'dan portu değiştirirse kiosk da
# doğru adrese gider. Config okunamazsa sessizce yanlış porta gitmektense
# systemd biriminde de yazılı olan 8080'e düşülür.
PORT=8080
if [ -x "$VENV_PYTHON" ] && [ -f "$CONFIG_FILE" ]; then
    export BACKEND_DIR CONFIG_FILE
    detected_port="$("$VENV_PYTHON" - <<'PYEOF' 2>/dev/null || true
import os, sys
sys.path.insert(0, os.environ["BACKEND_DIR"])
from app.core.config import load_config
print(load_config(os.environ["CONFIG_FILE"]).server.port)
PYEOF
)"
    [[ "$detected_port" =~ ^[0-9]+$ ]] && PORT="$detected_port"
fi

URL="http://127.0.0.1:$PORT/"
log "Dashboard bekleniyor: $URL (azami ${MAX_WAIT_SECONDS}s)"

# --- Servis hazır olana kadar bekle ---------------------------------------
ready=0
for _ in $(seq "$MAX_WAIT_SECONDS"); do
    if curl -fsS -m 2 "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done

if [ "$ready" -ne 1 ]; then
    # Yine de tarayıcıyı aç: kullanıcı ekranda bir hata görsün, sessizce
    # hiçbir şey olmamasından iyidir. Teşhis için servis durumunu logla.
    log "UYARI: servis ${MAX_WAIT_SECONDS}s içinde yanıt vermedi."
    log "  systemctl status jetson-camera-dashboard.service --no-pager (özet):"
    systemctl status jetson-camera-dashboard.service --no-pager --lines=5 || true
else
    log "Servis hazır."
fi

# --- Kiosk'a özel Firefox profili ----------------------------------------
# Kullanıcının normal tarayıcı profiline DOKUNULMAZ. Ayrı profilin sebebi:
# profilsiz ilk açılışta Firefox karşılama/veri-toplama sayfalarını açar ve
# bunlar kiosk modunda dashboard'un üstünü kapatır. Ayrıca elektrik
# kesintisinden sonra "oturumu geri yükle" ekranı çıkmasın istiyoruz -
# cihaz her açılışta temiz bir dashboard göstermeli.
FF_PROFILE="$HOME/.local/share/jetson-camera-kiosk/firefox"
if [ ! -d "$FF_PROFILE" ]; then
    log "Kiosk Firefox profili oluşturuluyor: $FF_PROFILE"
    mkdir -p "$FF_PROFILE"
fi
cat > "$FF_PROFILE/user.js" <<'PREFS'
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("datareporting.policy.firstRunURL", "");
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.tabs.warnOnClose", false);
# userChrome.css'in uygulanması için şart (Firefox 69+ varsayılan olarak kapalı).
user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);
# SİSTEM başlık çubuğunu kullan. Varsayılanda Firefox GNOME'da başlığı kendi
# çizer ve küçült/büyüt/kapat düğmelerini SEKME ŞERİDİNİN İÇİNE koyar -
# aşağıda sekme şeridini gizlediğimiz için o düğmeler de kaybolurdu.
# Bu tercih gerçek bir GNOME başlık çubuğu getirir; düğmeler sağ üstte,
# masaüstü ayarındaki (button-layout) yerleşimle çıkar.
user_pref("browser.tabs.inTitlebar", 0);
user_pref("browser.tabs.drawInTitlebar", false);
PREFS

# Sekme şeridini ve adres çubuğunu gizle. --kiosk KULLANMIYORUZ çünkü kiosk
# modu pencereyi çerçevesiz açar ve küçült/kapat düğmeleri kaybolur; burada
# istenen "normal pencere görünümü + sağ üstte düğmeler". Bu yüzden pencere
# dekorasyonu korunuyor, yalnızca tarayıcı arayüzü gizleniyor - ekranda
# sadece dashboard ve başlık çubuğu kalır.
mkdir -p "$FF_PROFILE/chrome"
cat > "$FF_PROFILE/chrome/userChrome.css" <<'CSS'
#TabsToolbar { visibility: collapse !important; }
#nav-bar     { visibility: collapse !important; }
CSS

# Pencere her açılışta ekranı kaplasın. Firefox'un pencere boyutunu/durumunu
# sakladığı yer xulstore.json'dır; wmctrl/xdotool bu makinede kurulu
# olmadığı için pencereyi sonradan büyütmek yerine Firefox'a doğrudan
# "maximized" başlamasını söylüyoruz. Her açılışta yazılıyor: cihaz
# önceki oturumda pencere küçültülmüş olsa bile temiz ve tam ekran açılmalı.
cat > "$FF_PROFILE/xulstore.json" <<'XUL'
{"chrome://browser/content/browser.xhtml":{"main-window":{"screenX":"0","screenY":"0","width":"1680","height":"1050","sizemode":"maximized"}}}
XUL

# --- Tarayıcıyı başlat ----------------------------------------------------
# SIRALAMA ÖNEMLİ: Firefox önce deneniyor çünkü bu makinede snap chromium
# ÇALIŞMIYOR - snap-confine "required permitted capability cap_dac_override
# not found" hatasıyla ölüyor (setuid biti yerinde, sorun snapd/Tegra
# çekirdeği tarafında). Firefox ise Mozilla apt deposundan gelen gerçek bir
# deb; sorunsuz açılıyor. Chromium yine de yedekte tutuluyor: snap ileride
# onarılırsa ya da betik başka bir Jetson'a kopyalanırsa işe yarar.
#
# exec KULLANILMIYOR: ilk tarayıcı açılmazsa ikinciye düşebilmek için
# süreci izlememiz gerekiyor.
launch() {
    local bin="$1"
    shift
    log "Deneniyor: $bin"
    "$bin" "$@" &
    local pid=$!
    # 5 sn: başlatma hataları (snap-confine gibi) anında ölür; gerçekten
    # açılan bir tarayıcı bu süreyi rahat geçer.
    sleep 5
    if kill -0 "$pid" 2>/dev/null; then
        log "Açıldı (PID $pid): $bin"
        wait "$pid" || true
        return 0
    fi
    log "BAŞARISIZ (5 sn içinde kapandı): $bin"
    return 1
}

if [ -x /usr/bin/firefox ]; then
    # --no-remote: çalışan başka bir Firefox varsa ona sekme eklemek yerine
    #              kendi penceresini açsın (kiosk ayrı profil kullanıyor).
    launch /usr/bin/firefox --no-remote --profile "$FF_PROFILE" --new-window "$URL" && exit 0
fi

if [ -x /snap/bin/chromium ]; then
    launch /snap/bin/chromium \
        --app="$URL" \
        --start-maximized \
        --no-first-run \
        --no-default-browser-check \
        --disable-session-crashed-bubble \
        --disable-infobars && exit 0
fi

log "HATA: Çalışan bir tarayıcı bulunamadı (firefox/chromium)."
exit 1
