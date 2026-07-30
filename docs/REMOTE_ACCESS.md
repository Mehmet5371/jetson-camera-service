# Uzaktan Erişim (Tailscale)

Bu servis, kuruluş sonrası fiziksel erişim olmayacağı varsayımıyla
tasarlandı (bkz. `PROJECT_STATE.md`). Dashboard'a internet üzerinden
güvenli erişim için **Tailscale** kullanılır - **port yönlendirme
(port forwarding) kullanılmaz ve önerilmez.**

## Neden Tailscale?

* Router'da port açmaya gerek yok - saldırı yüzeyini artırmaz.
* Trafik uçtan uca şifrelenir (WireGuard tabanlı).
* Cihaz herkese açık bir IP'de görünmez, yalnızca sizin Tailscale
  ağınızdaki cihazlar erişebilir.
* Router'ın CGNAT arkasında olması veya IP'sinin değişmesi önemsizdir.

Alternatif: kendi WireGuard sunucunuzu işletmek isterseniz aynı ağ
seviyesi kısıtlama mantığı (bkz. aşağıdaki güvenlik duvarı bölümü)
geçerlidir, yalnızca `tailscale0` yerine kendi VPN arayüzünüzü
kullanırsınız.

## Kurulum

```bash
# Kullanıcı: sudo yetkisi olan kullanıcı (emre)
cd /mnt/recordings/jetson-camera-service
./scripts/setup-tailscale.sh
```

Script sizi bir tarayıcı linkine yönlendirecek (interaktif mod) veya
`TS_AUTHKEY` ortam değişkeni verilmişse (Tailscale admin panelinden
üretilen bir anahtar) headless olarak bağlanacaktır:

```bash
TS_AUTHKEY=tskey-auth-xxxxxxxxxxxx ./scripts/setup-tailscale.sh
```

Kurulum tamamlandığında cihazınızın Tailscale IP'si (örn.
`100.x.y.z`) ekrana yazdırılır. Bu IP, Tailscale ağınızdaki diğer
cihazlardan (telefon, dizüstü bilgisayar - hepsinde Tailscale
istemcisi kurulu ve aynı hesaba bağlı olmalı) sabittir.

## Dashboard erişimini ağ seviyesinde kısıtlama

Tailscale kurulumundan SONRA, dashboard portunun (8080) yalnızca
Tailscale ağından, yerel LAN'dan ve (isteğe bağlı) açıkça izin
verilen IP'lerden erişilebilir olmasını sağlayın:

```bash
# Kullanıcı: sudo yetkisi olan kullanıcı
./scripts/configure-firewall.sh
```

Bu script `ufw` (Ubuntu Uncomplicated Firewall) kullanarak:

1. Varsayılan politikayı "gelen trafiği reddet" yapar.
2. SSH'ı (port 22) her zaman açık bırakır (kilitlenmeyi önlemek için).
3. Yerel LAN alt ağınızı otomatik tespit edip 8080 portuna izin verir.
4. `tailscale0` arayüzünden (veya henüz kurulmadıysa Tailscale'ın
   CGNAT aralığı `100.64.0.0/10`'dan) 8080 portuna izin verir.
5. Komut satırı argümanı olarak verilen ek IP'lere izin verir:
   ```bash
   ./scripts/configure-firewall.sh 203.0.113.5
   ```

**UYARI:** Bu script'i cihaza yalnızca SSH ile bağlıyken çalıştırıyorsanız,
SSH kuralının önce eklendiğinden emin olun (script bunu otomatik yapar).
Yerel konsol/klavye-ekran erişiminiz yoksa ve bir hata olursa cihazdan
kilitlenebilirsiniz - dikkatli test edin.

## Dashboard'a bağlanma

Kurulum sonrası dashboard'a şu adreslerden erişilebilir:

* **Tailscale üzerinden (önerilen, internetten):**
  `http://<tailscale-ip>:8080`
* **Yerel ağdan (aynı Wi-Fi/Ethernet):**
  `http://<jetson-lan-ip>:8080`

HTTPS zorunlu değildir çünkü Tailscale bağlantısı zaten uçtan uca
şifrelidir. Dashboard'ı VPN olmadan doğrudan internete açacaksanız
(önerilmez) HTTPS zorunlu olmalıdır - bkz. `docs/SECURITY.md` içindeki
Caddy/Nginx reverse proxy notu (Faz 10).

## Sorun giderme

```bash
# Tailscale bağlantı durumu
tailscale status

# Bu cihazın Tailscale IP'si
tailscale ip -4

# ufw kuralları
sudo ufw status verbose

# Tailscale servis logları
sudo journalctl -u tailscaled -n 50 --no-pager
```

Dashboard'a Tailscale üzerinden erişilemiyorsa:
1. İstemci cihazda (telefon/laptop) Tailscale uygulamasının aynı
   hesaba bağlı ve "Connected" durumda olduğunu doğrulayın.
2. `tailscale status` çıktısında Jetson'ın listelendiğini kontrol edin.
3. `sudo ufw status verbose` çıktısında 8080 portu için Tailscale
   kuralının aktif olduğunu doğrulayın.
4. Servisin çalıştığını doğrulayın: `sudo systemctl status
   jetson-camera-dashboard.service`
