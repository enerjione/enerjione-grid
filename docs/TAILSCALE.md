# Uzaktan Bakım VPN'i (Tailscale)

Saha cihazları kurulum anında otomatik olarak tailnet'e katılır. Böylece
müşteri ağında port açmadan, statik IP / DDNS gerektirmeden, NAT arkasından
uzaktan bakım yapılabilir.

Tailscale (WireGuard) **giden** bağlantı kurar; cihazda dinleyen port
**açılmaz**.

---

## 1. Tailscale tarafında yapılacaklar (bir kez)

> Admin konsolu: `https://console.tailscale.com/admin`
> (eski `login.tailscale.com` adresi de buraya yönlenir)

### 1.1 Etiketi ACL'e tanımla

`https://console.tailscale.com/admin/acls` → **Access controls**.
`tagOwners` bölümüne cihaz etiketini ekle:

```jsonc
{
  "tagOwners": {
    // Bu etiketi kimler cihazlara atayabilir
    "tag:e1-appliance": ["autogroup:admin"]
  },

  "acls": [
    // Ekibiniz sahadaki cihazlara erişsin
    {
      "action": "accept",
      "src":    ["autogroup:admin"],
      "dst":    ["tag:e1-appliance:*"]
    }
    // NOT: tag:e1-appliance'a giden kural YOK — cihazlar tailnet'te
    // başka bir şeye erişemez. Saha cihazı ele geçirilse bile yanal
    // hareket edemez.
  ],

  // Tailscale SSH — ayrı anahtar dağıtmaya gerek kalmaz
  "ssh": [
    {
      "action": "accept",
      "src":    ["autogroup:admin"],
      "dst":    ["tag:e1-appliance"],
      "users":  ["root", "autogroup:nonroot"]
    }
  ]
}
```

> Cihazlar `tag:e1-appliance` etiketiyle katıldığı için, kullanıcıya bağlı
> değildirler: personel ayrılsa bile cihazlar tailnet'te kalır. Etiketli
> cihazların anahtarı **süresiz**dir (key expiry uygulanmaz).

### 1.2 Anahtar üret

İki seçenek var; ikisi de çalışır.

**A) Auth key (en basit — buradan başla)**

`https://console.tailscale.com/admin/settings/keys`
→ **Generate auth key**

- ✅ **Reusable** (birden fazla cihaz aynı anahtarı kullanacak)
- ✅ **Pre-approved** (cihaz elle onay beklemesin)
- ✅ **Tags:** `tag:e1-appliance` ← **bunu işaretlemeyi unutma**
- Expiration: en fazla 90 gün

Çıkan `tskey-auth-...` değerini kullan.

> **90 gün ne anlama geliyor:** Sadece **yeni cihaz katılımını** etkiler.
> Etiketli (`tag:`) katılan cihazlarda anahtar süresi uygulanmaz — bir kez
> katılan cihaz süresiz bağlı kalır. Yani 90 günde bir anahtarı yenilemen
> yeterli, sahadaki cihazlar etkilenmez.

**B) OAuth client (süresi dolmaz — filo için)**

Tailscale bu bölümü **"Trust credentials"** adı altına taşıdı:

`https://console.tailscale.com/admin/settings/trust-credentials`
→ **OAuth clients → Generate**

**Adım 2 (Scopes)** ekranında `Custom scopes` seçiliyken:

1. **`Keys`** başlığını aç (daraltılmış gelir)
2. **Auth Keys** satırında → **Write** işaretle
3. Çıkan tag seçicide → **`tag:e1-appliance`**
4. **Başka hiçbir scope'u işaretleme.** DNS, Policy File, Users, Devices,
   Logging, Settings — hepsi kapalı kalsın. Bu credential yalnızca cihaz
   katılım anahtarı üretebilmeli; sızarsa tailnet'te başka bir şey yapamasın.
5. **Generate credential** → `tskey-client-...` değerini kopyala
   (**bir kere gösterilir**)

> **Tag seçicide `tag:e1-appliance` görünmüyorsa** ACL'de tanımlı değildir.
> Önce adım 1.1'deki `tagOwners` bloğunu kaydet, sonra bu ekrana dön.
> En sık takılınan nokta budur.

> Bölümü hiç göremiyorsan: tailnet'te **Owner/Admin** olman gerekir ve
> ayar *Personal settings* altında değil **Tailnet settings** altındadır.

---

## 2. Cihaz tarafı — anahtarı nereye koyacaksın

Kurulum anahtarı şu sırayla arar; ilk bulduğunu kullanır:

| # | Kaynak | Ne zaman |
|---|--------|----------|
| 1 | `E1_TAILSCALE_AUTHKEY` ortam değişkeni | Tek seferlik kurulum |
| 2 | `/etc/enerjione-grid/install.env` | **Sıfır dokunuşlu** (önerilen) |
| 3 | `<kurulum dizini>/.env` | Mevcut kurulumu güncellerken |

### Sıfır dokunuşlu kurulum (önerilen)

Anahtarı cihaza bir kez koy; sonraki tüm `install.sh` / `update.sh`
çalıştırmaları onu otomatik bulur. Disk imajına da gömülebilir:

```bash
sudo mkdir -p /etc/enerjione-grid
sudo tee /etc/enerjione-grid/install.env >/dev/null <<'EOF'
E1_TAILSCALE_AUTHKEY=tskey-client-BURAYA-ANAHTAR
E1_TAILSCALE_TAGS=tag:e1-appliance
E1_TAILSCALE_SSH=1
EOF
sudo chmod 600 /etc/enerjione-grid/install.env
```

Sonra normal kurulum — başka hiçbir şey yapmadan cihaz tailnet'e katılır:

```bash
sudo bash install.sh
```

> **Golden image akışı:** Bu dosyayı bir mini PC'ye yazıp diskin imajını
> alırsan, o imajdan çıkan her cihaz ilk açılışta kendiliğinden tailnet'e
> girer. Cihaz adı hostname'den gelir; her cihazın hostname'i farklı olmalı
> (`setup-appliance.sh` varsayılan olarak `e1-grid` yapar — filo için
> `APPLIANCE_HOSTNAME=e1-grid-023` gibi cihaza özel verin).

### Tek seferlik kurulum

```bash
E1_TAILSCALE_AUTHKEY=tskey-client-xxxx sudo -E bash install.sh
```

`sudo -E` önemli: ortam değişkenini root'a taşır.

---

## 2.1 Etiket unutulursa ne olur

Anahtarı üretirken **Tags** işaretlenmezse `--advertise-tags` reddedilir
(*"requested tags are invalid or not permitted"*). Kurulum bunu yakalar ve
**etiketsiz olarak tekrar dener** — cihaz yine tailnet'e katılır, kurulum
bozulmaz. Ama uyarı verir:

```
! Anahtar 'tag:e1-appliance' etiketini tasimiyor — etiketsiz deneniyor.
! Cihaz ETIKETSIZ katildi. Onerilen: anahtari 'tag:e1-appliance' etiketiyle
! yeniden uretip 'sudo tailscale up --advertise-tags=tag:e1-appliance' calistirin.
```

Etiketsiz katılmanın iki dezavantajı var:

1. Cihaz, anahtarı üreten **kullanıcıya** bağlanır — o kişi ayrılırsa cihaz
   düşer.
2. Anahtar süresi uygulanır: cihaz periyodik olarak yeniden yetkilendirme
   ister (varsayılan ~6 ay), sahada elle müdahale gerekir.

Bu yüzden anahtarı **mutlaka etiketle** üret.

---

## 3. Doğrulama

Kurulum çıktısında şunu görmelisin:

```
== Uzaktan bakim VPN'i (Tailscale)
  ✓ tailscale kuruldu.
  · Tailnet'e katiliniyor (hostname: e1-grid, etiket: tag:e1-appliance)...
  ✓ Tailnet'e katildi — 100.x.y.z
  ✓ Tailscale SSH acik (erisim tailnet ACL'i ile sinirli).
```

Cihazda:

```bash
tailscale status          # bagli mi, hangi IP
tailscale ip -4           # tailnet IP'si
```

Sizin tarafta: Tailscale admin konsolu → **Machines** → cihaz hostname'iyle
listede. Oradan:

- `ssh root@<hostname>` (Tailscale SSH — ACL'de izin verdiğin kadar)
- `http://<tailnet-ip>` → cihazın web arayüzü

---

## 4. Ayarlar

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `E1_TAILSCALE_AUTHKEY` | *(boş)* | **Boşsa VPN adımı hiç çalışmaz.** Anahtarsız kurulum etkilenmez. |
| `E1_TAILSCALE_TAGS` | `tag:e1-appliance` | Cihaz etiketi; ACL yetkisi buna göre |
| `E1_TAILSCALE_HOSTNAME` | sistem hostname'i | Tailnet'te görünecek ad |
| `E1_TAILSCALE_SSH` | `1` | Tailscale SSH (0 = kapalı) |
| `E1_TAILSCALE_ACCEPT_DNS` | `0` | **0 önerilir** — 1 olursa tailnet DNS'i cihazın yerel DNS'ini (AP dnsmasq, `e1-grid.local`) ezer |

---

## 5. Güvenlik notları

- **Anahtar bir tailnet anahtarıdır.** Repoya commit etmeyin — `.env` ve
  `/etc/enerjione-grid/install.env` git dışındadır. Sızarsa Tailscale
  konsolundan **hemen iptal edin** (Settings → Keys → Revoke).
- Cihazlar **etiketli** katılır; ACL'de `tag:e1-appliance`'tan **çıkan**
  kural tanımlamayın ki ele geçirilen bir saha cihazı tailnet'te yanal
  hareket edemesin.
- Tailscale SSH erişimi ACL ile sınırlıdır; cihaza ayrı SSH anahtarı
  dağıtmaya gerek yoktur. İstemiyorsanız `E1_TAILSCALE_SSH=0`.
- Kurulum idempotenttir: cihaz zaten tailnet'teyse yeniden giriş denenmez.
- Anahtar yoksa, root değilse veya internet yoksa script `exit 0` ile geçer;
  **kurulumu asla bozmaz**.

---

## 6. Cihazı tailnet'ten çıkarma

```bash
sudo tailscale logout
sudo systemctl disable --now tailscaled
sudo rm -f /etc/enerjione-grid/install.env   # tekrar katilmasin
```

Admin konsolundan da makineyi silin (**Machines → ... → Delete**).
