# ftp-server — Gömülü FTP sunucusu

Horstmann SN2 cihazlarının config ve firmware dosyalarını bıraktığı/aldığı FTP
ucu. Cihazın kendi FTP ekranından (`Server / Port 21 / User / Pass / Dir`,
örn. `/SN20/FOTA/`) doğrudan bu sunucuya bağlanır; platform aynı kök dizini
okuyup config'i arayüzde düzenler.

**Neden ayrı servis:** cihaz FTP konuşuyor, backend HTTP. Aradaki dönüşümü
backend'e gömmek, FTP'nin pasif port aralığını ve uzun süren dosya transferini
API sürecinin içine sokardı. Ayrı container hem izole hem de yeniden
başlatılabilir.

**Container:** `e1-grid-ftp-server` · **İmaj:** `.../ftp-server:${E1_VERSION}`

---

## Çalışma şekli

`pyftpdlib` üzerine kurulu (saf Python, C bağımlılığı yok). Non-blocking
`asyncore` döngüsü; sağlık ucu ayrı bir thread'te HTTP olarak yayınlanır.

Deneme sürümünde **tek ortak kullanıcı** var ve tam yetkili (oku/yaz/liste/
sil/dizin oluştur). Cihaz kendi seri numarasına ait alt dizini kendisi açar.

```
SN2 cihaz  ──FTP:21──▶  ftp-server  ──volume──▶  /data/ftp/<SN>/...
                                                      ▲
                                       backend-api ───┘  (config okuma/yazma)
```

## Yapılandırma

| Değişken | Varsayılan | Not |
|---|---|---|
| `FTP_USER` | `device` | Tüm cihazların paylaştığı kullanıcı |
| `FTP_PASSWORD` | — | **Zorunlu.** `.env`'den gelir, koda gömülmez |
| `FTP_ROOT` | `/data/ftp` | Docker volume; cihaz dizinlerinin kökü |
| `FTP_LISTEN_PORT` | `21` | Kontrol kanalı |
| `FTP_PASV_MIN_PORT` | `30000` | Pasif mod veri portu alt sınırı |
| `FTP_PASV_MAX_PORT` | `30009` | Pasif mod veri portu üst sınırı |
| `FTP_MASQUERADE_ADDRESS` | boş | NAT arkasındaysa cihaza bildirilecek dış IP |
| `WORKER_HEALTH_PORT` | `8015` | `GET /health` |

### Pasif port aralığı neden açıkça veriliyor

FTP pasif modda veri için ayrı bir port açar ve numarasını istemciye söyler.
Aralık sabitlenmezse rastgele bir port seçilir; container port eşlemesi ve
güvenlik duvarı o portu bilemez, transfer kontrol kanalı sağlıklıyken sessizce
takılır. Bu yüzden aralık hem `compose`'da hem burada aynı: 30000-30009.

### `FTP_MASQUERADE_ADDRESS` ne zaman gerekir

Sunucu NAT arkasındaysa pasif moda geçerken istemciye **kendi** iç IP'sini
bildirir; cihaz o adrese ulaşamaz ve transfer zaman aşımına düşer. Dış IP bu
değişkenle verilirse doğru adres bildirilir. Aynı LAN'daki kurulumlarda boş
bırakılır.

## Sağlık

```bash
curl -s http://localhost:8015/health
docker logs e1-grid-ftp-server --tail 50
```

## Sorun giderme

| Belirti | Muhtemel neden |
|---|---|
| Cihaz bağlanıyor, dizin listesi boş kalıyor | Pasif port aralığı dışarı açılmamış |
| Bağlantı kuruluyor, transfer takılıyor | NAT arkasında `FTP_MASQUERADE_ADDRESS` boş |
| `530 Authentication failed` | `FTP_PASSWORD` `.env`'de yok ya da cihazdakinden farklı |
| Dosyalar görünüyor ama UI'da yok | Backend farklı bir volume'a bakıyor; `FTP_ROOT` eşleşmesini kontrol edin |

## Bilinen sınır

Tek kullanıcı ve tam yetki, cihaz başına izolasyon **yok** — bir cihaz
diğerinin dizinini okuyabilir. Sahada FTP ağı cihazlara özel bir segmentte
tutulmalı. Cihaz başına kullanıcı ayrımı açık bir iş kalemi.
