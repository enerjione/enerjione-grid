# Appliance Modu — Mini PC Kurulumu

EnerjiOne Grid'i sahada tek basina calisan bir **cihaz** gibi kullanmak icin.
Mini PC acilir acilmaz:

1. Tum servisler (Docker + systemd) otomatik kalkar,
2. **`EnerjiOne Grid`** adinda **sifresiz** bir WiFi agi yayina girer,
3. Bu aga baglanan herkes **`http://e1-grid.local`** adresinden arayuze girer,
4. Kablolu (ethernet) IP/DNS ayari arayuzden yapilir; cihaz yeniden baslar ve
   yeni adresle gelir.

---

## 1. Donanim gereksinimleri

| Bilesen | Minimum | Not |
|---|---|---|
| CPU | 4 cekirdek x86_64 | Tum stack Docker'da calisir |
| RAM | 8 GB | Postgres + NATS + RabbitMQ + 6 servis |
| Disk | **500 GB SSD** | Historian + dakikalik ozet arsivi (asagiya bakin) |
| WiFi | **AP (master) modunu destekleyen** kart | Dahili karti yoksa USB adaptor |
| Ethernet | 1x | SCADA/kurumsal aga baglanti |

### Disk neden 500 GB?

Eskiden burada 128 GB yaziyordu; 600 cihazlik bir sahada bu YETMIYOR.
Kabaca kararli durum butcesi:

| Kalem | Yaklasik | Nasil sinirlaniyor |
|---|---|---|
| Dakikalik ozet arsivi (1 yil) | en buyuk kalem | TimescaleDB retention + sikistirma (migration 0023) |
| Ham telemetri (90 gun) | orta | retention + 7 gun sonrasi sikistirma |
| Saatlik ozet (2 yil) | kucuk | retention + sikistirma |
| NATS JetStream | 12 GiB | stream basina `max_bytes` + `discard=old` |
| `processed_messages` (24 saat) | kucuk | retention worker |
| Yedekler | birkac yuz MB | historian pg_dump'tan haric |
| Harita karolari | 4 GiB | onbellek tavani |

Bu kalemlerin HEPSI yanlis hesaplanmis olsa bile diskin dolmasini **disk
guard** engeller: toplam kapasitenin %10'u bos kalacak sekilde gercek bos
alani olcer, dolmaya yaklasilirsa once uyarir, sonra retention'lari
kisaltir, en son yeniden uretilebilir veriyi (harita onbellegi, fazla
yedekler) siler. Denetim kaydina, lisansa ve analiz verisine ASLA dokunmaz.
Ayarlar: `.env` icinde `DISK_GUARD_*`.

Daha kucuk diskle (orn. 128 GB) kurmak zorundaysaniz sistem yine calisir —
guard yuzde tabanli oldugu icin otomatik uyarlanir — ama dakikalik ozet
saklama suresini (`telemetry_history_1m` retention'i, migration 0023)
dusurmeniz gerekir.

WiFi kartinin AP modunu destekleyip desteklemedigini kontrol:

```bash
iw list | grep -A 10 "Supported interface modes"
# Ciktida "* AP" satiri olmali
```

Desteklemiyorsa AP kurulmaz (kurulum scripti uyarir); **Realtek RTL8188/8192**
veya **Atheros AR9271** tabanli ucuz USB adaptorler AP modunu destekler.

**Isletim sistemi:** Ubuntu Server 22.04/24.04 veya Debian 12 (minimal kurulum).

---

## 2. Kurulum — tek komut

```bash
TOKEN=ANAHTAR; curl -fsSL -H "Authorization: token $TOKEN" \n  https://raw.githubusercontent.com/enerjione/enerjione-grid/main/install.sh \n  | sudo E1_GHCR_TOKEN=$TOKEN bash
```

Bu tek komut her seyi kurar: Docker stack, systemd, **ve** appliance katmani
(WiFi AP + mDNS + ag ajani).

**Appliance modu otomatik secilir:** kurulum makinede WiFi karti arar.

| Makine | WiFi karti | Sonuc |
|---|---|---|
| Saha mini PC | var | Appliance modu **kurulur** (interaktifse varsayilan EVET ile onaylatilir) |
| Bulut sunucu / VPS | yok | Appliance modu **atlanir**, klasik sunucu kurulumu |

Karari elle zorlamak icin:

```bash
# WiFi adaptoru sonra takilacak — yine de kur
curl -fsSL ... | sudo E1_APPLIANCE=1 bash

# Test laptop'u — WiFi olsa bile kurma
curl -fsSL ... | sudo E1_APPLIANCE=0 bash
```

### Guncelleme

```bash
cd /opt/enerjione-grid && sudo bash update.sh
```

Appliance kurulu makinelerde `update.sh` **host katmanini da gunceller**
(ag ajani, systemd unit'leri, AP profili, DNS kaydi) — ayrica bir komut
gerekmez. Yayindaki AP kesintiye ugratilmaz. Zorlamak/kapatmak icin ayni
`E1_APPLIANCE=1` / `E1_APPLIANCE=0` degiskenleri gecerlidir.

Mevcut bir kurulumu sonradan appliance'a cevirmek:

```bash
cd /opt/enerjione-grid && sudo E1_APPLIANCE=1 bash update.sh
```

Sadece host katmanini elle calistirmak isterseniz (nadiren gerekir):

```bash
sudo bash infra/appliance/setup-appliance.sh
sudo docker compose up -d backend-api
```

Kurulum scriptinin yaptiklari:

| Adim | Ne yapar |
|---|---|
| Paketler | `network-manager`, `avahi-daemon`, `dnsmasq-base`, `iw` |
| Hostname | `e1-grid` → avahi ile `e1-grid.local` yayinlanir |
| Ag yoneticisi | netplan renderer'i **NetworkManager**'a cevirir (eski dosyalar `/var/backups/` altina yedeklenir) |
| Docker muafiyeti | `docker*`, `veth*`, `br-*` arayuzleri NM'den muaf tutulur (container aglari kopmasin) |
| WiFi AP | `e1-grid-ap` profili: SSID `EnerjiOne Grid`, sifresiz, 2.4 GHz kanal 6, `ipv4.method shared` → 10.42.0.1/24 DHCP |
| AP DNS | `e1-grid.local` → `10.42.0.1` (AP istemcileri mDNS'e muhtac kalmaz) |
| Ag ajani | `e1-netd` + systemd `path`/`timer` unit'leri |
| Paylasim dizini | `/var/lib/e1-grid/net` (root:10001, 0770) |

Env ile ozellestirme:

```bash
sudo AP_SSID="Saha Cihazi" AP_CHANNEL=11 APPLIANCE_HOSTNAME=e1-saha \
     bash infra/appliance/setup-appliance.sh

sudo SKIP_AP=1 bash infra/appliance/setup-appliance.sh   # WiFi karti yoksa
```

---

## 3. Ilk kullanim

1. Telefon/laptop WiFi listesinden **EnerjiOne Grid** agina baglanin (sifre yok).
2. Tarayicidan **`http://e1-grid.local`** acin.
   Acilmazsa: **`http://10.42.0.1`** (AP'nin sabit adresi — her zaman calisir).
3. `installer` hesabiyla giris yapin (ilk sifre kurulum ciktisinda yazar).
4. **Muhendislik > Sistem > Ag Ayarlari** sayfasindan kablolu IP'yi ayarlayin.

> `.local` adresi: Windows 10+ ve iOS/macOS mDNS'i destekler. Android'de bazi
> tarayicilar desteklemez — AP uzerinden bagliyken sorun olmaz, cunku AP'nin
> kendi DNS'i bu ismi zaten cozer. Kablolu agdan baglanan Android istemciler
> icin IP kullanin.

---

## 4. IP/DNS ayari nasil calisir

```
  Tarayici
     │  PUT /api/v1/network/config        (rol: SADECE installer)
     ▼
  backend-api (container, uid 10001)
     │  /var/lib/e1-grid/net/request.json yazar   ← container'in TEK yetkisi
     ▼
  e1-netd.path (systemd, host)  →  e1-netd.service (root)
     │  1. istegi kendi kurallariyla YENIDEN dogrular
     │  2. nmcli ile ethernet profiline uygular
     │  3. status.json + state.json yazar
     │  4. `systemctl reboot`
     ▼
  Cihaz yeni IP ile acilir
```

**Neden ayri ajan?** Backend container'i non-root calisir ve host agina
erisimi yoktur. Alternatif (`privileged` + `network_mode: host`) container ele
gecirildiginde tum makineyi kaybetmek demekti. Bu tasarimda container yalnizca
bir JSON dosyasi yazabilir; ne uygulanacagina host'taki ajan karar verir.

**Ajanin reddettikleri** (backend'den bagimsiz, ikinci savunma hatti):

- ethernet olmayan arayuzler (WiFi AP profiline dokunulamaz),
- gecersiz IPv4 / prefix (1-32 disi),
- IP ile ayni alt agda olmayan gateway (cihazi erisilemez yapar),
- ag adresi veya broadcast adresinin host IP'si olarak verilmesi,
- AP alt agi (`10.42.0.0/24`) ile cakisan araliklar,
- arayuz adinda kabuk enjeksiyonu denemesi.

### Guvenlik agi

**WiFi AP her zaman aciktir ve bu sayfadan degistirilemez.** Yanlis statik IP
girip cihazi kablolu agdan erisilemez yapsaniz bile:

1. `EnerjiOne Grid` agina baglanin,
2. `http://e1-grid.local` (veya `http://10.42.0.1`) acin,
3. Ag Ayarlari'ndan duzeltin.

Arayuze hic erisilemiyorsa host'tan:

```bash
sudo nmcli connection modify e1-grid-eth ipv4.method auto
sudo reboot
```

---

## 5. Tanilama

```bash
# Ag durumu (backend'in okudugu dosya)
cat /var/lib/e1-grid/net/state.json | python3 -m json.tool

# Son uygulama sonucu
cat /var/lib/e1-grid/net/status.json

# Ajan logu
sudo journalctl -u e1-netd -n 50
sudo journalctl -u e1-netd-report -n 20

# Unit'ler ayakta mi?
systemctl status e1-netd.path e1-netd-report.timer

# AP durumu
nmcli connection show e1-grid-ap
nmcli device wifi list                 # cevredeki aglar
iw dev <wlan> station dump             # AP'ye bagli istemciler

# mDNS yayini
avahi-browse -at | grep e1-grid
```

### Sik karsilasilan durumlar

| Belirti | Sebep / cozum |
|---|---|
| Arayuzde "Appliance modu kurulu degil" | `setup-appliance.sh` calistirilmamis veya backend eski mount ile ayakta → `sudo docker compose up -d backend-api` |
| "Ag ajani henuz hic durum bildirmemis" | `systemctl start e1-netd-report.service` ve logu kontrol edin |
| AP gorunmuyor | Kart AP modunu desteklemiyor olabilir (`iw list`); `rfkill list` ile radyo kapali mi bakin |
| `e1-grid.local` acilmiyor | AP'ye bagliyken `http://10.42.0.1`; kablolu agda IP kullanin |
| Ayar kaydedildi ama IP degismedi | `status.json` icindeki `error` alanina bakin; ajan istegi reddetmis olabilir |
| Sayfa "uygulaniyor" da takildi | `request.json` duruyor demektir; `journalctl -u e1-netd` + gerekirse dosyayi silin |

---

## 6. Kaldirma

```bash
cd /opt/enerjione-grid && sudo bash uninstall.sh
```

Appliance kurulu ise ayrica sorar: onaylarsaniz ag ajani, systemd unit'leri,
WiFi AP profili ve `/var/lib/e1-grid` silinir. **Hostname ve netplan renderer
degisikligi otomatik geri alinmaz** (sistemde baska seyleri etkileyebilir);
uninstall ciktisi geri alma komutlarini ve netplan yedeginin yerini yazar.

---

## 7. Sinirlar

- **IPv4** yapilandirilir; IPv6 AP'de kapali, ethernet'te sisteme birakilir.
- **Sadece ethernet** ayarlanabilir. Mini PC'yi mevcut bir WiFi agina *istemci*
  olarak baglamak bu surumde yok (ayni kart AP modundayken zaten mumkun degil;
  ikinci bir adaptor gerekir).
- AP **sifresizdir** — guvenlik fiziksel erisim kontrolune dayanir. Sifre
  eklemek icin: `nmcli connection modify e1-grid-ap wifi-sec.key-mgmt wpa-psk
  wifi-sec.psk "<parola>"` (setup scripti tekrar calisirsa acik aga geri
  ceker; kalici istiyorsaniz scripti de guncelleyin).
- Ayni anda tek istek islenir; onceki uygulanmadan ikincisi reddedilir.
