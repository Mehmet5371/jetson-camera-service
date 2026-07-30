# Sistem Kurtarma

Bu belge, cihaza fiziksel erişim OLMADAN (yalnızca SSH/Tailscale
üzerinden) yapılabilecek kurtarma senaryolarını kapsar.

## Servis yanıt vermiyor ama SSH erişimi var

```bash
sudo systemctl restart jetson-camera-dashboard.service
sudo journalctl -u jetson-camera-dashboard.service -n 100 --no-pager
```

## Kamera kalıcı olarak hata veriyor

```bash
sudo systemctl restart nvargus-daemon
./scripts/diagnose-camera.sh
```

Hâlâ çalışmıyorsa Jetson'ı yeniden başlatın (bkz. aşağıda).

## Yarım kalmış/orphan bir gst-launch süreci var

Servis beklenmedik şekilde durursa (örn. `kill -9`, güç kesintisi), bir
GStreamer süreci sahipsiz kalabilir:

```bash
ps aux | grep gst-launch
# varsa, ÖNCE SIGINT (dosyayı finalize etmeye çalışır), sonra gerekirse SIGKILL:
kill -INT <pid>
sleep 5
kill -9 <pid>   # yalnızca SIGINT işe yaramadıysa
```

Servis kendi başladığında (`recover_from_previous_run()`) bunu tespit
edip loglara uyarı yazar ama otomatik ÖLDÜRMEZ (aktif bir kaydı bozma
riski) - manuel müdahale gerekir.

## Veritabanı bozuldu

```bash
# Yedekten geri yükleme (bkz. backup-config.sh ile alınmış bir yedek varsa)
./scripts/restore-config.sh <yedek.tar.gz>
sudo systemctl restart jetson-camera-dashboard.service
```

Yedek yoksa: veritabanı yalnızca METADATA tutar, video dosyaları
etkilenmez. Veritabanını sıfırdan oluşturup mevcut dosyaları yeniden
indeksleyebilirsiniz:

```bash
sudo systemctl stop jetson-camera-dashboard.service
mv /mnt/recordings/db/app.db /mnt/recordings/db/app.db.corrupt
cd /mnt/recordings/jetson-camera-service/migrations
JETSON_CAMERA_CONFIG=/mnt/recordings/jetson-camera-service/config/config.yaml \
  ../backend/.venv/bin/alembic upgrade head
sudo systemctl start jetson-camera-dashboard.service
```

Servis her başlangıçta ve her 5 dakikada bir (boştayken) diski otomatik
tarayıp (`reconcile_recordings`) veritabanında olmayan dosyaları
`ffprobe` ile doğrulayıp yeniden ekler - manuel bir "reindex" komutuna
gerek yoktur.

## config.yaml bozuldu / geçersiz

Servis BAŞLAMAZ (kasıtlı - sessizce varsayılana düşmez). Kurtarma:

```bash
sudo systemctl stop jetson-camera-dashboard.service
cp /mnt/recordings/jetson-camera-service/config/config.example.yaml \
   /mnt/recordings/jetson-camera-service/config/config.yaml
# veya bir yedekten:
./scripts/restore-config.sh <yedek.tar.gz>
sudo systemctl start jetson-camera-dashboard.service
```

## Parola/erişim tamamen kayboldu

Bkz. `docs/INSTALLATION.md` "Varsayılan kullanıcı ve parola değiştirme".

## SSD çıkarıldı/değiştirildi

Servis `RequiresMountsFor=/mnt/recordings` sayesinde SSD mount olana
kadar başlamaz (`nofail` fstab seçeneği sayesinde Jetson'ın kendisi
kilitlenmez). Yeni bir SSD takıldıysa:

```bash
sudo mkdir -p /mnt/recordings
sudo mount /dev/nvme0n1p1 /mnt/recordings   # veya /etc/fstab'daki UUID ile
# jetson-camera-service dosyalarının SSD'de olduğunu doğrulayın (yoksa
# yeniden kurulum gerekir - bkz. INSTALLATION.md)
sudo systemctl restart jetson-camera-dashboard.service
```

## Jetson'ı fiziksel erişim olmadan yeniden başlatma

```bash
sudo reboot
```

`jetson-camera-dashboard.service` `enable` edildiği için (bkz.
`install.sh`) açılışta otomatik başlar. `nofail` + `RequiresMountsFor`
kombinasyonu SSD gecikmeli algılansa bile servisin doğru şekilde
beklemesini/başlamamasını sağlar.

## Tamamen sıfırdan kurulum (felaket kurtarma)

Kod SSD'de kayıtlıysa (repo/dosyalar), video kayıtları ve veritabanı da
SSD'de olduğu için yalnızca `./install.sh`'ı yeniden çalıştırmak
yeterlidir - idempotent olduğu için mevcut veriye dokunmaz. SSD'nin
kendisi de kaybolduysa: yeni bir SSD hazırlayın (`docs/INSTALLATION.md`
"SSD hazırlama"), proje dosyalarını (kod deposu) oraya kopyalayın,
`./install.sh` çalıştırın. Video kayıtları geri getirilemez (yalnızca
SSD'deydi) - bu yüzden kritik veriler için ayrı bir offsite yedekleme
stratejisi (örn. `rsync` ile periyodik NAS/SFTP aktarımı, şartname böl.
26'da opsiyonel modül olarak listelenmiştir) düşünülmelidir.
