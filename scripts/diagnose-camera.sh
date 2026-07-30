#!/usr/bin/env bash
#
# Kamera donanım/yazılım yığınını tanılar. Servisi durdurmaz, kamerayı
# AÇMAZ (yalnızca sorgular) - çalışan bir kayıtla çakışmaz.
#
# Çalıştıran kullanıcı: herhangi bir kullanıcı (bazı komutlar sudo
# gerektirebilir, script bunu kendisi belirtir).
#
# Kullanım: ./scripts/diagnose-camera.sh

set -uo pipefail

section() { echo ""; echo "=== $* ==="; }

section "Video cihazları (/dev/video*)"
ls -l /dev/video* 2>&1

section "v4l2-ctl --list-devices"
v4l2-ctl --list-devices 2>&1

section "v4l2-ctl --list-formats-ext (/dev/video0)"
v4l2-ctl --device /dev/video0 --list-formats-ext 2>&1

section "GStreamer: nvarguscamerasrc"
gst-inspect-1.0 nvarguscamerasrc 2>&1 | head -20

section "GStreamer: v4l2src"
gst-inspect-1.0 v4l2src 2>&1 | head -10

section "GStreamer: donanım/yazılım H.264 encoder"
if gst-inspect-1.0 nvv4l2h264enc &>/dev/null; then
    echo "nvv4l2h264enc (DONANIM) mevcut."
else
    echo "nvv4l2h264enc mevcut DEĞİL - bu SKU'da donanım video encoder yok (bkz. PROJECT_STATE.md)."
fi
if gst-inspect-1.0 x264enc &>/dev/null; then
    echo "x264enc (YAZILIM) mevcut - bu servis bunu kullanıyor."
else
    echo "UYARI: x264enc de mevcut değil, kayıt çalışmaz!"
fi

section "nvargus-daemon servis durumu"
systemctl status nvargus-daemon --no-pager 2>&1 | head -10

section "Media controller topolojisi"
media-ctl -d /dev/media0 -p 2>&1 | head -40

section "I2C bus 10 (sensör 0x1a, odak motoru 0x0c - yalnızca akış aktifken yanıt verir)"
i2cdetect -y -r 10 2>&1

section "Kernel modülleri"
lsmod | grep -iE 'imx519|tegra_camera' 2>&1

section "dmesg (imx519 ile ilgili son satırlar)"
dmesg 2>&1 | grep -i imx519 | tail -20 || echo "(dmesg okunamadı veya sonuç yok - sudo gerekebilir)"

echo ""
echo "Tanılama tamamlandı."
