# Saha Kurulum Kılavuzu — Mini PC

Ubuntu yüklü bir mini PC'ye EnerjiOne Grid'i sıfırdan kurmak için adım adım
kılavuz. Teknik bilgi gerektirmez; komutları olduğu gibi kopyalayıp
yapıştırmanız yeterli.

> **Süre:** 20–40 dakika (internet hızına göre)
> **Sonuç:** Cihaz açıldığında her şey kendiliğinden çalışır, elle bir şey
> başlatmanız gerekmez.

---

## Başlamadan önce

Elinizde olması gerekenler:

- [ ] **Ubuntu Server 22.04 / 24.04** (veya Debian 12) yüklü mini PC
- [ ] **İnternet bağlantısı** — kurulum sırasında dosyalar indirilecek
      (kurulumdan sonra internet gerekmez)
- [ ] Mini PC'ye bağlı **klavye + ekran**, ya da başka bir bilgisayardan
      **SSH** erişimi
- [ ] Kurulumu yapacak kullanıcının **sudo** yetkisi

Donanım olarak en az: **4 çekirdek işlemci, 8 GB RAM, 128 GB SSD**, bir
**ethernet** portu ve **WiFi kartı**.

> **WiFi kartı neden gerekli?** Cihaz kendi WiFi ağını yayınlar. Sahada
> kablolu ağ olmasa bile telefonunuzla bu ağa bağlanıp arayüze girersiniz.
> Kart yoksa kurulum yine tamamlanır, sadece bu WiFi özelliği olmaz.

---

## Adım 1 — İnterneti kontrol edin

Mini PC'de terminali açın ve şunu yazın:

```bash
ping -c 3 github.com
```

**Görmeniz gereken:** `3 packets transmitted, 3 received` gibi bir satır.

Hata alıyorsanız ethernet kablosunu takın ya da bilinen bir WiFi ağına
bağlanın. İnternet olmadan kurulum yapılamaz.

---

## Adım 2 — Kurulum komutunu çalıştırın

Aşağıdaki **tek satırı** olduğu gibi kopyalayıp terminale yapıştırın ve
Enter'a basın:

```bash
TOKEN=ANAHTAR; curl -fsSL -H "Authorization: token $TOKEN" \n  https://raw.githubusercontent.com/enerjione/enerjione-grid/main/install.sh \n  | sudo E1_GHCR_TOKEN=$TOKEN bash
```

> Kısa adres çalışmazsa (DNS kaydı henüz yapılmamışsa) uzun hâlini kullanın:
> ```bash
> TOKEN=ANAHTAR; curl -fsSL -H "Authorization: token $TOKEN" \n  https://raw.githubusercontent.com/enerjione/enerjione-grid/main/install.sh \n  | sudo E1_GHCR_TOKEN=$TOKEN bash
> ```

Sudo şifrenizi soracaktır; kullanıcı şifrenizi yazın (yazarken ekranda
görünmez, normaldir).

### Kurulum size ne soracak?

Üç soru sorulur. **Hepsinde sadece Enter'a basmanız yeterlidir** — köşeli
parantezteki büyük harf varsayılan cevaptır (`[E/h]` → varsayılan Evet).

| Soru | Cevap |
|---|---|
| `Kuruluma baslansin mi?` | **Enter** (Evet) |
| `Appliance modu kurulsun mu?` | **Enter** (Evet) — WiFi ağı ve `e1-grid.local` bunu gerektirir |
| `systemd servisi olarak kaydedilsin mi?` | **Enter** (Evet) — açılışta otomatik başlaması için |

Soru başına 5 dakika süre vardır; cevap gelmezse varsayılan uygulanır ve
kurulum devam eder, takılıp kalmaz.

> **Hiç soru istemiyorsanız** komutu şöyle çalıştırın — hepsine otomatik
> Evet denir:
> ```bash
> curl -fsSL https://raw.githubusercontent.com/enerjione/enerjione-grid/main/install.sh | sudo ASSUME_YES=1 bash
> ```

---

## Adım 3 — Kurulumun bitmesini bekleyin

Ekranda mavi **ENERJIONE GRID** yazısı ve altında adımlar akar:

```
[1/6] Pre-req paketler kontrol ediliyor...
[2/6] Repo hazirlaniyor: /opt/enerjione-grid
[3/6] Docker Engine + Compose plugin kontrolu...
[4/6] .env hazirlaniyor (secret'lar rastgele uretilir)...
[5/6] Servisler build ediliyor ve ayaga kaldiriliyor...
[6/6] Backend hazir olana kadar bekleniyor (max 2 dk)...
```

**5. adım en uzunu** — 3 ile 8 dakika sürer, ekran bir süre hareketsiz
görünebilir. Bu normaldir, beklemeye devam edin.

Kurulum bitince yeşil bir kutu göreceksiniz:

```
============================================================
  Kurulum tamamlandi.
============================================================

  Web arayuzu :  http://192.168.1.50/
  Ilk giris:
    Kullanici : installer
    Sifre     : ChangeMe123!    <<< MUTLAKA DEGISTIR
```

📸 **Bu ekranın fotoğrafını çekin** — yazan IP adresi lazım olacak.

---

## Adım 4 — Arayüze ilk giriş

### Yol A — Cihazın kendi WiFi'si üzerinden (önerilen)

1. Telefonunuzun WiFi listesini açın.
2. **`EnerjiOne Grid`** adlı ağa bağlanın — **şifre yoktur**.
3. Tarayıcıdan şu adresi açın:

   ```
   http://e1-grid.local
   ```

   Açılmazsa şunu deneyin (bu adres her zaman çalışır):

   ```
   http://10.42.0.1
   ```

### Yol B — Kablolu ağ üzerinden

Adım 3'te not ettiğiniz IP adresini tarayıcıya yazın, örneğin
`http://192.168.1.50/`

### Giriş bilgileri

| | |
|---|---|
| Kullanıcı | `installer` |
| Şifre | `ChangeMe123!` |

⚠️ Giriş yapar yapmaz **şifre değiştirme ekranı** otomatik açılır. Yeni bir
şifre belirleyin ve **güvenli bir yere not edin**. Bu şifre unutulursa
sıfırlamak için teknik destek gerekir.

---

## Adım 5 — Kablolu ağ ayarı

Cihazı kurumun ağına sabit bir IP ile bağlamak için:

1. Üst menüden **Mühendislik** sekmesine girin.
2. **Sistem** menüsünü açın → **Ağ Ayarları**.
3. **Adres Yöntemi** bölümünden seçim yapın:
   - **Otomatik (DHCP)** — ağdaki modem/router adresi kendi verir. Basit
     kurulumlar için bunu seçin.
   - **Statik IP** — sabit adres verirsiniz. Kurum size bir IP verdiyse bunu
     seçin ve IP, ağ maskesi, ağ geçidi, DNS alanlarını doldurun.
4. **Kaydet ve yeniden başlat** düğmesine basın.
5. Onay ekranı çıkar; onaylayın. **Cihaz yeniden başlar** (1–2 dakika).

> **Yanlış adres girerseniz ne olur?** Cihazın WiFi ağı **her zaman açık
> kalır** ve bu sayfadan kapatılamaz. Kablolu ağdan erişemezseniz telefonla
> `EnerjiOne Grid` ağına bağlanıp `http://10.42.0.1` adresinden ayarı
> düzeltebilirsiniz. Cihaz asla tamamen erişilemez hale gelmez.

---

## Adım 6 — Otomatik çalışmayı test edin

Cihazın elektrik kesintisinden sonra kendiliğinden açıldığını **mutlaka**
doğrulayın:

```bash
sudo reboot
```

2–3 dakika bekleyin, sonra tarayıcıdan arayüzü tekrar açın. **Giriş ekranı
geliyorsa test başarılıdır** — elle hiçbir şey başlatmanıza gerek yok.

### Neden otomatik açılıyor?

Kurulum bunu iki katmanda garantiler:

| Katman | Ne yapar |
|---|---|
| Docker servisi | İşletim sistemiyle birlikte açılır (`systemctl enable docker`) |
| Uygulama servisleri | 12 servisin hepsi `restart: unless-stopped` ile işaretli — Docker açılınca hepsini geri getirir |
| systemd kaydı | `enerjione-grid` servisi açılışta etkin; ek güvence ve kolay yönetim sağlar |

Yani elektrik gidip gelse, cihaz kapanıp açılsa bile sistem kendi kendine
ayağa kalkar.

---

## Kontrol listesi

Kurulumu bitirmeden önce hepsini işaretleyin:

- [ ] Arayüze giriş yapabiliyorum
- [ ] `installer` şifresini değiştirdim ve not ettim
- [ ] Kablolu ağ ayarını yaptım, cihaza kablolu ağdan da erişebiliyorum
- [ ] `sudo reboot` sonrası sistem kendiliğinden açıldı
- [ ] Telefonla `EnerjiOne Grid` WiFi'sine bağlanıp arayüzü açabiliyorum

Durumu terminalden görmek isterseniz:

```bash
cd /opt/enerjione-grid
sudo docker compose ps
```

**Görmeniz gereken:** tüm satırlarda `Up ... (healthy)` yazması.

---

## Güncelleme

Yeni sürüm çıktığında:

```bash
cd /opt/enerjione-grid
sudo bash update.sh
```

Bu komut güncellemeden önce **otomatik veritabanı yedeği** alır, sonra
yeni sürümü kurar. Bitince tarayıcıda **Ctrl + Shift + R** tuşlarına basın.

---

## Sorun giderme

### Kurulum "İnternet yok" gibi bir hatayla durdu

Ethernet kablosunu kontrol edin, `ping -c 3 github.com` ile tekrar deneyin,
sonra kurulum komutunu **baştan çalıştırın**. Kurulum yarıda kalsa bile
tekrar çalıştırmak güvenlidir, kaldığı yerden devam eder.

### `EnerjiOne Grid` WiFi ağı görünmüyor

WiFi kartı "erişim noktası" modunu desteklemiyor olabilir. Kontrol:

```bash
iw list | grep -A 10 "Supported interface modes"
```

Çıktıda `* AP` satırı yoksa kart desteklemiyordur. Bu durumda cihaza kablolu
ağdan IP ile erişirsiniz; sistem yine çalışır. Kalıcı çözüm için AP modunu
destekleyen bir USB WiFi adaptörü takıp şunu çalıştırın:

```bash
cd /opt/enerjione-grid
sudo bash infra/appliance/setup-appliance.sh
```

### `http://e1-grid.local` açılmıyor

`http://10.42.0.1` adresini deneyin (WiFi ağına bağlıyken her zaman çalışır).
Bazı Android telefonlar `.local` adreslerini desteklemez.

### Arayüz açılmıyor / boş geliyor

```bash
cd /opt/enerjione-grid
sudo docker compose ps
```

`Up (healthy)` yazmayan bir servis varsa logunu alın ve teknik desteğe
gönderin:

```bash
sudo docker compose logs --tail 50 backend-api
```

### Güncelleme "commit edilmemiş yerel değişiklik var" diyor

```bash
cd /opt/enerjione-grid
git status --short
```

Çıkan dosyaları teknik desteğe bildirin. Kendiliğinden bir şey silmeyin.

---

## Daha fazlası

Bu kılavuz saha kurulumunu kapsar. Teknik ayrıntı gerekirse:

- **[APPLIANCE.md](APPLIANCE.md)** — WiFi AP ve ağ ajanının nasıl çalıştığı,
  tanılama komutları, sınırlar
- **[DEPLOYMENT.md](DEPLOYMENT.md)** — sunucu/VPS kurulumu, nginx, SSL,
  alan adı yapılandırması
