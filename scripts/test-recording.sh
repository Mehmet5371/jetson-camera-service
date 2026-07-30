#!/usr/bin/env bash
#
# Servisi hiç çalıştırmadan, gerçek üretim kodunun (kamera algılama +
# pipeline kurucu) kullandığı AYNI mantıkla kısa bir test kaydı alır ve
# ffprobe ile doğrular. Servisi durdurmaya gerek yoktur AMA aktif bir
# kayıt varsa Argus tek-oturum kısıtlaması nedeniyle bu script başarısız
# olur (bu beklenen davranıştır).
#
# Çalıştıran kullanıcı: video ve i2c gruplarında olan herhangi bir
# kullanıcı (kurulumdan sonra jetcam, geliştirme sırasında emre).
#
# Kullanım: ./scripts/test-recording.sh [süre_saniye]

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DURATION="${1:-10}"
VENV_PYTHON="$PROJECT_ROOT/backend/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "HATA: venv bulunamadı ($VENV_PYTHON). Önce ./install.sh çalıştırın." >&2
    exit 1
fi

export JETSON_CAMERA_CONFIG="${JETSON_CAMERA_CONFIG:-$PROJECT_ROOT/config/config.yaml}"

cd "$PROJECT_ROOT/backend"
"$VENV_PYTHON" - "$DURATION" <<'PYEOF'
import subprocess
import sys
import time
from pathlib import Path

from app.camera.detector import detect_camera_backend
from app.core.config import load_config
from app.recording.pipeline import build_pipeline_args

duration = int(sys.argv[1])
config = load_config()

print(f"Kamera algılanıyor (backend={config.camera.backend})...")
backend_info = detect_camera_backend(config.camera)
print(f"  -> {backend_info.backend} ({backend_info.source_element})")

test_dir = Path(config.storage.mount_path) / "run" / "manual_test"
test_dir.mkdir(parents=True, exist_ok=True)
base_filename = f"manual_test_{int(time.time())}"

# Test kaydı segment ayarından bağımsız tek dosya olsun.
test_config = config.model_copy(
    update={"recording": config.recording.model_copy(update={"segment_duration_minutes": 0})}
)
args = build_pipeline_args(test_config, backend_info, test_dir, base_filename)
print("Pipeline:", " ".join(args))

print(f"{duration} saniyelik test kaydı başlıyor...")
process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
time.sleep(2)
if process.poll() is not None:
    output = process.stdout.read().decode(errors="replace") if process.stdout else ""
    print("HATA: pipeline başlamadan sonlandı:\n", output[-2000:])
    sys.exit(1)

time.sleep(max(0, duration - 2))

import signal

process.send_signal(signal.SIGINT)
try:
    process.wait(timeout=20)
except subprocess.TimeoutExpired:
    print("UYARI: SIGINT sonrası kapanmadı, SIGKILL uygulanıyor (dosya bozuk olabilir).")
    process.kill()
    process.wait()

output_file = test_dir / f"{base_filename}.{config.recording.container}"
if not output_file.exists():
    print(f"HATA: çıktı dosyası oluşmadı: {output_file}")
    sys.exit(1)

print(f"Dosya oluştu: {output_file} ({output_file.stat().st_size} bytes)")
probe = subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
     "-show_entries", "stream=codec_name,width,height",
     "-of", "default=noprint_wrappers=1", str(output_file)],
    capture_output=True, text=True,
)
if probe.returncode != 0:
    print("HATA: ffprobe dosyayı doğrulayamadı:\n", probe.stderr)
    sys.exit(1)

print("ffprobe sonucu:")
print(probe.stdout)
output_file.unlink()
print("Test dosyası temizlendi. TEST BAŞARILI.")
PYEOF
