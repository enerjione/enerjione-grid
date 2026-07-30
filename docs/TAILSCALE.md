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
| 3 | `<kurulum dizini>/.env` | Eski kurulumlarla uyumluluk |

`install.sh` ve `update.sh` bu sırayı **birebir aynı** uygular; kurulumda
tailnet'e katılan bir cihaz güncellemeden sonra anahtarsız kalmaz.

### Sıfır dokunuşlu kurulum (önerilen)

Anahtarı sunucuda elle yazmana gerek yok — **kurulum dosyasını üret**,
anahtarlar onun içine gömülür:

```bash
# kendi makinende, bir kez
bash packaging/make-provisioner.sh
# -> dist/enerjione-grid-kurulum.sh  (GHCR + Tailscale anahtarlari icinde)
```

Bu tek dosyayı hedef sunucuya kopyala ve çalıştır:

```bash
sudo bash enerjione-grid-kurulum.sh
```

Gerisi otomatik: anahtarları `/etc/enerjione-grid/install.env`'e (chmod 600)
yazar, `install.sh`'i private depodan çeker, kurulumu sürer ve cihaz saha
kimliğinden üretilmiş adıyla tailnet'e katılır.

> Üretilen dosya **gizlidir** — içinde canlı anahtarlar vardır. Depoya
> koymayın, e-posta ile göndermeyin. `.gitignore` bunu zaten engeller
> (`*-kurulum.sh`). Kurulumdan sonra `--wipe` ile sunucudan sildirebilirsiniz.
>
> Her müşteri/saha için ayrı anahtarlı dosya üretebilirsiniz:
> `bash packaging/make-provisioner.sh --out /tmp/musteri-a-kurulum.sh`

**Neden repoya gömmüyoruz:** depo private olsa da kurulum token'ı
(`E1_GHCR_TOKEN`) depoya okuma yetkisi verir — o token kimdeyse repodaki bir
anahtarı da çıkarabilirdi. Ayrıca git geçmişine giren anahtar oradan
silinemez; değiştirmek commit + tüm cihazlara dağıtım gerektirirdi. Üretilen
dosya yaklaşımında anahtar değiştirmek = dosyayı yeniden üretmek.

### Elle vermek istersen

```bash
sudo -E E1_TAILSCALE_AUTHKEY=tskey-client-xxxx bash install.sh
```

`install.sh` elle verilen anahtarı `/etc/enerjione-grid/install.env`'e
**kendisi kaydeder** (chmod 600, dosyadaki diğer ayarlar korunur); sonraki
kurulum ve güncellemelerde tekrar vermen gerekmez.

> **Golden image akışı:** `/etc/enerjione-grid/install.env` dolu bir mini PC'nin
> disk imajını alırsan, o imajdan çıkan her cihaz kendiliğinden tailnet'e
> girer. Cihaza özel **ad** için kurulumda sorulan saha kimliğini doldur
> (bkz. aşağıdaki bölüm); `ASSUME_YES=1` ile soru sorulmayan imajlarda
> `E1_CUSTOMER` / `E1_SITE` değişkenlerini ver.

### Cihaz adı: saha kimliğinden üretilir, sistem hostname'ine dokunulmaz

**Sorun:** Sistem hostname'i her cihazda aynı (`e1-grid`) — çünkü
`e1-grid.local` sahada standart erişim adresidir ve site başına tek cihaz
olduğu için yerel ağda çakışma yok. Ama tailnet **tek bir isim alanıdır**:
aynı adla katılan cihazları Tailscale `e1-grid-1`, `e1-grid-2`… diye
numaralandırır ve hangisinin hangi saha olduğu anlaşılmaz.

**Çözüm:** Sistem hostname'ine **dokunmuyoruz** (`e1-grid.local` çalışmaya
devam eder); Tailscale'e ayrı, cihaza özel bir ad veriyoruz.

#### Kurulumda sorulan saha kimliği (tercih edilen yol)

`install.sh`, Docker adımından **önce** üç soru sorar:

```
== Saha kimligi
  · Bu kutu uzaktan bakim listesinde bu adla gorunecek.
    Turkce yazabilirsiniz; teknik ad otomatik uretilir.
  ? Musteri / firma adi  Dicle EDAŞ
  ? Saha / proje adi  Şırnak Cizre
  ? Cihaz no (ayni sahada birden fazla kutu varsa)
  ✓ Saha kimligi kaydedildi: dicle-edas-sirnak-cizre
```

Türkçe karakterler otomatik çevrilir (`Ş→s`, `ı→i`, `Ğ→g`…), boşluklar tireye
iner. Sonuç `/etc/enerjione-grid/site.env` dosyasına yazılır:

```bash
E1_SITE_ID="dicle-edas-sirnak-cizre"
E1_CUSTOMER_NAME="Dicle EDAŞ"
E1_SITE_NAME="Şırnak Cizre"
E1_SITE_UNIT=""
```

Tailnet adı: **`e1-grid-dicle-edas-sirnak-cizre`**. Konsolda hangi cihazın
hangi müşteride olduğu doğrudan okunur — seri numarası ezberlemek gerekmez.

Dosya repo dışındadır; **kurulum/güncellemede silinmez**, soru bir daha
sorulmaz. Değiştirmek için:

```bash
sudo E1_SITE_FORCE=1 bash /opt/enerjione-grid/infra/appliance/setup-site-identity.sh
```

> **Aynı sahada birden fazla kutu varsa** üçüncü soruyu doldur (`Pano 2`) —
> `e1-grid-dicle-edas-sirnak-cizre-pano-2`. Boş bırakılırsa iki cihaz aynı adı
> alır ve Tailscale sonuna `-1`, `-2` ekler.

#### Soru sorulamayan kurulumlar

`ASSUME_YES=1`, golden image veya otomasyon: bilgiyi baştan ver, soru
sorulmaz.

```bash
# /etc/enerjione-grid/install.env  ya da  sudo -E ile ortam değişkeni
E1_CUSTOMER="Dicle EDAŞ"
E1_SITE="Şırnak Cizre"
E1_SITE_UNIT="Pano 2"     # opsiyonel
```

Hiçbiri verilmezse ve soru da sorulamıyorsa saha kimliği **atlanır** —
kurulum durmaz, ad donanımdan türetilir (aşağıda).

#### Yedek yol: donanımdan türetme

Saha kimliği yoksa ad otomatik üretilir:

| Sıra | Kaynak | Örnek sonuç |
|---|---|---|
| 1 | `E1_SITE_ID` (saha kimliği) | `e1-grid-dicle-edas-sirnak-cizre` |
| 2 | DMI seri no — Dell'de **Service Tag**, kasanın üzerindeki etiket | `e1-grid-7x2k9m3` |
| 3 | `/etc/machine-id` ilk 8 hane (her kurulumda benzersiz) | `e1-grid-abcdef12` |
| 4 | Hiçbiri yoksa düz önek *(uyarı verilir)* | `e1-grid` |

Üreticinin bıraktığı placeholder seri numaraları (`To Be Filled By O.E.M.`,
`Default string`, `0123456789`…) **benzersiz sayılmaz**, `machine-id`'ye düşer.
Sanal makinelerde genelde seri no bulunmaz — bu yüzden 2. sıraya güvenmek
yerine saha kimliğini doldurmak önerilir.

Ad DNS-güvenli hale getirilir (küçük harf, sadece harf/rakam/tire) ve 63
karakter sınırına kırpılır. Kurulum çıktısı adın nereden geldiğini yazar:

```
· Tailnet adi saha kimliginden uretildi: e1-grid-dicle-edas-sirnak-cizre
· Tailnet adi donanimdan turetildi: e1-grid-7x2k9m3
```

**Adı tamamen elle sabitlemek istersen** (saha kimliğini de ezer):

```bash
# /etc/enerjione-grid/install.env
E1_TAILSCALE_HOSTNAME=e1-batman-tpao-01
```

Ya da sadece öneki değiştir — cihaza özel kısım yine otomatik eklenir:

```bash
E1_TAILSCALE_HOSTNAME_PREFIX=e1-batman     # -> e1-batman-dicle-edas-sirnak-cizre
```

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
  · Tailnet adi saha kimliginden uretildi: e1-grid-dicle-edas-sirnak-cizre
  · Tailnet'e katiliniyor (hostname: e1-grid-dicle-edas-sirnak-cizre, etiket: tag:e1-appliance)...
  ✓ Tailnet'e katildi — 100.x.y.z
  ✓ Tailscale SSH acik (erisim tailnet ACL'i ile sinirli).
```

Ad `e1-grid` çıkıyorsa (uyarı verilir) saha kimliği tanımlı değildir ve
donanımdan da benzersiz bir değer okunamamıştır — ikinci cihaz katılınca
isimler karışır. Düzeltmek için:

```bash
sudo bash /opt/enerjione-grid/infra/appliance/setup-site-identity.sh
sudo tailscale up --hostname=e1-grid-<saha>   # adi hemen guncelle
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

### 3.1 SSH bağlanamıyorsanız

Üç ayrı sebep var; **sırayla** kontrol edin.

**1) Cihazda SSH açık mı?**

```bash
sudo tailscale debug prefs | grep RunSSH     # "RunSSH": true olmali
```

`false` ise:

```bash
sudo tailscale set --ssh
```

> `tailscale up --ssh` yalnızca **katılım anında** uygulanır. Bu bayrak
> eklenmeden önce kurulan cihazlarda SSH hiç açılmamıştı; `setup-tailscale.sh`
> artık her `install.sh`/`update.sh` çalıştırmasında bunu **kendisi düzeltir**
> (yeniden giriş gerekmez, anahtar da gerekmez).

**2) Cihaz etiketli mi katılmış?**

```bash
sudo tailscale debug prefs | grep AdvertiseTags   # ["tag:e1-appliance"] olmali
```

Boşsa ACL'deki `dst: ["tag:e1-appliance"]` kuralları bu cihaza **uymaz** ve
SSH açık olsa bile bağlantı reddedilir. Anahtarı doğru etiketle üretip:

```bash
sudo tailscale up --advertise-tags=tag:e1-appliance --ssh --reset
```

**3) Tailnet ACL'inde `ssh` bloğu var mı?** ← en sık atlanan adım

Bu **cihazdan yapılamaz**, tailnet politikasıdır. `tailscale up --ssh`
yalnızca "bu cihaz SSH kabul edebilir" der; **kimin** bağlanabileceğini ACL
belirler. `ssh` bloğu yoksa her bağlantı reddedilir.

`https://console.tailscale.com/admin/acls` → şu blok **bulunmalı**:

```jsonc
"ssh": [
  {
    "action": "accept",
    "src":    ["autogroup:admin"],
    "dst":    ["tag:e1-appliance"],
    "users":  ["root", "autogroup:nonroot"]
  }
]
```

- `src`: kimler bağlanabilir. `autogroup:admin` değilseniz kendi
  kullanıcınızı/grubunuzu yazın — aksi halde kendi cihazınızdan bağlanamazsınız.
- `users`: cihazda hangi yerel kullanıcıya girilebilir. `root` olmadan
  `ssh root@...` reddedilir.
- `"action": "check"` yazarsanız her bağlantıda tarayıcıdan yeniden doğrulama
  ister; otomasyon için `accept` kullanın.

**Bağlanan taraf:** Tailscale istemcisi kurulu ve **aynı tailnet'te** oturum
açmış olmalı. Bağlantı tailnet adıyla yapılır:

```bash
ssh root@e1-grid-dicle-edas-sirnak-cizre
```

---

## 4. Ayarlar

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `E1_TAILSCALE_AUTHKEY` | *(boş)* | **Boşsa VPN adımı hiç çalışmaz.** Verilirse `/etc/enerjione-grid/install.env`'e kaydedilir, bir daha vermen gerekmez |
| `E1_TAILSCALE_TAGS` | `tag:e1-appliance` | Cihaz etiketi; ACL yetkisi buna göre |
| `E1_TAILSCALE_HOSTNAME` | *(saha kimliği → donanım)* | Tailnet adını elle sabitle; her şeyi ezer |
| `E1_TAILSCALE_HOSTNAME_PREFIX` | `e1-grid` | Otomatik adın öneki (`<önek>-<saha>`) |
| `E1_TAILSCALE_SSH` | `1` | Tailscale SSH (0 = kapalı) |
| `E1_TAILSCALE_ACCEPT_DNS` | `0` | **0 önerilir** — 1 olursa tailnet DNS'i cihazın yerel DNS'ini (AP dnsmasq, `e1-grid.local`) ezer |

Saha kimliği (kurulumda sorulur, `/etc/enerjione-grid/site.env`):

| Değişken | Açıklama |
|---|---|
| `E1_CUSTOMER` | Müşteri / firma adı — verilirse o soru sorulmaz |
| `E1_SITE` | Saha / proje adı — verilirse o soru sorulmaz |
| `E1_SITE_UNIT` | Aynı sahada 2. kutu ise ayırt edici (`Pano 2`) |
| `E1_SITE_ID` | Üç alanı da atla, slug'ı doğrudan ver |
| `E1_SITE_FORCE=1` | Kayıtlı bilgi olsa bile yeniden sor |

---

## 5. Güvenlik notları

- **Anahtar bir tailnet anahtarıdır; repoya asla girmez.** `.env`,
  `/etc/enerjione-grid/install.env` ve üretilen `*-kurulum.sh` dosyaları
  `.gitignore`'dadır. Anahtarı repoya gömmeyin: kurulum token'ı
  (`E1_GHCR_TOKEN`) depoya okuma yetkisi verdiği için o token kimdeyse
  anahtarı da çıkarabilir, üstelik git geçmişinden silinemez.
- **Şüphe varsa revoke edin.** Tailscale konsolu → Settings → Keys → Revoke.
  Sonra `packaging/make-provisioner.sh` ile yeni anahtarlı kurulum dosyasını
  yeniden üretin; zaten kurulu cihazlar etkilenmez (`setup-tailscale.sh`
  idempotenttir, tekrar login denemez).
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
