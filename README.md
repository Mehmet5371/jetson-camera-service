# jetson-camera-service

NVIDIA Jetson Orin Nano üzerinde çalışan, tarayıcıdan uzaktan yönetilen
kamera veri toplama servisi. Amaç: görüntü işleme modeli eğitimi için
video verisi toplamak. Kurulum sonrası cihaza fiziksel erişim
olmayacağı varsayımıyla tasarlandı - tüm işlemler (kayıt başlat/durdur,
dosya yönetimi, servis restart, ayarlar) bir web dashboard'undan yapılır.

## Sistem gereksinimleri

* NVIDIA Jetson Orin Nano Developer Kit, JetPack 6.2.1 (L4T R36.4.4),
  Ubuntu 22.04 aarch64
* Kamera: Arducam B0371 (Sony IMX519, 16MP, autofocus, CSI)
* NVMe SSD, `/mnt/recordings` olarak mount edilmiş (kod, veritabanı ve
  kayıtların tamamı burada tutulur - SD kart yalnızca işletim sistemi
  içindir)
* İnternet bağlantısı (kurulum sırasında)

**Doğrulanmış kritik donanım gerçeği**: bu Jetson Orin Nano SKU'sunda
donanımsal video encoder (NVENC) YOK; servis yazılımsal `x264enc`
kullanır. Kamera yalnızca Argus (`nvarguscamerasrc`) üzerinden
erişilebilir, v4l2 çalışmaz (ham Bayer format). Ayrıntılar:
[`docs/CAMERA_SETUP.md`](docs/CAMERA_SETUP.md).

## Hızlı kurulum

```bash
cd /mnt/recordings/jetson-camera-service
./install.sh
```

İdempotenttir, mevcut config/veriyi asla silmez. Ayrıntılar:
[`docs/INSTALLATION.md`](docs/INSTALLATION.md).

## Kamera testi

```bash
./scripts/diagnose-camera.sh     # donanım/GStreamer/I2C tanılama
./scripts/test-recording.sh 10   # gerçek 10 saniyelik test kaydı + ffprobe doğrulama
```

## Otomatik odak

Sürekli otomatik odak varsayılan olarak AÇIKTIR: canlı görüntü (önizleme
veya kayıt) varken keskinlik sürekli ölçülür, odak kaçtığında hiçbir butona
basmadan kendiliğinden yeniden odaklanır - kayıt sırasında da. Dashboard'un
"Canlı Önizleme & Odak" kartındaki "Sürekli Otomatik Odak" anahtarıyla
kapatılabilir; kalıcı ayarlar `config.yaml`'daki
`camera.continuous_autofocus` bloğundadır. Ayrıntı ve bu tasarımı belirleyen
donanım ölçümleri: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) böl. 6.1.

## Servis başlatma ve durdurma

**Kurulum yapılmadıysa (systemd servisi yok) - tek komut:**

```bash
/mnt/recordings/jetson-camera-service/scripts/start-dashboard.sh --background
```

Ön koşulları (SSD mount, venv, config.yaml, .env, port boş mu) kontrol eder,
portu `config.yaml`'dan okur, servisi ayağa kaldırır ve sağlık kontrolünden
geçtiğini doğrular. `--background` verilmezse ön planda çalışır (Ctrl+C
durdurur, aktif kayıt SIGINT ile düzgün finalize edilir).
Durdurmak için: `pkill -f 'uvicorn app.main:app'`

**`install.sh` çalıştırıldıktan sonra (systemd):**

```bash
sudo systemctl start|stop|restart|status jetson-camera-dashboard.service
```

## Dashboard adresi

```
http://<jetson-ip>:8080        # yerel ağdan
http://<tailscale-ip>:8080     # Tailscale kurulduktan sonra
```

## Tailscale ile uzaktan erişim

Port yönlendirme KULLANILMAZ. Bkz.
[`docs/REMOTE_ACCESS.md`](docs/REMOTE_ACCESS.md):

```bash
./scripts/setup-tailscale.sh       # Tailscale kurulumu + bağlantı
./scripts/configure-firewall.sh    # dashboard portunu yalnızca Tailscale/LAN'a kısıtlar
```

## Varsayılan kullanıcı ve parola değiştirme

`install.sh` ilk çalıştırmada rastgele bir admin parolası üretip
terminale BİR KEZ yazdırır. İlk girişte parola değişimi zorunludur.
Parolayı unutursanız: [`docs/INSTALLATION.md`](docs/INSTALLATION.md#varsayılan-kullanıcı-ve-parola-değiştirme).

## Log görüntüleme

```bash
sudo journalctl -u jetson-camera-dashboard.service -f
```

Veya dashboard'un Loglar sekmesinden.

## Güncelleme

```bash
git pull   # veya yeni dosyaları kopyalayın
./install.sh   # idempotent
```

## Yedekleme

```bash
./scripts/backup-config.sh
```

## Sistem kurtarma

Bkz. [`docs/RECOVERY.md`](docs/RECOVERY.md).

## Sık karşılaşılan hatalar

Bkz. [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

## Geliştirme ortamı

```bash
cd /mnt/recordings/jetson-camera-service/backend
source .venv/bin/activate
export JETSON_CAMERA_CONFIG=/mnt/recordings/jetson-camera-service/config/config.yaml
export JETSON_CAMERA_ENV_FILE=/mnt/recordings/jetson-camera-service/.env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

```bash
curl -s http://127.0.0.1:8080/api/health
# {"success":true,"data":{"status":"ok","service_version":"0.1.0"}}
```

## Testler

```bash
cd /mnt/recordings/jetson-camera-service/backend
source .venv/bin/activate
python -m pytest tests/ -v                  # tümü (110 test, kamera gerekir)
python -m pytest tests/ -m "not hardware"   # kamerasız (84 test, mock backend dahil)
python -m pytest tests/ -m hardware         # yalnızca gerçek donanım (26 test)
```

## Dokümantasyon

* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — bileşenler, veri akışı, tasarım kararları
* [`docs/INSTALLATION.md`](docs/INSTALLATION.md) — kurulum, güncelleme, yedekleme
* [`docs/CAMERA_SETUP.md`](docs/CAMERA_SETUP.md) — doğrulanmış donanım gerçekleri
* [`docs/REMOTE_ACCESS.md`](docs/REMOTE_ACCESS.md) — Tailscale, güvenlik duvarı
* [`docs/SECURITY.md`](docs/SECURITY.md) — kimlik doğrulama, gizli bilgiler, ağ güvenliği
* [`docs/API.md`](docs/API.md) — REST/WebSocket uç noktaları (canlı: `/docs`)
* [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) — sık karşılaşılan hatalar
* [`docs/RECOVERY.md`](docs/RECOVERY.md) — felaket kurtarma senaryoları

## Proje durumu

Faz ilerlemesi, doğrulanmış teknik bulgular ve karşılaşılıp düzeltilen
gerçek bug'lar için bkz. `/mnt/recordings/PROJECT_STATE.md`.
