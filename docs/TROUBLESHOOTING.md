# Sorun Giderme

## Servis başlamıyor

```bash
sudo systemctl status jetson-camera-dashboard.service
sudo journalctl -u jetson-camera-dashboard.service -n 100 --no-pager
```

Yaygın nedenler:

* **`/mnt/recordings` mount değil**: `RequiresMountsFor` servisin
  başlamasını engeller. `mountpoint /mnt/recordings` ile doğrulayın,
  gerekirse `sudo mount -a`.
* **`.env` eksik veya `JETSON_CAMERA_SECRET_KEY` boş**: `EnvConfigError`
  ile başlangıçta çöker (kasıtlı - sessizce varsayılana düşmez). `.env`
  dosyasının var olduğunu ve `install.sh` tarafından doğru üretildiğini
  kontrol edin.
* **`config.yaml` geçersiz**: `ConfigError` ile başlangıçta çöker, hata
  mesajı hangi alanın geçersiz olduğunu söyler. `config/config.example.yaml`
  ile karşılaştırın.
* **Migration hatası**: `alembic upgrade head` manuel çalıştırıp tam
  hatayı görün: `cd migrations && JETSON_CAMERA_CONFIG=<path> ../backend/.venv/bin/alembic upgrade head`

## Kamera bulunamadı / "CAMERA_NOT_AVAILABLE"

```bash
./scripts/diagnose-camera.sh
```

Kontrol listesi:

1. `ls -l /dev/video0` var mı?
2. `sudo systemctl status nvargus-daemon` aktif mi? Değilse:
   `sudo systemctl restart nvargus-daemon`
3. Kamera başka bir işlem tarafından mı kullanılıyor? (Argus tek
   proses kısıtlaması - `ps aux | grep gst-launch` ile orphan bir
   süreç kontrol edin, varsa `kill -INT <pid>` ile durdurun.)
4. Kernel modülü yüklü mü? `lsmod | grep imx519`

## Kayıt başlıyor ama hemen "PIPELINE_START_FAILED" ile bitiyor

Loglardaki `output` alanına bakın (`sudo journalctl -u
jetson-camera-dashboard.service | grep pipeline`). Sık görülen:

* `Failed to create CaptureSession` → kamerayı tutan başka bir Argus
  süreci var (yukarıdaki madde 3'e bakın).
* GStreamer element hatası → `gst-inspect-1.0 x264enc`,
  `gst-inspect-1.0 nvarguscamerasrc` ile ilgili plugin'in kurulu
  olduğunu doğrulayın.

## "STORAGE_NOT_MOUNTED" hatası

SSD çıkarılmış veya mount kopmuş demektir. Kayıt BİLEREK başlamaz (SD
karta yanlışlıkla yazmayı önlemek için). `./scripts/check-storage.sh`
ile durumu kontrol edin, `sudo mount -a` ile yeniden mount edin.

## Video oynatılamıyor / bozuk

* Kayıt normal şekilde durdurulduysa (`POST /api/recordings/stop`)
  dosya EOS ile finalize edilir ve bozuk olmamalıdır.
* Servis beklenmedik şekilde çöktüyse (örn. `sudo systemctl kill`)
  aktif kayıt finalize edilemeyebilir - dosya `corrupted` olarak
  işaretlenir (reconciler bunu `ffprobe` ile gerçekten doğrular).
* `corrupted` işaretli bir dosyayı `ffprobe <dosya>` ile manuel
  kontrol edin; genellikle moov atom eksikliğinden kaynaklanır (yarım
  kalmış yazma).

## Dashboard'a bağlanamıyorum

1. Servis çalışıyor mu? `curl http://127.0.0.1:8080/api/health`
   (cihazın kendisinde).
2. Güvenlik duvarı 8080'i engelliyor olabilir -
   `sudo ufw status verbose`.
3. Tailscale üzerinden bağlanıyorsanız `tailscale status` ile hem
   Jetson'ın hem istemci cihazın "Connected" olduğunu doğrulayın.
4. Yerel ağdansanız doğru IP'yi kullandığınızdan emin olun:
   `hostname -I`.

## Parolamı unuttum

Bkz. `docs/INSTALLATION.md` "Varsayılan kullanıcı ve parola değiştirme"
bölümündeki script.

## Disk doluyor / "STORAGE_INSUFFICIENT_SPACE"

Varsayılan davranış kaydı GÜVENLİ şekilde durdurmaktır
(`low_space_action: stop`). Otomatik eski kayıt silmeyi etkinleştirmek
için dashboard'un Ayarlar sekmesinden "Otomatik Eski Kayıt Silme"yi
açın veya `config.yaml`'da `retention.enabled: true` +
`retention.delete_oldest_files: true` ayarlayın.

## "Attempt to overwrite 'X' in LogRecord" hatası görürsem

Bu, geliştirme sırasında iki kez karşılaşılan ve düzeltilen bir bug
sınıfıydı (bkz. `PROJECT_STATE.md` Faz 2/5). Eğer kod değiştirip yeni
bir `logger.info(..., extra={...})` çağrısı eklerseniz, `extra`
sözlüğündeki anahtar isimlerinin şu rezerve `LogRecord` alanlarıyla
ÇAKIŞMADIĞINDAN emin olun: `args, created, exc_info, exc_text,
filename, funcName, levelname, levelno, module, msecs, msg, name,
pathname, process, processName, relativeCreated, stack_info, thread,
threadName`. Bunun yerine `component`, `pipeline_args` gibi rezerve
olmayan isimler kullanın.

## Genel tanılama komutları

```bash
./scripts/diagnose-camera.sh      # kamera/GStreamer/I2C durumu
./scripts/check-storage.sh        # SSD mount/disk/izin durumu
./scripts/test-recording.sh 10    # gerçek test kaydı
sudo journalctl -u jetson-camera-dashboard.service -f   # canlı log
sudo systemctl status jetson-camera-dashboard.service nvargus-daemon
```
