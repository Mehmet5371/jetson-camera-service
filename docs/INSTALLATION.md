# Kurulum

## Sistem gereksinimleri

* NVIDIA Jetson Orin Nano Developer Kit
* JetPack 6.2.1 (L4T R36.4.4), Ubuntu 22.04 aarch64
* Arducam B0371 (Sony IMX519) CSI kamera, doğru bağlanmış ve sürücüsü
  kurulu (`imx519` kernel modülü yüklü olmalı - `lsmod | grep imx519`)
* NVMe SSD, `/mnt/recordings` olarak mount edilmiş (bkz. aşağıda "SSD
  hazırlama")
* İnternet bağlantısı (apt paketleri ve Python bağımlılıkları için)
* sudo yetkisi olan bir kullanıcı hesabı

## SSD hazırlama

Bu proje kodun tamamını, veritabanını ve video kayıtlarını SSD'de tutar
- SD kart yalnızca işletim sistemi içindir. Kurulumdan ÖNCE SSD'nin
mount edildiğinden emin olun:

```bash
# Kullanıcı: sudo yetkisi olan kullanıcı
lsblk                              # NVMe SSD'yi tanımlayın (örn. nvme0n1)
sudo mkfs.ext4 /dev/nvme0n1p1      # yalnızca BOŞ/yeni bir SSD ise
sudo mkdir -p /mnt/recordings
UUID=$(sudo blkid -s UUID -o value /dev/nvme0n1p1)
echo "UUID=$UUID /mnt/recordings ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab
sudo mount -a
mountpoint /mnt/recordings         # doğrulama
```

`nofail` seçeneği önemlidir: SSD çıkarılmış olsa bile Jetson'ın
başlatılabilmesini sağlar (yalnızca kamera servisi başlamaz, sistem
tamamen kilitlenmez).

## Hızlı kurulum

```bash
# Kullanıcı: sudo yetkisi olan kullanıcı (root DEĞİL, doğrudan)
git clone <repo-url> /mnt/recordings/jetson-camera-service   # veya dosyaları SSD'ye kopyalayın
cd /mnt/recordings/jetson-camera-service
./install.sh
```

`install.sh` şunları yapar (ayrıntı için betiğin kendisine veya
`PROJECT_STATE.md`'ye bakın):

1. Sistem uyumluluğu ve SSD mount kontrolü
2. Gerekli apt paketlerinin kurulumu
3. Dedike, düşük yetkili `jetcam` servis kullanıcısının oluşturulması
4. Python venv + backend bağımlılıklarının kurulumu
5. `config.yaml` ve `.env` dosyalarının oluşturulması (varsa DOKUNULMAZ)
6. Veritabanı migration'larının çalıştırılması
7. systemd servisinin kurulup etkinleştirilmesi
8. Sağlık kontrolü

Kurulum sonunda **varsayılan admin parolası TERMİNALE BİR KEZ
yazdırılır** - kaydedin, bir daha gösterilmeyecektir.

**İdempotenttir**: `install.sh`'ı tekrar çalıştırmak (örn. güncelleme
sonrası) mevcut `config.yaml`, `.env`, veritabanını veya kayıtları
SİLMEZ/ÜZERİNE YAZMAZ - yalnızca kodu/bağımlılıkları günceller ve
servisi yeniden başlatır.

## Kamera testi

```bash
# Kullanıcı: video ve i2c gruplarında olan herhangi bir kullanıcı
./scripts/diagnose-camera.sh     # donanım/sürücü/GStreamer durumunu raporlar
./scripts/test-recording.sh 10   # gerçek 10 saniyelik test kaydı alır, ffprobe ile doğrular
```

## Servis başlatma ve durdurma

```bash
# Kullanıcı: sudo yetkisi olan kullanıcı
sudo systemctl start jetson-camera-dashboard.service
sudo systemctl stop jetson-camera-dashboard.service
sudo systemctl restart jetson-camera-dashboard.service
sudo systemctl status jetson-camera-dashboard.service
```

## Dashboard adresi

```
http://<jetson-ip>:8080        # yerel ağdan
http://<tailscale-ip>:8080     # Tailscale kurulduktan sonra (bkz. REMOTE_ACCESS.md)
```

## Varsayılan kullanıcı ve parola değiştirme

İlk kurulumda tek bir `admin` kullanıcısı oluşturulur, parolası
`install.sh` tarafından rastgele üretilip terminale yazdırılır. İlk
girişte parola değişimi ZORUNLUDUR - dashboard bunu otomatik ister,
başka hiçbir işlem yapılamaz.

Parolayı script ile değiştirmeniz gerekirse (örn. unutulduysa):

```bash
# Kullanıcı: sudo yetkisi olan kullanıcı
cd /mnt/recordings/jetson-camera-service/backend
source .venv/bin/activate
python3 -c "
from sqlmodel import Session, create_engine, select
from app.models.user import User
from app.security.passwords import hash_password
engine = create_engine('sqlite:////mnt/recordings/db/app.db')
with Session(engine) as s:
    user = s.exec(select(User)).first()
    user.password_hash = hash_password('YENİ_PAROLA_BURAYA')
    user.must_change_password = True
    s.add(user); s.commit()
"
```

## Log görüntüleme

```bash
sudo journalctl -u jetson-camera-dashboard.service -f       # canlı takip
sudo journalctl -u jetson-camera-dashboard.service -n 200   # son 200 satır
```

Veya dashboard'un "Loglar" sekmesinden (JSON formatlı, seviye filtresi
ve indirme linkiyle).

## Güncelleme

```bash
cd /mnt/recordings/jetson-camera-service
git pull   # veya yeni dosyaları kopyalayın
./install.sh   # idempotent - mevcut config/veri korunur, kod/bağımlılıklar güncellenir
```

## Yedekleme

```bash
./scripts/backup-config.sh   # config.yaml + .env + veritabanını yedekler (video dosyaları HARİÇ)
```

Video kayıtlarının kendisi zaten SSD'de kalıcıdır; ayrıca yedeklemek
isterseniz `rsync -a /mnt/recordings/media/ <hedef>/` kullanın.

## Sistem kurtarma

Bkz. `docs/RECOVERY.md`.

## Sık karşılaşılan hatalar

Bkz. `docs/TROUBLESHOOTING.md`.
