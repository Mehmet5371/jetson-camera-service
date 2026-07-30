# Mimari ve Geliştirici Rehberi

> **Bu dosyanın amacı:** Yeni bir geliştirme oturumuna sıfırdan başlayan
> birinin (insan veya AI) projeyi anlayıp kaldığı yerden devam
> edebilmesi. Kod gerçeklerine güven, tahmine değil - donanımla ilgili
> her karar gerçek komut çıktısıyla doğrulanmıştır.

## 0. Hızlı başlangıç (yeni oturum için)

- **Proje kök dizini:** `/mnt/recordings/jetson-camera-service/`
- **Faz ilerlemesi ve doğrulanmış bulgular:** `/mnt/recordings/PROJECT_STATE.md` (her önemli değişiklikte güncellenir - ÖNCE BUNU OKU)
- **Backend'i çalıştır (tek komut):** `./scripts/start-dashboard.sh --background`
  (ön koşul kontrolleri + sağlık doğrulaması yapar; ön plan için bayrağı kaldır)
- **Elle, adım adım (aynı iş):**
  ```bash
  cd /mnt/recordings/jetson-camera-service/backend
  source .venv/bin/activate
  export JETSON_CAMERA_CONFIG=/mnt/recordings/jetson-camera-service/config/config.yaml
  export JETSON_CAMERA_ENV_FILE=/mnt/recordings/jetson-camera-service/.env
  python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
  ```
  Dashboard: `http://<jetson-ip>:8080`, giriş `admin` / `123`
  (bu bir DEV parolası; `install.sh` üretimde kendi rastgele parolasını üretir)
- **Testler:**
  ```bash
  python -m pytest tests/ -q                  # tümü (110 test, kamera gerekir)
  python -m pytest tests/ -m "not hardware"   # kamerasız (84 test, mock backend)
  python -m pytest tests/ -m hardware         # yalnızca gerçek donanım (26 test)
  ```
- **Kurulum HENÜZ YAPILMADI:** `install.sh` çalıştırılmadı (kullanıcı incelemek istedi),
  Tailscale kurulmadı. Servis şu an yalnızca elle uvicorn ile çalışıyor.

## 1. Ne yapıyor

NVIDIA Jetson Orin Nano + Arducam IMX519 kamerayla, tarayıcıdan uzaktan
yönetilen video kayıt sistemi. Amaç: görüntü işleme modeli eğitimi için
veri toplamak. Kurulumdan sonra cihaza fiziksel erişim OLMAYACAK - her
şey web dashboard'undan.

## 2. Donanım gerçekleri (gerçek komutlarla doğrulandı - KRİTİK)

Bunlar `scripts/diagnose-camera.sh` ile yeniden doğrulanabilir. Farklı bir
donanımda bu varsayımlar GEÇERSİZDİR.

1. **Yalnızca Argus çalışır.** Sensör ham Bayer (`RG10`) sunuyor; `v4l2src`
   ISP olmadan kullanılamaz. `nvarguscamerasrc` (Argus) kullanılır.
2. **Donanımsal video encoder YOK.** Orin Nano SKU'sunda NVENC yok
   (`nvv4l2h264enc` mevcut değil). Yazılım encoder `x264enc` kullanılır.
3. **Argus tek oturumludur.** Kameraya aynı anda TEK proses erişebilir.
   Bu, önizleme+kayıt mimarisinin temel kısıtı (bkz. böl. 5).
4. **Odak motoru (VCM) yalnızca Argus akışı aktifken I2C'ye yanıt verir.**
   I2C bus 10, adres `0x0c` (sensörün 0x1a'sından ayrı). Akış kapalıyken
   `i2cset` "Write failed" verir. Ayrıca akış YENİDEN başladığında lens
   fiziksel olarak dinlenme konumuna (bulanık) döner - bu yüzden odak
   değeri her akış başında YENİDEN uygulanmalıdır (bkz. böl. 6).
5. **Odak değeri aralığı 0-1000** (Arducam `Focuser.py` OPT_FOCUS ile aynı).
6. **Depolama:** OS SD kartta (`/`), TÜM proje verisi (kod, venv, DB,
   videolar) SSD'de (`/mnt/recordings`, ext4, UUID
   `8214c9e1-8b27-4178-a294-377197d3fb05`). SSD mount değilse kayıt
   başlamaz (SD'ye yanlış yazmayı önlemek için).
7. **Sensör modları:** 4656x3496@9, 3840x2160@17, 1920x1080@60, 1280x720@120.
8. **sudo:** `emre` kullanıcısına `/etc/sudoers.d/jetson-camera-service`
   ile dar kapsamlı NOPASSWD verildi (apt, systemctl, useradd, mkdir,
   chown, ... - `rm`/`su` HARİÇ).

## 3. Teknoloji yığını

- Backend: Python 3.10, FastAPI, Uvicorn, Pydantic v2, SQLModel/SQLAlchemy,
  Alembic (migration), SQLite. Kayıt: GStreamer subprocess (`gst-launch-1.0`).
  Odak süpürme keskinlik analizi: `opencv-python-headless` (yalnızca JPEG
  çözmek için - GStreamer entegrasyonu GEREKMEZ).
- Frontend: sade HTML/CSS/JS (build adımı YOK), FastAPI `StaticFiles` sunar.
- Docker YOK (CSI kamera erişimini zorlaştırır). systemd servisi (Faz 7).

## 4. Dizin yapısı

```
/mnt/recordings/jetson-camera-service/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI giriş, lifespan (DI kurulumu), route mount
│   │   ├── api/               # REST + WebSocket uç noktaları
│   │   │   ├── deps.py        #   paylaşılan DI (config, db, managers, auth guard)
│   │   │   ├── auth.py health.py status.py camera.py recordings.py
│   │   │   ├── storage.py settings.py logs.py service.py
│   │   ├── camera/
│   │   │   ├── detector.py    # backend algılama (argus/v4l2/mock/auto)
│   │   │   ├── focus.py       # VCM odak I2C kontrolü (paylaşılan FocusController)
│   │   │   ├── autofocus.py   # kontrast tabanlı süpürme (tam aralık + yerel arama)
│   │   │   ├── continuous_focus.py  # AutofocusSupervisor: sürekli ölç/gerektiğinde odakla
│   │   │   ├── mjpeg.py       # MjpegBroadcaster + MjpegPipeReader (PAYLAŞILAN)
│   │   │   └── preview.py     # PreviewManager (kayıt YOKKEN canlı görüntü)
│   │   ├── recording/
│   │   │   ├── pipeline.py    # GStreamer argüman listesi kurucuları
│   │   │   ├── manager.py     # RecordingManager (state machine, tee, odak)
│   │   │   └── state.py       # kayıt durum makinesi
│   │   ├── storage/
│   │   │   ├── validator.py   # mount/disk/yazma kontrolü
│   │   │   ├── reconciler.py  # dosya<->DB senkronizasyonu (ffprobe)
│   │   │   └── retention.py   # eski kayıt silme politikası
│   │   ├── models/            # recording.py, user.py (SQLModel tabloları)
│   │   ├── security/          # passwords(Argon2), tokens(JWT), auth(CSRF), rate_limit
│   │   ├── services/          # user_seed.py
│   │   └── core/              # config, db, env, errors, logging, shell, system_info
│   ├── tests/                 # 97 test (pytest -m hardware / "not hardware")
│   ├── requirements.txt  pyproject.toml  .venv/
├── frontend/                  # index.html, login.html, static/{css,js}
├── config/                    # config.example.yaml, config.yaml (Git'e girmez)
├── migrations/                # Alembic (env.py app config'inden URL okur)
├── systemd/                   # jetson-camera-dashboard.service
├── scripts/                   # diagnose-camera, test-recording, check-storage,
│                              #   backup/restore-config, uninstall, setup-tailscale,
│                              #   configure-firewall
├── docs/                      # bu dosya + INSTALLATION, CAMERA_SETUP, SECURITY,
│                              #   REMOTE_ACCESS, TROUBLESHOOTING, API, RECOVERY
├── install.sh  .env(.example)  .gitignore  README.md
```

## 5. Kamera oturumu modeli (EN ÖNEMLİ TASARIM)

Argus tek oturumlu olduğu için kameraya aynı anda tek pipeline erişir. İki
mod var, ikisi de **MJPEG canlı görüntü** üretir (paylaşılan `mjpeg.py`
altyapısı):

- **Boşta önizleme** (`PreviewManager`): kayıt YOKKEN. Pipeline:
  `nvarguscamerasrc ! ... ! jpegenc ! multipartmux ! fdsink fd=<N>`.
- **Kayıt** (`RecordingManager`): pipeline `tee` ile İKİYE ayrılır - bir
  dal MP4 (`splitmuxsink`), bir dal MJPEG önizleme (`fdsink fd=<N>`). Yani
  **kayıt sırasında da canlı görüntü var** (kullanıcının ilk raporundaki
  "kayıt başlayınca görüntü kayboluyor" sorunu böyle çözüldü).

**MJPEG neden ayrı bir pipe fd'sine (stdout DEĞİL):** `gst-launch-1.0`
kendi durum mesajlarını ("Setting pipeline to PLAYING", "Redistribute
latency") STDOUT'a yazıyor (gerçek testle doğrulandı) - MJPEG'i stdout'a
koyarsak bozulur. Bu yüzden `os.pipe()` ile ayrı bir fd açılıp subprocess'e
`pass_fds` ile geçiliyor, `fdsink fd=<gerçek_fd_numarası>` oraya yazıyor,
`MjpegPipeReader` (asyncio `connect_read_pipe`) okuyor. **Tuzak:**
`os.pipe()` rastgele fd numarası verir - `fdsink fd=`'ye o GERÇEK numara
verilmeli (sabit fd=3 DEĞİL).

**tee tuzağı:** tee dallarına AÇIK capsfilter (`video/x-raw,width=W,height=H`)
konulmazsa dallar birbirinin çözünürlüğünü sızdırır (kayıt 640x360'a
düşüyordu). Kayıt dalına explicit caps konuldu.

**Geçişler:** Kayıt başlarken aktif ayrı önizleme otomatik durdurulur
(`api/recordings.py`), dashboard `<img>`'i birleşik `/api/camera/live/stream`
uç noktasına bağlı kaldığı için canlı görüntü kesintisiz devam eder
(kaynak arka planda önizlemeden kayıt tee dalına geçer).

## 6. Odak davranışı (kullanıcının ikinci raporu: "kayıtta odak dağılıyor")

- **Tek paylaşılan `FocusController`** (`main.py`'de yaratılıp hem
  `RecordingManager` hem `PreviewManager`'a verilir). `current_value`
  bellekte tutulur - önizlemede autofocus/manuel ile bulunan değer, kayıt
  başladığında da erişilebilir.
- **Akış her başladığında** (`preview.start` ve kayıt `_launch_pipeline`):
  1. İlk MJPEG karesi beklenir = Argus akışı gerçekten başladı, I2C hazır.
  2. `focus_controller.mark_stream_restarted()` (çip sıfırlanmış olabilir,
     bir sonraki `set_focus` `initialize()`'ı tekrar çalıştırır).
  3. `focus_controller.reapply(manual_focus_value)`: bilinen son değeri
     (yoksa config varsayılanını) lens'e YENİDEN yazar. Akış restart'ında
     lens fiziksel olarak dinlenmeye döndüğü için bu şart - aksi halde
     kayıt bulanık başlıyordu.
- **Odak API'si** (`/api/camera/focus`) önizleme VEYA kayıt aktifken çalışır.
  `auto` modu, aktif kaynağın (önizleme veya kayıt tee dalı) en son
  MJPEG karesinden keskinlik ölçüp süpürme yapar.

### 6.1 Sürekli otomatik odak (`camera/continuous_focus.py`)

Kullanıcı butona basmadan çalışır: `AutofocusSupervisor` lifespan'da tek bir
asyncio task olarak başlar, canlı akış (önizleme VEYA kayıt) varken
`sample_interval_seconds`'de bir keskinlik ölçer. **Lens durmadan
SÜPÜRÜLMEZ** - bu, görüntüyü/kaydı periyodik olarak bulanıklaştırırdı;
"sürekli ölç, gerektiğinde odakla" modeli kullanılır:

1. **Tetikleme:** ölçüm, referansın `trigger_ratio` katının altına
   `consecutive_drops` kez ÜST ÜSTE düşerse. Referans, ölçümlerin EMA'sıdır
   (`reference_smoothing`) - "görülen en yüksek değer" DEĞİL (aşağıdaki
   ölçüme bakın). Tek karelik gürültü için son 3 ölçümün medyanı alınır.
2. **Düzeltme:** önce mevcut pozisyonun çevresinde yerel arama
   (±`fine_step`×`fine_range_steps`, ~2-3s). Tam süpürmeye yalnızca en iyi
   pozisyon yerel PENCERENİN KENARINDA çıkarsa yükseltilir (gerçek tepe
   pencere dışındadır).
3. **Doğrulama:** sonuç kabul edilmeden önce `verify_settle_seconds` beklenip
   yeniden ölçülür; odaklama görüntüyü belirgin şekilde kötüleştirdiyse lens
   eski pozisyonuna GERİ ALINIR.
4. `cooldown_seconds` iki odaklama arasında en kısa süreyi zorlar (hunting'i
   önler). Akış ilk kez başladığında `initial_sweep` ile bir kez tam süpürme
   yapılır - sistem müdahale olmadan net başlar.

**Bu tasarımı belirleyen iki ÖLÇÜM (gerçek donanım, 2026-07-30):**
- Büyük bir odak hareketinden sonra keskinlik ~2.4 saniye boyunca yükselmeye
  devam ediyor (Argus ISP pozlama/gürültü azaltma adaptasyonu). Süpürme
  sırasındaki 0.25s'lik beklemeyle alınan MUTLAK değerler bu yüzden
  güvenilmez - "doğrulama ölçümü + geri alma" adımı bu yüzden var.
- Aynı pozisyonda aynı sahnede keskinliğin mutlak değeri zamanla kayıyor
  (dakikalar arayla 21 ile 68 arası okundu). Referans olarak maksimum
  tutulursa bir gürültü tepesine kilitlenip sonsuz yanlış tetikleme yapıyor -
  bu yüzden EMA. Tepe noktasının POZİSYONU ise güvenilir: 0.25s / 0.5s /
  1.2s bekleme ile yapılan üç süpürme de aynı pozisyonu (200) buldu.

**Odak yazmalarının tek kapısı:** manuel slider, "Şimdi Odakla" butonu ve bu
döngü `AutofocusSupervisor`'ın tek `asyncio.Lock`'unu paylaşır - iki odaklama
işlemi asla I2C üzerinde birbirine girmez. Manuel ayar sürekli odağı
`manual_override_seconds` kadar duraklatır; duraklatma bitince (veya şalter
yeniden açılınca) durum "yeni akış" gibi ele alınır - çünkü tek karelik bir
ölçümden "bu görüntü bulanık" sonucu ÇIKARILAMAZ (kontrast tabanlı AF'nin
doğası), o yüzden bulanık bir kareyi referans sanıp sonsuza kadar bulanık
kalmamak için gerekiyorsa yeniden odaklanır.

## 7. Kayıt yaşam döngüsü (özet)

`start` → `asyncio.Lock` → depolama doğrula → kamera algıla → MJPEG pipe
kur → `gst-launch` (tee) başlat (`pass_fds`) → ilk kareyi bekle → odağı
yeniden uygula → state=RECORDING + kilit dosyası.
`stop` → `SIGINT` (gst-launch `-e` bunu EOS'a çevirir, MP4 finalize olur;
20s'de SIGKILL'e escalate) → MJPEG reader durdur → dosyayı `ffprobe` ile
doğrula, DB'ye işle (completed/corrupted). State machine:
`idle→starting→recording→stopping→finalizing→idle`, hatada `error→idle`.

## 8. Standartlar / kurallar

- Standart hata formatı: `{"success": false, "error": {code, message, details}}`
  (tüm katmanlar `AppError` fırlatır, `main.py` çevirir).
- **`shell=True` YASAK.** Tüm subprocess çağrıları argüman listesiyle
  (`core/shell.py` veya doğrudan `asyncio.create_subprocess_exec`).
  GStreamer/i2cset komutları yalnızca doğrulanmış config alanlarından kurulur.
- **JSON loglama tuzağı:** `logger.*(..., extra={...})` içinde `extra`
  anahtarları rezerve `LogRecord` alanlarıyla (`message, args, module,
  name, ...`) ÇAKIŞAMAZ - `component`, `error_message`, `focus_value` gibi
  isimler kullanın. Bu bug 3 kez tekrarlandı, dikkat.
- Type hint zorunlu, modüller tek dev dosyada toplanmaz.

## 9. Bilinçli kapsam dışı / gelecek işler

- `install.sh` ve Tailscale kurulumu gerçek sistemde çalıştırılmadı
  (kullanıcı onayı bekliyor) - 22 kabul kriterinden 6'sı bu yüzden
  "gerçek testle doğrulanmadı" durumunda (bkz. PROJECT_STATE.md böl. 22).
- Sürekli otomatik odak VAR (bkz. böl. 6.1); yapılmayan: yüz/nesne takibi ya
  da ROI seçimi (odak her zaman merkez %20'lik bölgeye göre ölçülür).
- Şartname böl. 26'daki opsiyonel modüller (snapshot, zamanlanmış kayıt,
  webhook, SFTP yedekleme, çoklu kullanıcı, audit log) yapılmadı.
- Config dashboard'dan değişince `save_config` YAML'ı yeniden yazar ve
  YORUMLARI siler (PyYAML sınırı) - `config.example.yaml` yorumlu referans
  olarak kalır.

## 10. Ortam notları (bu cihaza özel)

- packagekit maskelendi (dpkg kilit sorunu). Gerekirse
  `sudo systemctl unmask packagekit`.
- Firefox snap DEĞİL .deb (snap kullanma). Chromium/geckodriver snap
  sandbox izinleri bu ortamda headless otomasyonu engelliyor - dashboard
  görsel doğrulaması `.deb` Firefox `--headless --screenshot` ile yapıldı.
- `~/Jetson_IMX519_Focus_Example/` Arducam'ın resmi odak örneği (Focuser.py,
  Autofocus.py) - referans alındı.
