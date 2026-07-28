# Güvenlik Politikası

## Açık bildirimi

Bir güvenlik açığı bulduysanız **issue açmayın**. Açık, yamalanana kadar
sahadaki tüm kurulumları etkiler.

Bildirim: **security@formelektrik.com**

Bildirime şunları ekleyin:
- Etkilenen sürüm (`cat /opt/enerjione-grid/VERSION`)
- Tekrarlama adımları veya kavram kanıtı
- Sizce etkisi (veri erişimi, yetki yükseltme, servis dışı bırakma…)

Dönüş süresi hedefi: **3 iş günü içinde ilk yanıt**, kritik açıklarda
**7 gün içinde yama**.

## Desteklenen sürümler

Yalnızca **en son minor sürüm** güvenlik yaması alır. Saha kurulumlarını
güncel tutun:

```bash
cd /opt/enerjione-grid && sudo bash update.sh
```

## Bu depodaki gizli bilgiler

Depoda **hiçbir gerçek secret bulunmaz**. Bulursanız yukarıdaki adrese bildirin.

- Çalışma zamanı secret'ları `.env` dosyasındadır — `.gitignore`'da, asla
  commit edilmez.
- `.env` üretimde `chmod 600` ve kurulum kullanıcısına aittir.
- Uygulama içi hassas değerler (SMTP parolası, entegrasyon anahtarları)
  `app/services/secrets_vault.py` üzerinden şifreli saklanır.
- GHCR imaj çekme token'ı **salt-okunur** (`read:packages`) olmalıdır; saha
  cihazına depoya yazma yetkisi olan bir token asla dağıtılmaz.
- FCM service account JSON'u depoya girmez; kurulum yer tutucu üretir.

## Saha cihazı sertleştirme

Ayrıntı: `docs/security-roadmap.md` ve `docs/APPLIANCE.md`.

Özet:
- Container'lar `no-new-privileges`, `cap_drop: ALL`, `read_only` ile çalışır.
- RabbitMQ management arayüzü ve NATS monitoring **yalnızca localhost**'a açıktır.
- Backend 8000 portu host'a açılmaz; trafik nginx üzerinden geçer.
