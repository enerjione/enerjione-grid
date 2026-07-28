# Debian Paketi (.deb) ile Dağıtım

Müşteriye giden dağıtım biçimi budur. Amaç: **müşteri depoya hiç erişmesin.**

## Paketin içinde ne var, ne yok

**Var** — yalnızca dağıtım katmanı:

```
/opt/enerjione-grid/     docker-compose.yml, .env.example, VERSION
                         install.sh, update.sh, uninstall.sh
                         infra/  (NATS şablonu, scriptler, appliance, nginx)
                         docs/   (saha kılavuzları)
/usr/bin/enerjione-grid  yönetim komutu
/lib/systemd/system/     enerjione-grid.service
```

**Yok** — uygulama kaynak kodu. `apps/` dizini pakete **girmez**; hem
`build-deb.sh` hem CI bunu bağımsız olarak kontrol eder ve sızarsa build'i
kırar.

Servis kodu `ghcr.io/enerjione/enerjione-grid/*` imajlarının içindedir.

> **Dürüst uyarı:** `.deb` kaynak kodu *korumaz*, sadece deponuzu verMEmenizi
> sağlar. Python kodu imajın içinde okunabilir durumdadır:
> `docker run --rm -it --entrypoint sh <imaj> -c 'cat app/main.py'`
> Gerçek koruma isteniyorsa ayrı bir adım gerekir (PyArmor vb.) — bkz. sonda.

## Kurulum (müşteri makinesi)

```bash
sudo apt install ./enerjione-grid_2.24.4_all.deb
sudo enerjione-grid setup
```

Birinci komut dosyaları yerleştirir, `.env`'i üretir (secret'lar rastgele),
systemd'yi tanıtır. **Ağır iş yapmaz** — `apt` dakikalarca kilitlenmez.

İkinci komut kurulum anahtarını sorar, imajları indirir ve sistemi başlatır.

## Yönetim

```bash
sudo enerjione-grid update           # en son yayına geç
sudo enerjione-grid update 2.24.4    # belirli sürüme dön (geri alma)
sudo enerjione-grid start|stop|restart
     enerjione-grid status           # servis durumu + sürüm
     enerjione-grid logs backend-api # canlı log
sudo enerjione-grid backup           # elle DB yedeği
```

Kurulumcunun `docker compose` veya `systemctl` bilmesine gerek yok.

## Paketi üretmek

```bash
bash packaging/build-deb.sh          # VERSION dosyasındaki sürüm
bash packaging/build-deb.sh 2.25.0   # açık sürüm
# çıktı: dist/enerjione-grid_<sürüm>_all.deb
```

Her `v*` tag'inde CI paketi üretip **GitHub Release'e asset olarak** ekler.
Ayrıca her PR'da paket üretilip temiz bir `debian:12-slim` konteynerinde
gerçekten kurulur — `postinst`/`prerm`/`postrm` hataları ancak böyle yakalanır,
sözdizimi kontrolü yetmez.

## Yükseltme davranışı

- **`.env` korunur.** Mevcut secret'ların üzerine yazmak çalışan sistemin
  veritabanı ve RabbitMQ kimliklerini bozar; `postinst` yalnızca dosya yoksa
  üretir.
- **`E1_VERSION` güncellenir** — imaj etiketi bundan çözülüyor.
- `docker-compose.yml` bilerek `conffile` **değil**: sürümle birlikte değişmesi
  gereken bir dosya, dpkg her yükseltmede "değiştirdiniz mi" diye sormamalı.

## Kaldırma

```bash
sudo apt remove enerjione-grid    # servisi durdurur, dosyaları kaldırır
sudo apt purge  enerjione-grid    # aynısı + paket ayarları
```

**Veri hiçbir durumda otomatik silinmez** — `purge`'de bile. Docker
volume'ları (veritabanı, yedekler, lisans) ve `.env` yerinde kalır. Bir saha
kurulumunda bunlar abonenin ölçüm geçmişidir; yanlışlıkla `purge` yazan bir
operatör geri alınamayacak bir kayıp yaşamamalı. Kalıcı silme açık ve ayrıdır:

```bash
sudo docker volume ls | grep enerjione-grid
sudo docker volume rm <volume-adı>
sudo rm -rf /opt/enerjione-grid
```

## Kurulum anahtarı

İmajlar private olduğu için cihaz `ghcr.io`'ya salt-okunur bir anahtarla
girer. `enerjione-grid setup` bunu bir kez sorar ve `.env`'e yazar (chmod
600); sonraki güncellemelerde bir daha sorulmaz.

Anahtar üretimi: GitHub → Settings → Developer settings → Personal access
tokens → **Fine-grained** → yalnızca `enerjione-grid` deposu → **Packages:
Read-only**. Depoya yazma yetkisi olan bir anahtar sahaya **asla** verilmez.

Otomatik kurulumda soru sorulmadan geçmek için:

```bash
sudo E1_GHCR_TOKEN=github_pat_xxx enerjione-grid setup
```

## Kaynak koruması — bir sonraki adım

Paket depoyu gizler ama Python kodu imajda okunabilir. Seçenekler, gerçekçi
değerlendirmeyle:

| Yöntem | Koruma | Bedeli |
|---|---|---|
| Sadece lisans (bugün) | Kod okunur; **kullanım** machine-id'ye bağlı | Yok |
| `.pyc` gönder | Çok zayıf, decompiler'lar mevcut | Düşük |
| PyArmor | İyi | Ticari lisans + build adımı |
| Nuitka/Cython | En güçlü | Yüksek risk: SQLAlchemy/Pydantic/Alembic introspection'ı kırılabilir |

Müşterinin makinesinde çalışan hiçbir kod tam korunamaz; hedef kopyalamanın
maliyetini işe yaramaz kılmaktır. Lisans doğrulaması (machine-id bağı) bu
zincirin en değerli halkasıdır — kod okunsa bile lisanssız çalışmaz.
