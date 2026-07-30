# Güvenlik

## Kimlik doğrulama

* Tek kullanıcı modeli (çoklu kullanıcı/roller kapsam dışı, bkz. şartname
  böl. 26).
* Parolalar **Argon2** ile hash'lenir (`passlib`), düz metin ASLA
  tutulmaz veya loglanmaz.
* Oturum, httponly bir cookie'de taşınan kısa ömürlü (12 saat) bir JWT'dir
  - JavaScript'ten okunamaz, XSS ile çalınamaz.
* **CSRF koruması**: ayrı, httponly OLMAYAN bir cookie'ye rastgele bir
  token yazılır; tarayıcı JS'i bunu okuyup durum değiştiren isteklerde
  (POST/PUT/DELETE) `X-CSRF-Token` header'ı olarak geri göndermek
  zorundadır (çift-gönderim / double-submit deseni).
* **Login rate limiting**: aynı IP+kullanıcı adı kombinasyonu için 5
  dakikada en fazla 5 başarısız deneme; aşılırsa 429 + `retry_after`.
* **İlk girişte zorunlu parola değişimi**: varsayılan parola
  değiştirilmeden `/api/auth/change-password` ve `/api/auth/me`/`logout`
  DIŞINDAKİ hiçbir uç nokta kullanılamaz (`PASSWORD_CHANGE_REQUIRED`).

## Gizli bilgiler

* `JETSON_CAMERA_SECRET_KEY` (JWT imzalama), `JETSON_CAMERA_ADMIN_PASSWORD_HASH`
  yalnızca `.env` dosyasında tutulur, `config.yaml`'da DEĞİL.
* `.env` dosyası `.gitignore`'dadır, asla commit edilmez.
* `.env` dosya izni `600` (yalnızca sahip okuyabilir); systemd bunu
  `EnvironmentFile=` ile root olarak okuyup `jetcam` kullanıcısına
  environment variable olarak aktarır.

## Ağ erişimi

* Dashboard'un varsayılan uzaktan erişim yöntemi **Tailscale**'dir - port
  yönlendirme (port forwarding) KULLANILMAZ ve önerilmez (bkz.
  `REMOTE_ACCESS.md`).
* `./scripts/configure-firewall.sh`, dashboard portunu (8080) yalnızca
  Tailscale ağı, yerel LAN ve açıkça izin verilen IP'lerden erişilebilir
  kılar (`ufw`, varsayılan-reddet politikası, SSH her zaman açık).
* Dashboard'ı VPN olmadan doğrudan internete açacaksanız (ÖNERİLMEZ)
  HTTPS zorunlu olmalıdır - Caddy veya Nginx reverse proxy ile (aşağıya
  bakın).

## Komut enjeksiyonu koruması

* `shell=True` KODUN HİÇBİR YERİNDE kullanılmaz (`app/core/shell.py`
  merkezi sarmalayıcısı bunu garanti eder).
* GStreamer pipeline'ı, kullanıcıdan gelen hiçbir ham string ile
  OLUŞTURULMAZ - yalnızca Pydantic ile doğrulanmış sayısal/enum config
  alanlarından bir argüman LİSTESİ kurulur (`recording/pipeline.py`).
* Kamera odak kontrolü (`i2cset`) de aynı şekilde argüman listesiyle
  çalıştırılır (`camera/focus.py`) - Arducam'ın orijinal örneğindeki
  `os.system(shell string)` deseninden KASITLI olarak farklıdır.

## Path traversal koruması

* Kullanıcı, video dosyalarına erişirken (`/stream`, `/download`, `DELETE`)
  yalnızca bir tamsayı `id` gönderir - hiçbir zaman ham bir dosya yolu
  kabul edilmez (yapısal olarak imkânsız).
* Savunma amaçlı ikinci katman: sunulan dosyanın gerçekten
  `storage.video_path` içinde olduğu her istekte yeniden doğrulanır
  (`api/recordings.py::_resolve_safe_path`) - DB'nin bir şekilde
  bozulması/manuel müdahale durumuna karşı.

## Dosya sistemi izinleri

* Servis dedike, düşük yetkili bir kullanıcı (`jetcam`) olarak çalışır -
  `emre` gibi geniş sudo yetkisine sahip admin hesabıyla DEĞİL.
* `jetcam`'e yalnızca `video` ve `i2c` grupları (donanım erişimi için)
  ve TEK bir dar kapsamlı NOPASSWD sudo kuralı (`systemctl restart
  nvargus-daemon`) tanımlıdır - `NoNewPrivileges=false` bilinçli olarak
  bu tek özellik için gereklidir, başka hiçbir yetki yükseltme yolu yoktur.
* Kod (`backend/`, `frontend/`, `migrations/`) yalnızca okunabilir
  (o+rX); yalnızca `config/` (ayarlar) ve veri dizinleri (`media/`,
  `db/`, `run/`, `logs/`) `jetcam`'e yazılabilir.

## HTTPS (yalnızca VPN'siz doğrudan internet erişimi için)

Tailscale kullanıyorsanız HTTPS GEREKMEZ (bağlantı zaten uçtan uca
şifrelidir). Dashboard'ı Tailscale/VPN olmadan internete açmayı
seçerseniz (önerilmez), bir reverse proxy ile HTTPS zorunlu kılın:

```
# /etc/caddy/Caddyfile örneği
kamera.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Caddy, Let's Encrypt sertifikalarını otomatik yönetir. Bu senaryoda
ayrıca `configure-firewall.sh`'ı yalnızca 443 (Caddy) portunu dışa açık
bırakacak, 8080'i yalnızca `127.0.0.1`'den erişilebilir kılacak şekilde
uyarlamanız gerekir.

## Bilinen sınırlamalar

* Rate limiter süreç-içi bellekte tutulur, servis yeniden başladığında
  sıfırlanır (tek worker/tek instance varsayımıyla tutarlı).
* Parola değişikliği eski JWT token'larını anında geçersiz kılmaz (yeni
  bir token üretilir ama eskisi süresi dolana kadar - en fazla 12 saat -
  teknik olarak geçerli kalır). Çoklu-oturum iptali gelecekte bir
  `token_version` alanıyla güçlendirilebilir.
