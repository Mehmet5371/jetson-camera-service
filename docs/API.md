# API Referansı

Canlı, otomatik üretilen OpenAPI dokümantasyonu her zaman şurada
mevcuttur: `http://<dashboard-adresi>:8080/docs` (Swagger UI) veya
`/openapi.json` (ham şema). Bu belge, tüm uç noktaların hızlı bir
özetidir.

## Genel kurallar

* Tüm yanıtlar `{"success": true, "data": {...}}` veya
  `{"success": false, "error": {"code": "...", "message": "...", "details": {}}}`
  formatındadır.
* `/api/health` dışındaki her uç nokta kimlik doğrulama gerektirir
  (httponly `jetson_camera_session` cookie'si).
* `POST`/`PUT`/`DELETE` istekleri `X-CSRF-Token` header'ı gerektirir
  (değeri, httponly OLMAYAN `jetson_camera_csrf` cookie'sinden okunur).
* `must_change_password: true` iken `/api/auth/*` dışındaki her uç nokta
  `403 PASSWORD_CHANGE_REQUIRED` döner.

## Kimlik doğrulama

| Uç nokta | Metod | Açıklama |
|---|---|---|
| `/api/auth/login` | POST | `{"username", "password"}` → oturum+CSRF cookie kurar |
| `/api/auth/logout` | POST | Cookie'leri temizler |
| `/api/auth/me` | GET | `{"username", "must_change_password"}` |
| `/api/auth/change-password` | POST | `{"current_password", "new_password"}` (min 8 karakter) |

**Örnek:**
```bash
curl -X POST http://jetson:8080/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"..."}' -c cookies.txt
```

## Durum ve sistem

| Uç nokta | Metod | Açıklama |
|---|---|---|
| `/api/health` | GET | Herkese açık, kimlik doğrulama GEREKMEZ. `{"status":"ok"}` |
| `/api/status` | GET | Kayıt durumu + depolama + CPU/RAM/sıcaklık + servis uptime |
| `/api/system` | GET | Jetson modeli, JetPack/L4T/Ubuntu sürümü, IP'ler, hostname |
| `/api/ws/status` | WS | Her 2 saniyede `/api/status` ile aynı payload'ı push eder |

## Kamera

| Uç nokta | Metod | Açıklama |
|---|---|---|
| `/api/camera/status` | GET | Algılanan backend, sensör modları, uyarılar |
| `/api/camera/test` | POST | 2 saniyelik gerçek test kaydı alır, `ffprobe` ile doğrular (aktif kayıt varsa `409 RECORDING_ALREADY_ACTIVE`) |

## Canlı görüntü (önizleme + kayıt sırasında) ve odak

Canlı MJPEG görüntü İKİ kaynaktan gelir ve `/api/camera/live/stream` hangisi
aktifse ondan yayınlar:
- Kayıt YOKKEN: ayrı önizleme pipeline'ı (`preview/start` ile başlatılır).
- Kayıt SIRASINDA: kayıt pipeline'ının `tee` dalı (kayıtla AYNI Argus akışı) -
  ayrı önizleme başlatmaya gerek yok, kayıt başlayınca otomatik devreye girer.

Odak kontrolü (manuel + otomatik) önizleme VEYA kayıt aktifken çalışır
(VCM odak motoru I2C'si yalnızca Argus akışı açıkken yanıt verir). Odak
değeri paylaşılan bir denetleyicide tutulur: önizlemede bulunan değer kayıt
başlarken korunup lens'e yeniden uygulanır (kayıt bulanık başlamaz).

**Sürekli otomatik odak varsayılan olarak AÇIKTIR**: canlı akış varken
keskinlik sürekli ölçülür ve odak kaçtığında (sahne/mesafe değişimi) hiçbir
uç nokta çağrılmadan kendiliğinden yeniden odaklanılır - kayıt sırasında da.
Ayrıntı: `docs/ARCHITECTURE.md` böl. 6.1.

| Uç nokta | Metod | Açıklama |
|---|---|---|
| `/api/camera/live/stream` | GET | Aktif kaynaktan (önizleme veya kayıt tee dalı) `multipart/x-mixed-replace` MJPEG - tarayıcıda `<img src="...">`, çoklu istemci; hiçbiri aktif değilse `409 PREVIEW_NOT_ACTIVE` |
| `/api/camera/preview/start` | POST | Kayıt YOKKEN ayrı önizleme başlatır (kayıt aktifse `409 PREVIEW_UNAVAILABLE_WHILE_RECORDING` - kayıt zaten canlı görüntü sağlıyor) |
| `/api/camera/preview/stop` | POST | Ayrı önizlemeyi durdurur |
| `/api/camera/focus` | GET | `{"current_value", "session_active", "continuous": {...}}` (aşağıya bakın) |
| `/api/camera/focus` | POST | `{"mode":"manual","value":0-1000}` (anında; sürekli odağı `manual_override_seconds` kadar duraklatır) veya `{"mode":"auto"}` (hemen tam süpürme, `{"value","sharpness","steps_taken"}` döner); önizleme veya kayıt aktif olmalı |
| `/api/camera/focus/continuous` | POST | `{"enabled": true\|false}` - sürekli otomatik odağı ÇALIŞMA ZAMANINDA aç/kapat (config'e yazılmaz; kalıcı varsayılan `camera.continuous_autofocus.enabled`). Canlı akış gerekmez |

### Sürekli otomatik odak durumu

Odak uç noktalarının yanıtındaki `continuous` nesnesi (aynı şema
`/api/camera/focus/continuous` yanıtında da döner):

```json
{
  "enabled": true, "configured_enabled": true, "runtime_enabled": true,
  "during_recording": true, "active": true, "suspended": false,
  "suspended_seconds_left": null, "disabled_reason": null, "source": "recording",
  "last_sharpness": 136.0, "baseline_sharpness": 130.2, "refocus_count": 2,
  "last_action": {"reason": "sharpness_drop", "value": 175, "sharpness": 139.9,
                  "sharpness_before": 2.1, "steps_taken": 9,
                  "escalated": false, "reverted": false, "duration_seconds": 3.36}
}
```

* `enabled`: config + çalışma zamanı şalteri birlikte (`camera.autofocus: false`
  otomatik odağın ana şalteridir, sürekli odağı da kapatır).
* `active`: şu an gerçekten izliyor mu (canlı akış var, duraklatılmamış,
  kayıttaysa `during_recording: true`).
* `disabled_reason`: kendini kapattıysa sebebi (örn. odak motoruna üst üste
  yazılamadı).
* `last_action.reason`: `initial` | `sharpness_drop` | `stream_restart`;
  `escalated`: yerel aramadan tam süpürmeye yükseltildi mi; `reverted`:
  sonuç kötü çıktığı için eski pozisyona geri dönüldü mü.

**Örnek:**
```bash
curl -X POST http://jetson:8080/api/camera/preview/start -b cookies.txt -H "X-CSRF-Token: $CSRF"
curl -X POST http://jetson:8080/api/camera/focus -b cookies.txt -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" -d '{"mode":"auto"}'
# canlı görüntü (önizleme veya kayıt sırasında):
curl -N http://jetson:8080/api/camera/live/stream -b cookies.txt -o live.mjpeg
```

## Kayıtlar

| Uç nokta | Metod | Açıklama |
|---|---|---|
| `/api/recordings/start` | POST | Kayıt başlatır (`409` eğer zaten aktifse) |
| `/api/recordings/stop` | POST | Kayıt durdurur (`409` eğer aktif değilse) |
| `/api/recordings` | GET | Sayfalı liste. Query: `page`, `page_size`, `sort_by` (`started_at`\|`file_size_bytes`\|`duration_seconds`\|`status`\|`filename`), `sort_order` (`asc`\|`desc`), `status`, `search` |
| `/api/recordings/{id}` | GET | Tek kayıt detayı (`404` yoksa) |
| `/api/recordings/{id}/stream` | GET | HTTP Range destekli video akışı (`Content-Type: video/mp4`) |
| `/api/recordings/{id}/download` | GET | `Content-Disposition: attachment` ile indirme, chunked |
| `/api/recordings/{id}` | DELETE | Siler (aktif kayıt ise `409 RECORDING_DELETE_FORBIDDEN`) |

**Örnek liste yanıtı:**
```json
{
  "success": true,
  "data": {
    "items": [{"id": 1, "filename": "2026-07-18_10-00-00.mp4", "status": "completed", "duration_seconds": 30.5, "file_size_bytes": 12345678, "width": 1920, "height": 1080, "codec": "h264", "is_valid": true, "started_at": "..."}],
    "page": 1, "page_size": 25, "total": 1
  }
}
```

## Depolama ve ayarlar

| Uç nokta | Metod | Açıklama |
|---|---|---|
| `/api/storage` | GET | Mount durumu, toplam/kullanılan/boş byte, yüzde |
| `/api/settings` | GET | Tüm config.yaml içeriği (gizli bilgi içermez) |
| `/api/settings` | PUT | Kısmi güncelleme: `{"camera":{...}, "recording":{...}, "retention":{...}}`. Aktif kayıt sırasında `camera`/`recording` alanları `409 SETTINGS_LOCKED` döner |

## Loglar ve servis

| Uç nokta | Metod | Açıklama |
|---|---|---|
| `/api/logs` | GET | Query: `level` (`ERROR`\|`WARNING`\|`INFO`), `limit` (varsayılan 200) |
| `/api/logs/download` | GET | Ham log dosyasını indirir |
| `/api/service/restart-camera` | POST | `sudo systemctl restart nvargus-daemon` (aktif kayıt varsa `409 CAMERA_SERVICE_BUSY`) |

## Hata kodları (tam liste)

`CONFIG_INVALID`, `CONFIG_NOT_FOUND`, `INTERNAL_ERROR`, `NOT_FOUND`,
`VALIDATION_ERROR`, `UNAUTHORIZED`, `CAMERA_NOT_AVAILABLE`,
`CAMERA_BACKEND_UNSUPPORTED`, `FOCUS_CONTROL_UNAVAILABLE`,
`PIPELINE_START_FAILED`, `RECORDING_ALREADY_ACTIVE`,
`RECORDING_NOT_ACTIVE`, `INVALID_STATE_TRANSITION`,
`STORAGE_NOT_MOUNTED`, `STORAGE_UUID_MISMATCH`,
`STORAGE_WRITE_TEST_FAILED`, `STORAGE_INSUFFICIENT_SPACE`,
`RECORDING_FILE_NOT_FOUND`, `RECORDING_DELETE_FORBIDDEN`,
`INVALID_CREDENTIALS`, `RATE_LIMITED`, `PASSWORD_CHANGE_REQUIRED`,
`CSRF_TOKEN_INVALID`, `SETTINGS_LOCKED`, `PATH_TRAVERSAL_REJECTED`,
`CAMERA_SERVICE_BUSY`, `PREVIEW_ALREADY_ACTIVE`, `PREVIEW_NOT_ACTIVE`,
`PREVIEW_UNAVAILABLE_WHILE_RECORDING`, `FOCUS_REQUIRES_ACTIVE_SESSION`
(bkz. `backend/app/core/errors.py` - tek doğru kaynak).
