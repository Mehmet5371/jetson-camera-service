#!/usr/bin/env bash
#
# SSD mount durumunu ve disk kullanımını raporlar. Servis çalışmasa bile
# kullanılabilir - kurulum sonrası veya sorun giderme için.
#
# Çalıştıran kullanıcı: herhangi bir kullanıcı.
#
# Kullanım: ./scripts/check-storage.sh

set -uo pipefail

SSD_MOUNT="/mnt/recordings"

echo "=== Mount durumu ==="
if mountpoint -q "$SSD_MOUNT"; then
    echo "OK: $SSD_MOUNT mount edilmiş."
else
    echo "HATA: $SSD_MOUNT bir mount noktası DEĞİL! Kayıt başlatılamaz."
fi

echo ""
echo "=== findmnt ==="
findmnt "$SSD_MOUNT" 2>&1

echo ""
echo "=== Disk kullanımı ==="
df -h "$SSD_MOUNT" 2>&1

echo ""
echo "=== Yazma testi ==="
TEST_FILE="$SSD_MOUNT/.check_storage_write_test_$$"
if echo "test" > "$TEST_FILE" 2>/dev/null; then
    rm -f "$TEST_FILE"
    echo "OK: $SSD_MOUNT yazılabilir."
else
    echo "HATA: $SSD_MOUNT üzerine yazılamıyor (izin sorunu olabilir)."
fi

echo ""
echo "=== Beklenen alt dizinler ==="
for dir in media/videos media/photos db run; do
    path="$SSD_MOUNT/$dir"
    if [[ -d "$path" ]]; then
        owner="$(stat -c '%U:%G' "$path" 2>/dev/null)"
        echo "OK: $path (sahip: $owner)"
    else
        echo "EKSİK: $path (./install.sh çalıştırılmamış olabilir)"
    fi
done

echo ""
echo "=== Kayıtlı video sayısı ve toplam boyut ==="
if [[ -d "$SSD_MOUNT/media/videos" ]]; then
    count=$(find "$SSD_MOUNT/media/videos" -name '*.mp4' 2>/dev/null | wc -l)
    size=$(du -sh "$SSD_MOUNT/media/videos" 2>/dev/null | cut -f1)
    echo "$count dosya, toplam $size"
fi
