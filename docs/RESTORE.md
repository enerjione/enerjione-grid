# Veritabanı Geri Yükleme — Güvenlik Semantiği

Bu belge restore işleminin **ne zaman geri dönülemez hale geldiğini** ve bir
şey ters gittiğinde ne yapılacağını anlatır. Operasyonel bir belgedir;
mümkün olduğunca kısa tutulmuştur.

## Kısa cevap

> Restore, yedeğin gerçekten geri yüklenebildiği **kanıtlanana kadar** üretim
> veritabanına dokunmaz.

Yedek önce ayrı ve boş bir *staging* veritabanına yüklenir, orada şema
güncellenir ve doğrulanır. Üretim veritabanı ancak bunların tamamı geçtikten
sonra, saniyeler süren bir "geçiş" (cutover) adımında değiştirilir.

## Akış

```
0  ön kontrol      kilit · arşiv doğrulama · pg_restore --list · disk
1  staging         CREATE DATABASE <db>_stg_<iş> TEMPLATE template0   (BOŞ)
2  yükleme         pg_restore --single-transaction --exit-on-error
3  şema            DATABASE_URL=<staging> python -m scripts.migrate_db
4  doğrulama       tablolar · alembic sürümü · extension · hypertable
   ─────────────────────────────────────────────────────────────────
5  geçiş           ÜRETİME DOKUNAN İLK ADIM  (iki ALTER DATABASE RENAME)
6  son doğrulama   yeni üretim veritabanında aynı kontroller
```

**0–4 arasındaki her hata:** staging düşürülür, **üretim değişmemiştir**,
sistem çalışmaya devam eder.

## Restore üretime ne zaman dokunur?

Yalnızca **5. adımda**. O ana kadar üretim veritabanında tek bir `DROP`,
`ALTER` veya `INSERT` çalışmaz.

5. adım iki işlemden oluşur:

```sql
ALTER DATABASE enerjione_grid       RENAME TO enerjione_grid_pre_<zaman>;
ALTER DATABASE enerjione_grid_stg_N RENAME TO enerjione_grid;
```

Birincisi başarısız olursa hiçbir şey değişmemiştir. İkincisi başarısız
olursa eski veritabanı **sağlam** durur ve otomatik olarak geri alınır.

## Hata durumunda ne olur?

| Nerede hata | Üretim | Otomatik aksiyon | Operatör? |
|---|---|---|---|
| Kilit alınamadı (başka restore var) | değişmedi | reddedilir | hayır |
| Yedek bozuk / okunamıyor | değişmedi | staging bile yaratılmaz | hayır |
| Disk yetersiz | değişmedi | **hiç başlamaz** | evet (yer aç) |
| `pg_restore` düştü | değişmedi | staging silinir | hayır |
| Şema güncelleme düştü | değişmedi | staging silinir | hayır |
| Doğrulama düştü | değişmedi | staging **incelenmek üzere bırakılır** | evet |
| 1. rename düştü | değişmedi | — | hayır |
| 2. rename düştü | eski DB geri alınır | otomatik geri alma | hayır |
| Son doğrulama düştü | yeni DB aktif, eski korunur | **otomatik geri alma YOK** | **evet** |
| Güç kesintisi (staging sırasında) | değişmedi | artık staging bırakılır | evet (temizlik) |
| Güç kesintisi (iki rename arası) | `_pre_<zaman>` adında | tespit edilir, düzeltilmez | **evet** |

Son satır en kritik durumdur: `enerjione_grid` adında veritabanı yoktur ve
backend açılamaz. Veriler **kayıp değildir**; aşağıdaki kurtarma adımları
uygulanır.

## Eski veritabanı ne kadar tutulur?

Başarılı her restore'dan sonra bir önceki üretim veritabanı
`enerjione_grid_pre_<zaman>` adıyla **saklanır**.

- Otomatik olarak **silinmez**.
- Yalnızca **bir sonraki restore başarıyla tamamlandığında** (yani elde daha
  yeni bir geri dönüş noktası varken) eskisi düşürülür.
- Yer açmak için mevcut geri dönüş noktası **asla** silinmez. Disk yetersizse
  restore hiç başlamaz.

## Disk gereksinimi

Staging, üretim veritabanı kadar yer kaplar. **Yedek dosyasının boyutu ölçüt
değildir** — sahada ölçüldü: veritabanı 64 MB iken yedek dosyası 1,5 MB
(~42 kat fark). En büyük tabloların verisi yedeğe hiç girmez, dosya
sıkıştırılmıştır ve indeksler geri yükleme sırasında yeniden üretilir.

Hesap:

```
gerekli ≈ maks(canlı veritabanı boyutu, yedek × RESTORE_EXPANSION_FACTOR)
          × RESTORE_DISK_SAFETY
          + disk koruma rezervi (toplam diskin %10'u veya en az 5 GB)
```

Ayarlar (`.env`): `RESTORE_EXPANSION_FACTOR` (50), `RESTORE_DISK_SAFETY`
(1.5), `RESTORE_PG_TIMEOUT_SEC` (7200), `RESTORE_MIGRATE_TIMEOUT_SEC` (1800).

## Eski sürümden alınmış yedekler

Staging veritabanı **boş** yaratıldığı için eski bir yedek sorunsuz yüklenir:
yedekte olmayan yeni tablolar staging'de hiç oluşmaz ve şema güncellemesi
temiz bir ortamda çalışır.

Yedek **koddan daha yeni** bir şemadan geliyorsa (örneğin yeni sürümde
alınmış bir yedek eski sürüme yükleniyorsa) şema güncelleme adımı bunu
yakalar ve **geçiş yapılmadan** restore reddedilir.

## Elle kurtarma

### Durum tespiti

```bash
cd /opt/enerjione-grid
docker compose exec -T postgres psql -U enerjione_grid -d postgres -c "\l"
```

`enerjione_grid` listede **varsa** sistem çalışır durumdadır; artık kalan
`_stg_` veritabanları zararsızdır ve arayüzden temizlenebilir.

### `enerjione_grid` yoksa (yarım kalmış geçiş)

```bash
# 1. Uygulama servislerini durdur (postgres HARİÇ)
docker compose stop backend-api backend-worker

# 2. Geri dönüş veritabanını bul — en yeni _pre_ olanı
docker compose exec -T postgres psql -U enerjione_grid -d postgres -tAc \
  "SELECT datname FROM pg_database WHERE datname LIKE 'enerjione_grid_pre_%' ORDER BY 1 DESC;"

# 3. Geri al
docker compose exec -T postgres psql -U enerjione_grid -d postgres -c \
  'ALTER DATABASE "enerjione_grid_pre_<zaman>" RENAME TO "enerjione_grid";'

# 4. Doğrula
docker compose exec -T postgres psql -U enerjione_grid -d enerjione_grid -c \
  "SELECT version_num FROM alembic_version;"

# 5. Başlat
docker compose start backend-api backend-worker
```

### Geçiş sonrası doğrulama başarısızsa

Yeni veritabanı aktiftir ama doğrulamayı geçememiştir. Eski veritabanı
`_pre_<zaman>` adıyla durur. **Otomatik geri dönüş yapılmaz** — çünkü geçiş
ile doğrulama arasında yeni veritabanına yazılmış olabilir ve ikinci bir
yıkıcı takas körlemesine olurdu.

Geri dönmeye karar verirseniz yukarıdaki adımların aynısı uygulanır; yeni
veritabanı önce farklı bir adla saklanmalıdır:

```sql
ALTER DATABASE "enerjione_grid" RENAME TO "enerjione_grid_basarisiz_<zaman>";
ALTER DATABASE "enerjione_grid_pre_<zaman>" RENAME TO "enerjione_grid";
```

## Artık (orphan) staging veritabanları

Güç kesintisinden sonra `enerjione_grid_stg_<sayı>` adında veritabanları
kalabilir. Bunlar zararsızdır, yalnızca yer kaplar.

Sistem bunları **otomatik silmez**. Silinebilir sayılması için iki kanıt
birden gerekir: adın tam olarak beklenen kalıpta olması **ve** kalıcı restore
kayıt dosyasındaki iş kimliğiyle eşleşmesi. Eşleşmeyen bir veritabanı
operatöre gösterilir ama silinmez — yanlış bir `DROP DATABASE`in bedeli
birkaç yüz MB'lık artık alandan kat kat büyüktür.

## Eşzamanlı restore

Aynı anda iki restore çalışamaz. Koruma, `postgres` bakım veritabanında
tutulan bir PostgreSQL advisory kilididir; süreç ölürse bağlantı kapanır ve
kilit kendiliğinden serbest kalır. Backend birden fazla süreçle çalışsa bile
geçerlidir.

## Geçiş (cutover) penceresi — ölçülmüş

Üretim yığınının birebir aynısında (PostgreSQL 16.6 + TimescaleDB 2.17.2)
ölçüldü:

| | süre |
|---|---|
| 1. yeniden adlandırma (üretim → `_pre_`) | ~85 ms |
| 2. yeniden adlandırma (staging → üretim) | ~156 ms |
| **toplam geçiş penceresi** | **~385 ms** |

Bu, veritabanının erişilemez olduğu süredir. Ölçüm 64 MB'lık bir veritabanı
içindir; yeniden adlandırma işlemi veri boyutundan **bağımsızdır** (katalog
işlemidir), dolayısıyla büyük kurulumlarda da benzer kalır.

Bu pencerede:

- **Telemetri kaybolmaz** — aşağıya bakın.
- Alarm servisi bu sürede bir olay kaçırabilir; bu bir tasarım kabulüdür.
- IEC 104 / Modbus çıkışları SCADA'ya bu süre boyunca son bilinen değeri
  servis etmeye devam eder.

Bu değer sabit bir taahhüt (SLA) değildir; sisteme özgü ölçüm sonucudur ve
`test_cutover_penceresi_olculur` her koşumda yeniden ölçer.

## PostgreSQL istemci sürümü — dikkat

Yedek alan ve geri yükleyen `pg_dump`/`pg_restore` **sunucuyla aynı ana
sürümden** olmalıdır. Ölçüldü:

```
pg16 istemci → PG16 sunucu :  başarılı   (üretim yolu, imajda client 16 gömülü)
pg18 istemci → PG16 sunucu :  BAŞARISIZ
    ERROR: unrecognized configuration parameter "transaction_timeout"
```

Sistem en yüksek sürümlü istemciyi seçtiği için, cihaza sonradan daha yeni
bir PostgreSQL kurulursa yedekler geri yüklenemez hale gelir. Restore artık
bunu **başlamadan önce** yakalar ve açık bir hata verir; üretim veritabanına
dokunulmaz.

## Restore sırasında telemetri

Veritabanı erişilemez olduğunda telemetri tüketicisi mesajı **onaylamaz**
(ACK etmez); mesajlar NATS JetStream'de birikir ve geçiş tamamlandıktan sonra
işlenir. Yani geçiş penceresinde telemetri **kaybolmaz**, gecikir.
