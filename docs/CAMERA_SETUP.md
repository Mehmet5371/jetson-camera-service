# Kamera Kurulumu ve Doğrulanmış Donanım Gerçekleri

Bu belge, Faz 1-3'te gerçek komutlarla doğrulanan bulguları özetler.
Farklı bir kamera/Jetson kombinasyonu kullanıyorsanız bu varsayımların
GEÇERLİ OLMADIĞINI unutmayın - `./scripts/diagnose-camera.sh` ile kendi
donanımınızı yeniden doğrulayın.

## Donanım

* **Jetson**: Orin Nano Developer Kit, JetPack 6.2.1, L4T R36.4.4
* **Kamera**: Arducam B0371, Sony IMX519 sensör, 16MP, otomatik odak, CSI
* **Bağlantı**: CAM0 portu → I2C bus 10

## Doğrulanmış gerçekler

### 1. Yalnızca Argus çalışır, v4l2 ÇALIŞMAZ

Sensör ham Bayer formatı (`RG10`, 10-bit) sunar; ISP (Image Signal
Processor) olmadan bu format doğrudan encode edilemez. `nvarguscamerasrc`
Argus/ISP üzerinden çalışır ve ÇALIŞIR; `v4l2src` ham Bayer'i işleyemez.
Servis bunu `camera.backend: auto` ile otomatik tespit eder;
`v4l2` açıkça seçilirse net bir hata döner (`CAMERA_BACKEND_UNSUPPORTED`).

```bash
v4l2-ctl --device /dev/video0 --list-formats-ext
# [0]: 'RG10' (10-bit Bayer RGRG/GBGB) - v4l2 ile kullanılamaz
```

### 2. Donanımsal video encoder YOK

Jetson Orin Nano (Orin NX/AGX Orin'in aksine) donanımsal video ENCODE
(NVENC) birimine sahip değildir - yalnızca decode (NVDEC) vardır. Bu
yüzden `nvv4l2h264enc` bu cihazda hiç mevcut değildir; servis yazılım
encoder (`x264enc`) kullanır. CPU yükü, `recording.speed_preset` ve
`recording.bitrate_bps` ile ayarlanabilir.

```bash
gst-inspect-1.0 nvv4l2h264enc   # "No such element or plugin"
gst-inspect-1.0 x264enc         # mevcut, servis bunu kullanıyor
```

### 3. Odak motoru yalnızca akış aktifken I2C'ye yanıt verir

Arducam VCM (voice coil motor) odak motoru sensörden (I2C adres `0x1a`,
kernel sürücüsüne bağlı) AYRI bir cihazdır (adres `0x0c`, bus 10).
**Kritik bulgu**: bu adres yalnızca bir Argus akışı aktifken I2C'ye yanıt
verir - akış kapalıyken `i2cset`/`i2cget` "Write failed" ile başarısız
olur. Bu, VCM'in güç hattının sensör akışıyla birlikte gate'lendiğini
gösteriyor. Servis bunu otomatik olarak doğru sırayla yapar: pipeline
başladıktan SONRA odak ayarlanır (bkz. `camera/focus.py`,
`recording/manager.py::_apply_focus_best_effort`).

```bash
# Akış kapalıyken (başarısız olması BEKLENİR):
i2cset -y 10 0x0c 0x02 0x00   # Error: Write failed

# Akış açıkken (gst-launch ile test kaydı sürerken) başarılı olur.
```

### 4. Sensör modları (sabit, donanım tarafından belirlenir)

| Çözünürlük | FPS |
|---|---|
| 4656x3496 | 9 |
| 3840x2160 | 17 |
| 1920x1080 | 60 |
| 1280x720 | 120 |

`config.yaml`'daki `camera.width`/`height`/`fps` bu modlardan birine
denk gelmelidir (Argus en yakın uygun modu otomatik seçer).

### 5. Kamera çöktüğünde/kilitlendiğinde

```bash
sudo systemctl restart nvargus-daemon
```

Dashboard'un "Kamera Servisini Yeniden Başlat" butonu (aktif kayıt
yokken) bunu otomatik yapar; backend `jetcam` kullanıcısına tanımlı DAR
kapsamlı bir NOPASSWD sudo kuralı kullanır (yalnızca bu tek komut, bkz.
`install.sh`).

### 6. Keskinlik ölçümünün zamansal davranışı (sürekli odağı belirleyen bulgu)

2026-07-30'da canlı önizleme üzerinde ölçüldü (merkez %20 ROI'de Laplacian
varyansı, ölçüm betiği: `docs/ARCHITECTURE.md` böl. 6.1):

* **Büyük bir odak hareketinden sonra keskinlik ~2.4 saniye boyunca
  yükselmeye devam ediyor** (Argus ISP'nin pozlama/gürültü azaltma
  adaptasyonu). Örnek (lens 900→175'e alındıktan sonra): 0.2s'de 3.5 →
  1.1s'de 6.7 → 1.9s'de 32.8 → 2.4s'de 45.2. Küçük adımlarda (50) böyle bir
  kuyruk YOK, ölçüm anında kararlı.
* **Mutlak keskinlik değeri zamanla kayıyor**: aynı sahnede aynı pozisyonda
  dakikalar arayla 21 ile 68 arası okundu. Bu yüzden "dakikalar önce ölçülen
  bir referansla bugünü karşılaştırmak" güvenilmez.
* **Tepe noktasının POZİSYONU güvenilir**: 0.25s, 0.5s ve 1.2s bekleme ile
  yapılan üç ayrı süpürme de aynı pozisyonu (200) buldu; eğri her üçünde de
  tek tepeli ve temiz. Süpürme beklemesinin 0.25s kalması bu yüzden güvenli.

Pratik sonuç: kontrast tabanlı odak, POZİSYON seçmekte iyi ama MUTLAK
keskinlik değerlerini eşik olarak kullanmakta kötüdür. Sürekli odak mantığı
buna göre kuruldu (EMA referans + odaklama sonrası doğrulama/geri alma).

## Fokus kalibrasyonu

Sürekli otomatik odak varsayılan olarak açıktır: canlı akış (önizleme veya
kayıt) varken keskinlik ölçülür, odak kaçtığında kendiliğinden yeniden
odaklanılır - butona basmak gerekmez (bkz. `docs/ARCHITECTURE.md` böl. 6.1
ve `config.example.yaml`'daki `camera.continuous_autofocus` bloğu).

`camera.autofocus: false` otomatik odağın ana şalteridir; kapatıldığında
`manual_focus_value` (0-1000, Arducam Focuser.py'nin `OPT_FOCUS` aralığıyla
aynı) kullanılır. Bu cihazda net görüntü veren değer sahneye göre değişiyor:
farklı oturumlarda otomatik odak 150-200 aralığını buldu (config'deki 250
yalnızca bir başlangıç varsayılanıdır). Elle kalibre etmek isterseniz:

```bash
# Akış aktifken (örn. bir test kaydı sürerken) farklı değerler deneyin:
i2cset -y 10 0x0c 0x00 0xXX
i2cset -y 10 0x0c 0x01 0xXX
```
