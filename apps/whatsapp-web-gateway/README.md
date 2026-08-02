# whatsapp-web-gateway — Self-hosted WhatsApp bildirim ucu

Alarm ve sistem bildirimlerini WhatsApp üzerinden göndermek için kullanılan
küçük Node servisi. [Baileys](https://github.com/WhiskeySockets/Baileys) ile
WhatsApp Web protokolünü konuşur; giriş **QR kod okutarak** yapılır.

**Neden self-hosted:** resmî WhatsApp Business API başvuru, onay ve mesaj başına
ücret gerektiriyor. Saha kurulumlarının çoğunda tek bir şirket hattı var ve
bildirim hacmi düşük. Bu servis o hattı olduğu gibi kullanır — dış servis,
abonelik ve müşteri verisinin üçüncü tarafa çıkması yok.

**Container:** `e1-grid-whatsapp-web-gateway` · **Port:** 8016 (yalnızca iç ağ)

---

## Mimarideki yeri

```
alarm-service ──▶ notification-worker ──HTTP──▶ whatsapp-web-gateway ──▶ WhatsApp
                                                        │
                                                   session-data/  (volume)
```

`notification-worker` diğer kanallarla (SMTP, SMS, Telegram, FCM) aynı şekilde
davranır; WhatsApp yalnızca bir dispatch hedefidir. Backend bu servise
`WHATSAPP_WEB_GATEWAY_URL` üzerinden ulaşır.

## Uçlar

Tümü `SERVICE_TOKEN` ile korunur (`/health` hariç). Karşılaştırma sabit zamanlı
yapılır — token uzunluğundan sızıntı olmasın diye.

| Uç | Metot | İş |
|---|---|---|
| `/health` | GET | Ayakta mı — auth yok, container healthcheck'i kullanır |
| `/status` | GET | Oturum durumu: bağlı mı, hangi numara |
| `/qr` | GET | Giriş için QR kodu (PNG/data-url) |
| `/groups` | GET | Erişilebilen grup listesi — grup hedefli bildirim için |
| `/send` | POST | Mesaj gönder |
| `/logout` | POST | Oturumu kapat; yeni QR gerekir |

## Yapılandırma

| Değişken | Not |
|---|---|
| `SERVICE_TOKEN` | `INTERNAL_SERVICE_TOKEN` ile aynı; iç servis kimliği |
| `WHATSAPP_SESSION_DIR` | `/data/session` — oturum anahtarları burada |
| `WORKER_HEALTH_PORT` | `8016` |

## Oturum kalıcılığı — en kritik nokta

QR ile bir kez giriş yapıldıktan sonra kimlik bilgisi `WHATSAPP_SESSION_DIR`
altına yazılır ve **volume'da kalır**. Bu dizin silinir ya da volume
bağlanmadan container ayağa kalkarsa oturum düşer ve **bildirimler sessizce
gitmemeye başlar** — servis ayakta, `/health` iyi, ama gönderim yok.

Bu yüzden:

- `/status` düzenli izlenmeli; "ayakta" yeterli bir sinyal değil.
- Volume yedeklemesi oturumu da kapsamalı.
- Yeniden QR okutmak fiziksel erişim ister (telefondan onay) — sahada bunu
  yapacak kimse olmayabilir. Oturum kaybı planlanmadan yaşanırsa bildirim
  kanalı günlerce kapalı kalabilir.

## Sınırlar ve riskler

- **Resmî değil.** WhatsApp Web protokolü tersine mühendislikle konuşuluyor;
  WhatsApp tarafındaki bir değişiklik servisi kırabilir. Kritik alarmlar için
  tek kanal olarak kullanılmamalı — SMTP/SMS yedekte kalsın.
- **Hesap askıya alınabilir.** Yoğun ya da otomatik görünen trafik hattın
  kapatılmasına yol açabilir. Bildirim hacmi düşük tutulmalı.
- **Tek oturum.** Aynı hat başka bir yerde WhatsApp Web'e bağlanırsa bu oturum
  düşebilir.

## Sorun giderme

```bash
docker logs e1-grid-whatsapp-web-gateway --tail 100
curl -s -H "Authorization: Bearer $INTERNAL_SERVICE_TOKEN" \
     http://localhost:8016/status
```

| Belirti | Muhtemel neden |
|---|---|
| `/status` bağlı değil diyor | Oturum düşmüş — `/qr` ile yeniden giriş gerekli |
| Container yeniden başlayınca QR isteniyor | `session-data` volume bağlanmamış |
| `401` | `SERVICE_TOKEN` backend'dekiyle uyuşmuyor |
| Mesaj gitmiyor, hata yok | Hedef numara formatı ya da hedef kişi hattı engellemiş |
