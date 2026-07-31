# Production Denetim Raporu — 2026-07-31

> Dal: `fix/disk-retention-ve-sema-sertlestirme` · Sürüm: 2.25.0
> Yöntem: 8 eksende bağımsız kod taraması + her bulgu için ayrı bir "çürütmeye çalışan" doğrulayıcı ajan.
> 51 iddia üretildi → **42 doğrulandı**, 3 çürütüldü, 6 doğrulanamadı. **16 tanesi sahaya çıkmayı engelleyici.**

Bu rapor `docs/PRODUCTION-HAZIRLIK.md` ile çakışmaz: o dosya süreç/ops maddelerini,
bu dosya **kod seviyesindeki** bulguları takip eder.

---

## Zemin gerçeği (çalıştırılarak doğrulandı)

| Kontrol | Sonuç |
|---|---|
| `pytest` (apps/backend-api) | 265 geçti, 4.4 sn |
| `tsc --noEmit` (apps/frontend-web) | temiz |
| Alembic zinciri | 0001 → 0023 düz, dallanma yok |

İki uyarı:

- **Test kapsamı aldatıcı.** 34.487 satır backend koduna 3.516 satır test (~%10).
  35 router'ın hiçbirinde endpoint testi yok (`TestClient` yalnızca `test_license_gate.py`'de).
  tag-engine, alarm-service, notification-worker, ftp-server, whatsapp-web-gateway: **sıfır test**.
  Frontend: **sıfır test**. Aşağıdaki bulguların hiçbiri mevcut testlerle yakalanamazdı.
- **`requirements.txt` lock dosyası değil.** İçi sürüm *aralıkları*, hash yok; dosyanın kendi
  başlığı bunu itiraf ediyor. `Dockerfile:46-50` hash bulamayınca sessizce `--require-hashes`'siz
  kuruyor. Altı servisin hepsinde aynı. Aynı tag'den iki build farklı bağımlılık sürümü içerebilir.
  (CLAUDE.md "hash-locked" diyor — bugün değil.)

---

## A. Sahaya çıkmadan önce — ENGELLEYICI (16)

> **Durum: 16/16 kapatıldı (2026-08-01).** Her madde ayrı commit'te; hepsinin
> arkasında davranış testi var (sözdizimi/typecheck değil). CI 7 job'dan
> 9'a çıktı: `alarm-service` ve `iec104-outbound` daha önce **hiç**
> koşmuyordu — A5 ve A7'nin aylarca fark edilmemesinin sebebi tam da buydu.
>
> Yerel makinede doğrulanamayan iki alan bilerek CI'a bağlandı:
> `O_NOFOLLOW` gerektiren symlink testleri (A11/A13) Windows'ta atlanıyor,
> gerçek doğrulama Linux CI'da yapılıyor.

### A1. [KRITIK] pg_restore kendi baglantisini oldurtuyor: PGAPPNAME Popen'dan SONRA set ediliyor

- [x] **Yer:** `apps/backend-api/app/services/backup_service.py:713`
- **Nedir:** Restore sirasinda worker baglantilarini temizleyen dongu, pg_restore'un kendi baglantilarini da kill eder cunku onu koruyacak olan PGAPPNAME ortam degiskeni surec baslatildiktan SONRA env sozlugune yaziliyor ve cocuk surece hic gecmiyor.
- **Risk:** Installer UI'dan bir yedegi geri yukler. pg_restore baslar; application_name'i 'pg_restore' olur (libpq fallback), 'e1_%' pattern'ine UYMAZ. 1.5 saniye sonra _terminate_loop ilk turunu atar ve --jobs=4 ile acilmis 5 pg_restore baglantisinin hepsini pg_terminate_backend ile kapatir. pg_restore 'server closed the connection unexpectedly' ile rc!=0 doner. --single-transaction KULLANILMADIGI icin rollback yoktur: DB, --clean asamasinda DROP edilmis tablolarla YARIM kalir. Restore her denemede ayni yerde patlar; sistem geri yuklenemez ve mevcut veri de gitmistir. docs/PRODUCTION-HAZIRLIK.md A2 'geri yukleme hic denenmedi' diyor — denendiginde bu bug ile karsilasilacak.
- **Düzeltme:** PGAPPNAME'i Popen'dan ONCE ata (env["PGOPTIONS"] satirinin hemen yanina tasi). Ayrica PGOPTIONS icine '-c application_name=e1_restore_session' eklemek ikinci bir emniyet olur. Bonus: _terminate_loop icindeki psql_path = os.getenv("PSQL", "psql") yerine resolve_pg_binary("psql") kullanilmali; aksi halde Windows/native kurulumda dongu sessizce hic calismaz.

### A2. [KRITIK] Her cihaz ayni sabit 'installer/ChangeMe123!' hesabiyla sahaya cikiyor ve backend sifre degisimini ZORLAMIYOR

- [x] **Yer:** `apps/backend-api/scripts/seed_installer.py:29`
- **Nedir:** Her kurulum repo'da acik yazan ayni varsayilan en yetkili hesabi yaratiyor; `must_change_password` yalnizca yanit govdesinde donen bir bayrak, hicbir backend dependency'si onu kontrol etmiyor, dolayisiyla login TAM YETKILI bir JWT + oturum cookie'si veriyor.
- **Risk:** Kurulum bitti, muhendis daha ilk login'i yapmadi (veya modali kapatip birakti). Ayni ag/internet uzerinden (frontend-web host'un 80 portunda, docker-compose.yml:672) biri `POST /api/v1/auth/login {username:"installer", password:"ChangeMe123!"}` atiyor. 200 + gecerli installer JWT aliyor. SPA modali devre disi kaldi cunku istemci hic calistirilmadi. Bu token ile installer rolunun her seyi yapilabiliyor: gateway token'larini okumak (gateways.py), API key uretmek, uzaktan bakim kapisini acmak, yedekten geri yukleme, kullanici yaratmak. install.sh:590-596 parolayi kuruluma bakan herkesin gordugu ekrana da basiyor.
- **Düzeltme:** (1) `get_current_user`'a (veya bir `require_password_changed` dependency'sine) `must_change_password` kontrolu ekle: bayrak True iken yalnizca `/auth/me` ve `/auth/change-password` gecsin, digerleri 403 `PASSWORD_CHANGE_REQUIRED` donsun. (2) Sabit parola yerine kuruluma ozel rastgele parola uret (install.sh `openssl rand`), ekrana bir kez bas ve DB'ye hash'ini yaz.

### A3. [YUKSEK] Operator tum alarmlari toplu onaylayip/resetleyip silebiliyor (kapsam yalnizca YANITTA uygulaniyor)

- [x] **Yer:** `apps/backend-api/app/api/alarms.py:97`
- **Nedir:** `/alarms/events/ack-all` ve `/alarms/events/reset-all` mutasyonu TUM alarmlara uygular; `scope_service` filtresi sadece donen listeye uygulandigi icin operator kendi sorumluluk alani disindaki alarmlari da kapatir ve resetlenmis olanlari kalici olarak SILER.
- **Risk:** Hicbir sorumluluk alanina atanmamis bir `operator` (get_visible_device_ids -> bos set) `POST /api/v1/alarms/events/ack-all` cagirir. Sistemdeki 600 cihazin tum acik alarmi onaylanir, reset=true olanlar yorumlariyla birlikte DB'den silinir; yanit bos liste dondugu icin UI'da hicbir sey olmamis gorunur. Ardindan `reset-all` ile tum acik alarmlar da resetlenir -> `fault_recompute` sonrasi haritadaki gercek arizalar kaybolur ve olay kaydinda yalnizca tek bir 'Tüm alarmlar onaylandı' satiri kalir.
- **Düzeltme:** Kapsami servise indir: `acknowledge_all_alarms` / `reset_all_alarms` fonksiyonlarina `visible_device_ids: set[int] | None` parametresi ekleyip `list_alarm_events` sorgusunu `AlarmEvent.device_id.in_(visible)` ile daralt. Ayrica toplu silme operator icin tamamen kapatilmali (tek-kayit DELETE zaten ENGINEER/INSTALLER).

### A4. [YUKSEK] GET /gateways operator'a gateway token'ini duz metin donuyor -> sahte telemetri enjeksiyonu

- [x] **Yer:** `apps/backend-api/app/api/gateways.py:100`
- **Nedir:** Gateway listesi `OPERATOR` rolune acik ve `GatewayRead` semasi `token: str` alanini duz metin tasiyor; bu token `POST /telemetry/gateway/{code}` icin tek kimlik dogrulama unsuru oldugundan operator, kendisine acikca yasaklanmis olan telemetri enjeksiyonunu yapabilir.
- **Risk:** Operator `GET /api/v1/gateways` cagirir, yanittan `token` degerini alir. `POST /api/v1/telemetry/gateway/GW01` + `X-Gateway-Token: <token>` ile kendi alani disindaki cihazlar icin uydurma deger gonderir: alarm-service kural motorunu tetikleyip sahte kritik ariza uretir veya `fault_indicator` degerini normal gondererek gercek arizayi maskeler. Ayni token ile `GET /gateways/{code}/config` cagrilarak sahadaki tum cihazlarin IP/DNP3 adres listesi de sizar. `POST /telemetry` uzerindeki rol kontrolu tamamen atlanmis olur.
- **Düzeltme:** `GatewayRead`den `token`i cikar (yerine `token_prefix`/`has_token` bool koy) veya en azindan `list_gateways`i ENGINEER/INSTALLER ile sinirla ve token'i yalnizca INSTALLER'a acik `GET /{code}/docker-compose` yanitinda birak.

### A5. [YUKSEK] alarm-service _SAMPLES buffer'ı 512M container limitini kaçınılmaz olarak aşıyor (OOM restart döngüsü)

- [x] **Yer:** `apps/alarm-service/alarm_service/main.py:464`
- **Nedir:** Her telemetri okuması, composite/agg kuralı olsun olmasın, (sinyal, cihaz) başına 5000 örneklik bir deque'e yazılıyor; is_alarmable() tüm sinyal kataloğuna True döndüğü için filtre yok ve steady-state bellek kodun kendi yorumuna göre GB'larla ifade ediliyor.
- **Risk:** 200 cihaz x 20 aktif sinyal = 4000 anahtar. Her sinyal ~10 sn'de bir güncellenirse her deque ~14 saatte maxlen=5000'e doyar → 20M örnek tuple, kabaca 1.5-2 GB. Container tavanı 512M olduğu için alarm-service birkaç saat içinde OOM-kill yer. restart: unless-stopped onu geri kaldırır ama _STATE (aktif alarm durumu) bellekte olduğu için sıfırlanır: açık alarmlar yeniden 'yeni alarm' olarak backend'e POST edilir (mükerrer bildirim seli) ve bu her birkaç saatte bir tekrarlanır. TTL temizliği yalnızca 24 saattir veri gelmeyen anahtarları atar; aktif cihazlarda hiçbir şey serbest bırakmaz.
- **Düzeltme:** _SAMPLES.put'u koşullu yapın: yalnızca en az bir composite/agg kuralının referans verdiği (signal_key) için örnek biriktirin — AlarmRuleCache zaten expression'ları parse ediyor, oradan bir `agg_keys` seti çıkarılabilir. Ek olarak MAX_PER_KEY'i en uzun agg penceresine göre boyutlayın (24 saat/5000 sabiti yerine).

### A6. [YUKSEK] NATS başlangıçta erişilemezse JetStream bus kalıcı olarak ölü kalıyor, tekrar deneme yok

- [x] **Yer:** `apps/backend-api/app/services/jetstream_bus.py:474`
- **Nedir:** start_bus_if_enabled() tek bir deneme yapıyor; başarısız olursa _bus=None kalıyor ve hiçbir kod yolu onu yeniden başlatmıyor, bu yüzden outbox'taki her telemetri yayını backend elle yeniden başlatılana kadar sonsuza dek RuntimeError alıyor.
- **Risk:** NATS container'ı çöker/yeniden başlatılır ve tam o sırada backend de yeniden başlar (OOM kill, `update.sh backend`, watchdog). depends_on: service_healthy yalnızca `compose up` sıralaması için geçerlidir, restart'ta uygulanmaz — backend NATS hazır olmadan kalkar, bus.start() False döner ve _bus None'da kilitlenir. Bundan sonra NATS saniyeler içinde sağlıklı hale gelse bile outbox_flush_worker her turda RuntimeError alır: telemetri hiç yayınlanmaz, cihazlar arayüzde 'Kesik' görünür, outbox_events tablosu published=False satırlarla sınırsız büyür (purge_published_outbox bunlara dokunmaz). /health 503 döner ama compose'da autoheal yoktur ve `restart: unless-stopped` healthcheck'e tepki vermez — kimse başında olmadığı için cihaz aylarca veri kaydetmeden ayakta kalır.
- **Düzeltme:** start_bus_if_enabled()'i idempotent bir yeniden-deneme haline getirin ve outbox_flush_worker döngüsünde (veya ayrı bir küçük supervisor thread'inde) `if get_bus() is None: start_bus_if_enabled()` çağırın; alternatif olarak `_bus`'ı başarısız durumda da saklayıp bus.start()'ı periyodik tekrar deneyin.

### A7. [YUKSEK] IEC 104 oturumu k-penceresinde kalıcı olarak kilitlenebiliyor — SCADA çıkışı sessizce ölüyor

- [x] **Yer:** `apps/iec104-outbound/iec104_outbound/server.py:424`
- **Nedir:** session.unacked yalnızca gelen bir S-frame ile sıfırlanıyor; sunucu tarafında t1/t3 zamanlayıcısı, okuma timeout'u veya TEST_ACT keepalive'ı olmadığı için yarı-açık bir bağlantıda sayaç 12'de takılı kalır ve o oturuma giden tüm spontane veri süresiz olarak düşürülür.
- **Risk:** SCADA master ile arada kalan bir switch/router yeniden başlar veya master process'i donar: TCP bağlantısı yarı-açık kalır, FIN gelmez. Sunucu 12 I-frame gönderdikten sonra S-frame alamaz; unacked 12'de sabitlenir. _send_i artık writer'a hiçbir şey yazmadığı için TCP de kopukluğu fark edemez (SO_KEEPALIVE ayarlı değil, sunucu TEST_ACT göndermiyor). Oturum self._sessions içinde 'started=True' olarak kalır, tüm telemetri değişimleri sessizce düşer — SCADA'da veriler donar, IEC104 çıkışı ölür ve container restart edilene kadar kendiliğinden düzelmez. Ayrıca her düşen frame için rate-limit'siz WARNING basılır; 600 cihazlık yükte log dosyalarını saniyeler içinde döndürüp diğer tüm teşhis loglarını süpürür.
- **Düzeltme:** Oturum başına bir t3 (idle) zamanlayıcısı ekleyip TEST_ACT gönderin ve cevap gelmezse oturumu kapatıp _sessions'tan düşürün; ayrıca `reader.read` çağrısını asyncio.wait_for ile bir t1 timeout'una bağlayın. k_window_full logunu oturum başına rate-limit'leyin (ws_broadcaster'daki last_warn_at deseni).

### A8. [YUKSEK] update.sh imajlar dogrulanmadan .env'e yeni E1_VERSION yaziyor — yarida kalan guncelleme cihazi ilk reboot'ta olduruyor

- [x] **Yer:** `update.sh:265`
- **Nedir:** Yeni surum tag'i checkout edilir edilmez .env'deki E1_VERSION yeni surume cekiliyor; imaj cekme VE yerel derleme ikisi de basarisiz olursa update e1_die ile duruyor ama .env geri alinmiyor, dolayisiyla bir sonraki `docker compose up` var olmayan imaj etiketini ariyor.
- **Risk:** Trafo merkezindeki cihazda 4G hattinda `sudo bash update.sh` calisir. `docker compose pull` GHCR'a erisemez, fallback `docker compose build` de base imajlari (python:3.11-slim, node:20-alpine) Docker Hub'dan cekemedigi icin basarisiz olur -> e1_die. Eski container'lar calismaya devam ettigi icin operator sorunu fark etmez. Ilk elektrik kesintisinden sonra systemd `docker compose up -d` kosar, .env'deki E1_VERSION=2.26.0 imajlari ne yerelde ne registry'de bulunur, unit failed olur ve cihaz sahada tamamen olu kalir.
- **Düzeltme:** E1_VERSION'i .env'e yazmayi imajlar hazir olduktan SONRAYA (_e1_prepare_images basarili donduktan sonra) al; ya da eski degeri bir degiskende tut ve e1_die/ERR trap yolunda .env'i eski surume geri yaz (git checkout'u da onceki HEAD'e dondur).

### A9. [YUKSEK] uninstall.sh --purge-dir uzaktan bakim kapisini KALICI ACIK birakiyor

- [x] **Yer:** `uninstall.sh:90`
- **Nedir:** Appliance temizligi yalnizca e1-netd unit'lerini kaldiriyor; e1-rad (ve e1-gwd) unit'leri sistemde kaliyor, --purge-dir ile ajan betigi silindigi icin suresi dolan izni kapatacak hicbir sey kalmiyor ve tailnet dugumu kalkani inik halde yayinda kaliyor.
- **Risk:** Musteri sahadan cihazi geri cekerken aktif bir uzaktan bakim izni acikken (shields-up=false + Tailscale SSH acik) `sudo bash uninstall.sh --yes --purge-dir` (belgelenmis "full nuke") calistirir. e1-rad-report.timer enabled kalir ama /opt/enerjione-grid/infra/appliance/e1-rad.py silinmistir; birim her 30 sn'de status=203/EXEC ile duser, hicbir kapanma yapilmaz. Cihaz tailnet'e kayitli, kalkani inik ve root SSH acik halde musterinin agina bagli kalir; uygulama kaldirilmis oldugu icin arayuzden "geri al" da yapilamaz.
- **Düzeltme:** uninstall.sh'a e1-rad/e1-gwd temizligi ekle: unit'leri stop+disable+rm et ve dizin silinmeden ONCE `"$SCRIPT_DIR/infra/appliance/e1-rad.py" close || true` calistir (kalkani kaldirir, SSH'i kapatir). Ayrica tailnet'ten dusme/dusurmeme karari kullaniciya sorulup `tailscale logout` opsiyonu sunulmali.

### A10. [YUKSEK] 0019/0023'te yutulan hata tum migration transaction'ini abort ediyor — backend hic boot etmiyor

- [x] **Yer:** `apps/backend-api/alembic_migrations/versions/2026_07_31_0001-0019_repair_historian_policies.py:130`
- **Nedir:** `_try(...)` PostgreSQL hatasini yakalayip yutuyor ama rollback/SAVEPOINT yapmiyor; env.py tum migration'lari TEK transaction'da kostugu icin ilk hatadan sonra transaction 'aborted' hale gelir ve alembic'in kendi `UPDATE alembic_version` ifadesi dahil sonraki HER ifade patlar.
- **Risk:** docker-compose.yml:95-100'de belgelenen senaryo: postgres:16'dan devralinmis dolu bir volume'de shared_preload_libraries yoksa `CREATE EXTENSION timescaledb` "could not access file" ile patlar. 0019 bunu `_try` ile yutup return eder, ama transaction artik ABORTED'dir; 0020-0023 ve alembic'in `UPDATE alembic_version SET version_num='0019'` ifadesi InFailedSqlTransaction ile patlar -> `python -m scripts.migrate_db` exception firlatir -> backend container CMD basarisiz -> sonsuz crash-loop. Yani migration tam da onarmak icin yazildigi sahalari tuglalar. Ayni yol 0023'te KESIN olarak tetiklenir: kod "tam form" ALTER'in bazi TimescaleDB surumlerinde reddedilecegini BEKLEYIP `if not ok:` ile "sade form"a dusuyor (0023:145-161) — ama ilk hata transaction'i abort ettigi icin fallback de, sonraki add_compression_policy/add_retention_policy de, alembic'in version update'i de patlar.
- **Düzeltme:** env.py'de `context.configure(..., transaction_per_migration=True)` ver VE `_try` icini SAVEPOINT'e al: `with op.get_bind().begin_nested(): fn()` (veya except blogunda `op.get_bind().rollback()` yerine nested transaction). Boylece tek adimin patlamasi digerlerini ve alembic_version guncellemesini bozmaz — "en iyi caba" politikasi ancak o zaman gercekten calisir.

### A11. [YUKSEK] Root ajan, container'in yazabildigi dizinde symlink takibiyle dosya aciyor (yetki sinirini deler)

- [x] **Yer:** `infra/appliance/e1-rad.py:227`
- **Nedir:** e1-rad root olarak `state.json.tmp` / `status.json.tmp` dosyalarini O_NOFOLLOW olmadan aciyor; bu tmp yollari backend container'in (uid/gid 10001) yazabildigi paylasilan dizinde, dolayisiyla container onceden symlink birakip root'a istedigi host dosyasini truncate + yazdirabilir.
- **Risk:** Backend container'da RCE/dosya-yazma alan bir saldirgan (tasarimin kendi tehdit modelinde GUVENILMEYEN taraf; bu yuzden lease.json ayri 0700 dizinde tutuluyor) `ln -s /etc/systemd/system/e1-rad-report.service /var/lib/e1-grid/remote/state.json.tmp` yapar. 30 sn icinde e1-rad-report.timer root olarak cmd_report() kosar, symlink'i takip eder ve unit dosyasini JSON ile ezer. Sonraki daemon-reload/boot'ta sure-dolunca-kapatma zorlayicisi olu olur — yani ozelligin TEK guvenlik garantisi sessizce devre disi kalir. Ayni primitif /etc/shadow, /etc/enerjione-grid/e1-rad.env veya /etc/cron.d altina yonlendirilerek host'u bozmak/kalicilastirmak icin de kullanilabilir. Ek olarak `os.replace` sonrasi symlink kaybolacagi icin iz de birakmaz.
- **Düzeltme:** tmp dosyasini paylasilan dizinde degil, yalnizca root'un erisebildigi PRIV_DIR icinde uret (ayni dosya sistemi, os.replace calisir): `tmp = os.path.join(PRIV_DIR, os.path.basename(path) + ".tmp")`. Bu mumkun degilse en azindan `os.O_NOFOLLOW | os.O_EXCL` ekleyip acmadan once `_remove(tmp)` cagir. Ayni sozlesmeyi paylasan e1-netd.py / e1-gwd icin de kontrol edin.

### A12. [YUKSEK] update.sh her guncellemede tam DB dump'i birakiyor; rotasyon yok ve historian haric tutulmuyor

- [x] **Yer:** `update.sh:146`
- **Nedir:** Guncelleme oncesi alinan `backups/auto-pre-update-*.sql.gz` dosyalari hicbir zaman silinmiyor, hicbir retention'a tabi degil ve backup_service'in yedekten cikardigi historian/telemetry tablolarini tam olarak iceriyor.
- **Risk:** Cihaz surum 2.25'ten 2.30'a kadar 5 kez guncellenir. Her update.sh calismasinda --exclude-table-data OLMADAN tam bir pg_dump alinir; 90 gunluk historian ile birlikte her dosya birkac GB'tir. Bu dosyalar compose proje dizinindeki ./backups altina yazilir — docker-compose.yml'deki BACKUP_DIR=/var/lib/e1-backups (backup-data volume) ile FARKLI bir yerdir, dolayisiyla ne apply_retention ne reindex_backup_jobs_from_disk ne de UI onlari gorur. Hicbir mekanizma silmez. Birkac guncelleme sonrasi 500 GB'lik diskin buyuk kismi bu dosyalarla dolar; postgres-data ayni dosya sisteminde oldugu icin sonunda Postgres yazamaz hale gelir. Ayrica bu dosyalar plain SQL oldugu icin validate_dump_file'in PGDMP magic kontrolune takilir — UI'dan geri de yuklenemezler, yani sadece yer kaplarlar.
- **Düzeltme:** Dump'i BACKUP_DIR volume'una (veya en azindan rotasyonlu bir dizine) yaz, `-F c` custom format kullan (UI'dan geri yuklenebilsin), backup_service.EXCLUDED_DATA_TABLES ile ayni --exclude-table-data listesini uygula ve dump sonrasi en yeni N (or. 3) disindaki auto-pre-update-* dosyalarini sil. Ek olarak yazmadan once `df` ile bos alan kontrolu yap.

### A13. [YUKSEK] e1-netd ve e1-gwd root ajanlari, container'in yazabildigi dizinde symlink takip ederek YAZIYOR (root dosya ustune yazma primitifi)

- [x] **Yer:** `infra/appliance/e1-netd.py:528`
- **Nedir:** Her iki ajanin `_write_json`'i `open(tmp, "w")` ile aciyor; tmp yolu backend container'inin (uid 10001) yazabildigi paylasimli dizinde, dolayisiyla container oraya symlink koyarak root'a istedigi dosyayi truncate/uzerine yazdirip chmod 0640 + chown root:10001 yaptirabiliyor.
- **Risk:** Backend container'i ele geciriliyor (RCE ya da zaten raporlanmis bir yetki asimi). Saldirgan `/var/lib/e1-grid/net/state.json.tmp` -> `/etc/systemd/system/e1-netd.service` symlink'i yaratiyor. 30 saniye sonra e1-netd-report.timer root olarak calisiyor, `open(...,"w")` symlink'i takip edip unit dosyasini truncate ediyor, `os.chmod`/`os.chown` hedefi degistiriyor, `os.replace` symlink'i state.json'a tasiyor. Ayni yontemle `/etc/passwd`, `/etc/sudoers.d/*` veya baska bir systemd unit'i bozulabiliyor/uzerine yazilabiliyor — cap_drop/no-new-privileges/read_only sertlestirmesinin tamami bu tek satirdan asiliyor. Ozellikle yikici hali: her 30 sn'de bir cihazi acilamaz hale getirmek (saha ziyareti).
- **Düzeltme:** Her iki ajanda `_write_json`'i e1-rad'in otesine tasi: `fd = os.open(tmp, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, mode)` kullan (O_EXCL onceden konmus symlink/dosyayi reddeder), `os.fchmod(fd, ...)`/`os.fchown(fd, ...)` ile fd uzerinden izin ver, ve `_read_json`'i da `os.open(..., os.O_RDONLY|os.O_NOFOLLOW)` yap. Alternatif olarak tmp dosyasini ajanin kendi root-only dizininde uret, sadece son `os.replace`'i paylasimli dizine yap.

### A14. [ORTA] BACKUP_OFFSITE_DIR Docker'da tamamen olu — off-site yedek hic alinmiyor

- [x] **Yer:** `docker-compose.yml:329`
- **Nedir:** backup_service off-site kopyayi `os.getenv("BACKUP_OFFSITE_DIR")` ile aciyor, fakat degisken backend-api environment blogunda yok (env_file de yok) ve hedef dizin icin bir volume mount da tanimli degil; yani .env'e deger yazan operator icin bile off-site kopya sessizce hic calismiyor.
- **Risk:** Operator .env'e `BACKUP_OFFSITE_DIR=/mnt/nas/backups/enerjione-grid` yazar ve NAS'i host'a mount eder. `_offsite_copy` best-effort oldugu ve degisken container'da bos oldugu icin fonksiyon hemen `return` eder — ne hata, ne uyari, ne job.error_message. Aylar sonra saha cihazinin diski bozulur veya kriptolocker'a yakalanir; tek yedek kopyasi ayni diskteki backup-data volume'undadir ve o da gitmistir. Operator var sandigi ikinci kopyanin hic olusmadigini ancak felaket aninda ogrenir.
- **Düzeltme:** docker-compose.yml backend-api'ye `BACKUP_OFFSITE_DIR: ${BACKUP_OFFSITE_DIR:-}` env'ini ve degisken doluyken host yolunu container'a baglayan bir bind mount ekleyin (orn. `- ${BACKUP_OFFSITE_DIR:-/dev/null}:/var/lib/e1-backups-offsite`, env'i container ici sabit yola cevirerek). Ayrica `_offsite_copy` env bos oldugunda en azindan bir kez INFO/WARN loglasin ki 'yapilandirdim ama calismiyor' durumu gorunur olsun.

### A15. [ORTA] install.sh FTP_PASSWORD uretmiyor — her temiz kurulumda ftp-server sonsuz restart dongusune giriyor

- [x] **Yer:** `install.sh:344`
- **Nedir:** Secret uretim ve placeholder dogrulama listelerinde FTP_PASSWORD yok; .env.example'da deger bos oldugu icin ftp-server her acilista SystemExit(2) ile oluyor ve `restart: unless-stopped` altinda sonsuza kadar yeniden baslatiliyor.
- **Risk:** Sahada `curl ... | sudo bash` ile temiz kurulum yapilir; kurulum "TAMAMLANDI" der. e1-grid-ftp-server container'i acilir acilmaz exit 2 verir ve Docker onu sonsuza kadar yeniden baslatir. Horstmann SN2 cihazlari config/firmware'i yukleyemez (baglanti reddedilir) ve sebep hicbir kurulum ciktisinda gorunmez; ayrica her `update.sh` sonunda "1 servis calismiyor gorunuyor" uyarisi kalici hale gelir ve gercek arizalari maskeler.
- **Düzeltme:** install.sh'ta diger secret'lar gibi `FP=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24); _ensure_env_var "FTP_PASSWORD" "$FP"` ekle ve FTP_PASSWORD'u placeholder sanity-check dongusune dahil et. Ek olarak compose'da `${FTP_PASSWORD:?FTP_PASSWORD .env'de olmali}` kullanip sessiz crash-loop yerine acik hata verilsin.

### A16. [ORTA] 0021: processed_messages BIGINT rewrite'i saha cihazini boot edemez duruma dusurebilir

- [x] **Yer:** `apps/backend-api/alembic_migrations/versions/2026_07_31_0003-0021_widen_hot_table_pk_to_bigint.py:85`
- **Nedir:** Budama 10M satirla tavanlanmis; kalan ~170M satirlik tabloda `ALTER TABLE ... TYPE BIGINT` tam tablo+index yeniden yazimi yapar, `lock_timeout=30s` ile kilit alamazsa hata firlatir ve bu hata YUTULMADIGI icin backend hic ayaga kalkmaz.
- **Risk:** Eski 7 gunluk TTL ile ~180M satirlik processed_messages: budama tavani yalnizca 10M satir siler, ~170M satir kalir. (a) Budamanin urettigi olu satirlar autovacuum'u tetikler; autovacuum'un ShareUpdateExclusiveLock'i ALTER'in ACCESS EXCLUSIVE talebiyle catisir -> 30sn'de lock_timeout -> migration exception -> migrate_db patlar -> backend crash-loop; her yeniden baslatmada ayni sey tekrarlanir (autovacuum saatlerce surebilir). (b) Kilit alinsa bile ~170M satirlik tablo+UNIQUE index yeniden yazimi diskte tablonun ~2 kati bos alan ister; bu dalin varlik sebebi zaten diskin dolmasiysa ENOSPC ile patlar ve yine boot engellenir. Her iki durumda da uzaktan erisimin zor oldugu bir saha cihazi acilmaz.
- **Düzeltme:** Budamayi tavansiz yap (silinecek satir kalmayana kadar dongu; her tur ayri commit zaten var) ki ALTER neredeyse bos tabloda kossun. Ek olarak `_widen`'i lock_timeout hatasina karsi kisa geri-cekilmeli retry'a al ve ALTER oncesi `pg_total_relation_size` vs. `shutil.disk_usage` karsilastirmasi yapip yer yoksa anlamli bir hata ile dur (ya da budamayi tamamlayip bir sonraki boot'a birak) — boot'u kalici bloklama.

---

## B. Engelleyici değil ama düzeltilmeli (26)

### B1. [YUKSEK] Harita karosu servisi paylasilan senkron threadpool'u blokluyor; 'cevrimdisi' korumasi tam da onbellek isabetsizliginde devreden cikiyor

- [ ] **Yer:** `apps/backend-api/app/services/map_tile_service.py:354`
- **Nedir:** `serve_tile` senkron bir endpoint ve icinde 20 sn timeout'lu `requests` cagrisi var; `_mark_offline()` cooldown'u yalnizca 331. satirdaki yolu koruyor, onbellekte olmayan karo icin 354'te KOSULSUZ tekrar yukari akisa gidiliyor.
- **Düzeltme:** (1) 354. satirdaki fallback'i de guard'a al: `if not _upstream_ok(): raise MapTileError("MAP_TILE_OFFLINE", ...)` — cooldown boyunca aninda 404 don, Leaflet bos karo gosterir. (2) Karo endpoint'ini `async def` yapip `await anyio.to_thread.run_sync(...)`'i AYRI ve kucuk bir `CapacityLimiter` ile kosturarak API'nin geri kalanindan izole et. (3) `map_tile_timeout_sec` varsayilanini connect icin 3-5 sn'ye indir (`timeout=(3, 20)`).

### B2. [YUKSEK] FTP volume'u hicbir bilesenin okumadigi, budanmayan ve disk_guard'in erisemedigi sinirsiz bir veri kuyusu

- [ ] **Yer:** `docker-compose.yml:627`
- **Nedir:** `ftp-data` volume'u yalnizca ftp-server'a bagli; backend/frontend onu ne okuyor ne de temizliyor, ve disk_guard'in acil seviye temizliginde bu volume icin hicbir kaldirac yok — 0.0.0.0:21'e acik tam yetkili tek hesap cihazi disk dolduruncaya kadar yazabiliyor.
- **Düzeltme:** (1) ftp-server'a volume kotasi/temizlik ekle: periyodik olarak `FTP_RETENTION_DAYS`'ten eski dosyalari sil ve toplam boyut tavani uygula. (2) `ftp-data`'yi backend'e read-only mount edip disk_guard'a acil seviyede en eski FTP dosyalarini budayan bir adim ekle (map tile/backup ile ayni desen). (3) Volume kullanimi ve dosya sayisi system_status'e cikarilsin ki dolmadan once gorulsun.

### B3. [ORTA] Operator kendine API key uretip /public/* uzerinden tum sahayi okuyabiliyor (scope_service tamamen atlaniyor)

- [ ] **Yer:** `apps/backend-api/app/api/api_keys.py:50`
- **Nedir:** `POST /api-keys` yalnizca `get_current_user` ile korunuyor (rol kontrolu yok) ve `/public/*` endpoint'lerinin hicbiri sorumluluk alani filtresi uygulamiyor; boylece operator uretilen token ile tum cihaz/telemetri/alarm verisine erisiyor.
- **Düzeltme:** `create_api_key`e `Depends(require_roles([UserRole.INSTALLER, UserRole.ENGINEER]))` ekle; ayrica `/public/*` sorgularina `ctx.user` uzerinden `get_visible_device_ids` filtresini uygula ki gelecekte operator'a key verilse bile kapsam korunsun.

### B4. [ORTA] Sifre degisimi ve admin sifre sifirlamasi mevcut oturumlari iptal etmiyor

- [ ] **Yer:** `apps/backend-api/app/api/auth.py:373`
- **Nedir:** `POST /auth/me/change-password` ve `POST /users/{id}/reset-password` yalnizca `hashed_password`i gunceller; `UserSession.revoked_at` set edilmez ve `revoke_jti` cagrilmaz, dolayisiyla calinmis JWT token'i sifre degistikten sonra da 7 gune kadar gecerli kalir.
- **Düzeltme:** Her iki akista da hedef kullanicinin `user_sessions` satirlarini (`revoked_at = now()`) kapat ve `revoke_jti` ile in-memory blacklist'e ekle; change-password'de istege bagli olarak istegi yapan oturumu haric tut, admin reset'te ise istisnasiz tumunu iptal et.

### B5. [ORTA] GET /telemetry/latest kapsam filtresi uygulamiyor

- [ ] **Yer:** `apps/backend-api/app/api/telemetry.py:25`
- **Nedir:** `/telemetry/latest` yalnizca `get_current_user` ile korunuyor ve `list_latest_telemetry` tum cihazlarin son 200 olcumunu kapsam filtresi olmadan donuyor; ayni verinin diger yollari (`/signals/live`, WS `/ws/live-values`) operator icin daraltiliyor.
- **Düzeltme:** `list_latest_telemetry(db, visible_device_ids)` imzasina gecip `Telemetry.device_id.in_(visible)` filtresini SQL'e ekle; `visible is None` (installer/engineer/ops_manager) durumunda mevcut davranisi koru.

### B6. [ORTA] alarm-service: bloklayan HTTP/AMQP çağrıları asyncio event loop'unun içinde çalışıyor

- [ ] **Yer:** `apps/alarm-service/alarm_service/main.py:659`
- **Nedir:** NATS push consumer'ın async callback'i, içinde senkron requests.post (timeout=8) ve pika BlockingConnection publish yapan senkron kural motorunu doğrudan çağırıyor; event loop bu süre boyunca tamamen donuyor.
- **Düzeltme:** _on_message içinde bloklayan işi loop'tan çıkarın: `await asyncio.to_thread(_process_rules_for_payload_jetstream, payload)` (telemetry_consumer.py'de backend tarafında zaten kullanılan desen). Alternatif olarak requests/pika çağrılarını tek bir worker thread + queue'ya taşıyın.

### B7. [ORTA] Bildirim ayarlari GET'i SMTP/SMS/Telegram sirlarini duz metin dondururyor

- [ ] **Yer:** `apps/backend-api/app/api/notification_settings.py:56`
- **Nedir:** DB'de Fernet ile sifrelenen smtp_password / sms_api_key / sms_account_sid / telegram_bot_token alanlari GET yanitinda decrypt edilip duz metin olarak API cevabina konuyor; at-rest sifreleme bu uctan tamamen etkisiz kaliyor.
- **Düzeltme:** Read semasinda sirlari maskeleyin (`smtp_password: ""` veya `has_smtp_password: bool`), PUT tarafinda bos/sentinel deger gelirse mevcut sifreli degeri KORUYUN (write-only alan deseni). Boylece UI yine '***' gosterir ama duz metin hicbir zaman yanitta yer almaz.

### B8. [ORTA] SECRETS_MASTER_KEY Docker'da hic okunamiyor; SECRET_KEY rotasyonu DB sirlarini sessizce siliyor

- [ ] **Yer:** `apps/backend-api/app/core/config.py:250`
- **Nedir:** config.py SECRETS_MASTER_KEY'i "production'da explicit set edilmesi onerilir" diye belgeliyor, ama degisken ne .env.example'da ne de docker-compose.yml backend-api environment blogunda var; env_file olmadigi icin container'a asla gecmiyor, yani ayar olu. Sonucta vault anahtari kalici olarak SECRET_KEY'den turetiliyor ve SECRET_KEY degisince decrypt_secret hata vermek yerine bos string donduruyor.
- **Düzeltme:** docker-compose.yml backend-api environment blogunda `SECRETS_MASTER_KEY: ${SECRETS_MASTER_KEY:-}` satirini ekleyin ve .env.example'a Fernet.generate_key() ornegiyle koyun; install.sh'ta diger sirlar gibi bir kez uretip yazin. Ayrica secrets_vault.decrypt_secret InvalidToken'da "" yerine istisna firlatsin (ya da en az ERROR loglayip cagiran tarafin yazmasini engelleyecek bir sentinel donsun) ki sessiz veri kaybi olmasin.

### B9. [ORTA] alarm-service NATS worker parolasini her acilista ve her reconnect'te log'a basiyor

- [ ] **Yer:** `apps/alarm-service/alarm_service/main.py:726`
- **Nedir:** NATS_URL degeri `nats://worker:<NATS_WORKER_PASSWORD>@nats:4222` bicimindedir ve dogrudan stdout'a yaziliyor; container json-file log'larinda cleartext NATS kimlik bilgisi birikiyor.
- **Düzeltme:** URL'yi loglamadan once kimlik bilgisini maskeleyin: `urlsplit(NATS_URL)` ile netloc'un `user:pass@` kismini `***@` yapan kucuk bir `_safe_url()` yardimcisi yazip her iki print'te onu kullanin (tag-engine / iec104-outbound / notification-worker'da ayni deseni tekrarlamamak icin de kontrol edin).

### B10. [ORTA] install.sh yeniden calistirilinca canli kurulumda rabbitmq/nats volume'lari SILINEBILIYOR

- [ ] **Yer:** `install.sh:554`
- **Nedir:** install.sh, update.sh'in bilerek kapattigi otomatik-onarim veri silme asamasini kapatmadan e1_repair_service cagiriyor; mevcut bir kurulumda tekrar calistirildiginda rabbitmq-data ve nats-data volume'lari silinebiliyor.
- **Düzeltme:** install.sh'ta altyapi bekleme blogundan once update.sh ile ayni korumayi koy: mevcut kurulum tespit edilmisse (ornegin `docker volume ls` ile postgres-data varsa ya da .env zaten varsa) `E1_WIPEABLE_SERVICES=""` ata. Veri silme asamasi sadece gercekten ilk kurulumda (bos volume) acik kalmali.

### B11. [ORTA] ftp-server container'i root + tam capability ile calisiyor, 0.0.0.0'a acik tek sertlestirilmemis servis

- [ ] **Yer:** `docker-compose.yml:590`
- **Nedir:** ftp-server servisinde ne `user`, ne `cap_drop`, ne `no-new-privileges` var ve imajda USER direktifi yok; container root ve varsayilan tum Linux capability'leriyle 21 + 30000-30009 portlarini tum arayuzlere aciyor.
- **Düzeltme:** modbus-outbound kalibini uygula: `sysctls: [net.ipv4.ip_unprivileged_port_start=0]` ekle, Dockerfile'a non-root USER (uid 10001) koy, FTP_ROOT volume'unu o uid'ye chown et ve compose'a `security_opt: [no-new-privileges:true]` + `cap_drop: [ALL]` ekle (read_only yerine yalnizca writable volume kalsin).

### B12. [ORTA] Yedekten geri yukleme TimescaleDB icin gerekli pre/post_restore adimlarini hic cagirmiyor

- [ ] **Yer:** `apps/backend-api/app/services/backup_service.py:585`
- **Nedir:** Uretimde Postgres imaji `timescale/timescaledb` ve telemetry_history bir hypertable; buna ragmen restore duz `pg_restore --clean --if-exists` ile yapiliyor, `SELECT timescaledb_pre_restore()` / `timescaledb_post_restore()` cagrilari kod tabaninda hic yok.
- **Düzeltme:** run_pg_restore'da hedef DB'de timescaledb kuruluysa restore ONCESI `SELECT timescaledb_pre_restore();`, SONRASI (basari/basarisizlik fark etmeksizin, finally icinde) `SELECT timescaledb_post_restore();` calistir (mevcut _run_psql_on_postgres_db benzeri bir yardimciyla hedef DB uzerinde). Ayrica --jobs=4 ile pre_restore modunun uyumunu dogrula; TimescaleDB restore'da tek is parcacigi onerilir.

### B13. [ORTA] Uygulanmakta olan istegin ustune yazilan 'geri al' istegi okunmadan siliniyor — kapi acik kaliyor

- [ ] **Yer:** `infra/appliance/e1-rad.py:706`
- **Nedir:** cmd_apply, isledigi istegi basta bir kez okuyup sonunda request.json'i KOSULSUZ siliyor; backend ise revoke'u bilerek bekleyen istegin uzerine yaziyor, dolayisiyla apply suren bir sirada gelen revoke hic okunmadan silinir ve systemd path unit'i (dosya artik yok) yeniden tetiklenmez.
- **Düzeltme:** _finish icinde silmeden once dosyayi yeniden oku ve yalnizca `id` isledigin istekle ayniysa sil: `cur = _read_json(REQUEST_PATH); if cur is None or str(cur.get('id') or '') == request_id: _remove(REQUEST_PATH)`. Farkliysa dosyayi birak ve cikista `systemctl start --no-block e1-rad.service` ile (veya cmd_apply'i dongude tekrarlayarak) yeni istegi isle.

### B14. [ORTA] WebSocket temizligi CONNECTING soketi kapatmiyor; sekme gezinmesinde sahipsiz soketler birikiyor

- [ ] **Yer:** `apps/frontend-web/src/shared/useLiveValuesSocket.ts:345`
- **Nedir:** Effect cleanup yalnizca readyState===OPEN olan soketi kapatiyor ve async connect() `await fetchWsTicket` sonrasi iptal kontrolu yapmadan yeni WebSocket aciyor; sonucta effect kapandiktan sonra da yasayan, hicbir zaman kapatilmayan soketler kaliyor.
- **Düzeltme:** Effect basina yerel bir `cancelled` bayragi tut; `await fetchWsTicket` sonrasi `if (cancelled) return;` kontrolu ekle. Cleanup'ta readyState kontrolunu kaldirip kosulsuz `ws.close()` cagir (CONNECTING soketi de kapanir) ve handler'lari (`ws.onopen/onmessage/onclose/onerror = null`) sok. explicitlyClosedRef yerine effect-scoped bayrak kullan ki eski closure'lar yeni kosuda diriltilmesin.

### B15. [ORTA] Uzaktan erisim denetim izi sessizce bosalabilir: sunucu tarafinda 1000 kayit tavani var, istemci filtreleme yapiyor

- [ ] **Yer:** `apps/frontend-web/src/shared/api.ts:2470`
- **Nedir:** fetchRemoteAccessAudit `limit` parametresini istege HIC koymuyor; tum `security` kategorisini cekip istemcide `remote_access_` ile filtreliyor, oysa backend en yeni 1000 kayitla siniri kesiyor.
- **Düzeltme:** Backend'e olay tipi filtresi + limit ekle (orn. `/events?category=security&event_type_prefix=remote_access_&limit=12`) veya `list_system_events`'e event_type parametresi gecir; istemci tarafi filtrelemeye guvenme. En azindan cagriya `&limit=` gecirilebilir hale getir.

### B16. [ORTA] Yedek diski %95 dolu iken 'backup atla' korumasi etkisiz — istisna cagiran tarafta yutuluyor

- [ ] **Yer:** `apps/backend-api/app/services/backup_scheduler.py:87`
- **Nedir:** _check_backup_disk_usage %95 esiginde RuntimeError firlatarak yedegi atlamayi amacliyor ama cagiran blok bu istisnayi genel `except Exception` ile yakalayip logluyor ve hemen ardindan create_backup'i yine de calistiriyor.
- **Düzeltme:** Disk kontrolunu istisnaya degil donus degerine bagla (or. `if not _check_backup_disk_usage(db): return`), ya da cagirandaki except blogunu sadece OSError icin daralt ve RuntimeError'i propagate ederek `_maybe_run`'dan cikacak sekilde ayir. Ek olarak `create_backup` oncesi tahmini dump boyutu + kalan bos alan karsilastirmasi yapilmali.

### B17. [ORTA] Ag ayari endpoint'lerinde record_event yazilir ama commit edilmez — denetim kaydi sessizce kayboluyor

- [ ] **Yer:** `apps/backend-api/app/api/network.py:104`
- **Nedir:** update_network_config, connect_wifi ve forget_wifi record_event cagiriyor fakat hicbiri db.commit() yapmiyor; get_db bagimliligi da commit etmedigi icin SystemEvent satirlari session kapaninca rollback ile yok oluyor.
- **Düzeltme:** Her uc endpoint'te record_event'ten sonra db.commit() ekle (remote_access.py:179 ile ayni desen). Regresyonu onlemek icin get_db'ye commit sorumlulugu vermek yerine, record_event cagrisi iceren endpoint'leri kapsayan bir test eklemek daha guvenli.

### B18. [ORTA] Alarm bildirimi gonderimi basarisiz olunca hicbir yere kayit dusmuyor — sessiz sessizlik

- [ ] **Yer:** `apps/backend-api/app/services/notification_dispatch_service.py:265`
- **Nedir:** E-posta/SMS/Telegram/WhatsApp gonderim hatalari yalnizca logger.warning ile gecistiriliyor; system_events'e olay yazilmiyor, sayac tutulmuyor, UI'da hicbir gosterge cikmiyor — alarm kimseye ulasmasa bile sistem saglikli gorunuyor.
- **Düzeltme:** Kanal basina hata sayacini biriktirip dispatch sonunda tek bir record_event(category="notification", event_type="alarm_notification_failed", severity="error", metadata={kanal, alarm_id, hata_tipi, basarisiz_alici_sayisi}) yaz + db.commit(). Ust uste N basarisizlikta 'bildirim kanali arizali' seklinde kalici bir sistem uyarisi (dashboard rozeti) uret; boylece 'alarm ulasmadi' durumu gozlemlenebilir olur.

### B19. [ORTA] notification-worker'in dead-letter cikisina BAGLI KUYRUK YOK — zehirli alarm bildirimleri sessizce yok ediliyor

- [ ] **Yer:** `apps/notification-worker/notification_service/main.py:189`
- **Nedir:** Worker DLX exchange'ini declare ediyor ama ona bagli hicbir kuyruk yok; RabbitMQ topic exchange'inde eslesen binding olmayinca mesaj DUSURULUR, yani 4xx alan her alarm bildirimi (401 dahil) kalici olarak kayboluyor.
- **Düzeltme:** event_bus.py:194-201'deki deseni worker'a kopyala: `dlq = f"{QUEUE_NAME}.dlq"; channel.queue_declare(queue=dlq, durable=True); channel.queue_bind(exchange=DLX_EXCHANGE, queue=dlq, routing_key="alarm.created.dead")`. Ayrica 401/403'u poison sayma — bunlar mesajin degil KONFIGURASYONUN hatasi; retryable'a al ve DLQ derinligini /health ile sistem durumuna tasi.

### B20. [DUSUK] Disk %95 dolu koruması try/except ile etkisizleştirilmiş — yedek yine de alınıyor

- [ ] **Yer:** `apps/backend-api/app/services/backup_scheduler.py:87`
- **Nedir:** _check_backup_disk_usage() %95 eşiğinde RuntimeError fırlatarak yedeği durdurmak üzere tasarlanmış, ancak çağıran taraf bu exception'ı yutuyor ve hemen ardından create_backup() çalışıyor.
- **Düzeltme:** _check_backup_disk_usage(db) çağrısını try/except'ten çıkarın veya RuntimeError'ı ayrı yakalayıp `return` edin: `except RuntimeError: logger.error(...); return` + `except Exception: logger.exception(...)` (yalnızca beklenmedik introspection hatası akışı sürdürsün).

### B21. [DUSUK] pg_restore RCE korumasi (POSTGRES_RESTORE_USER) Docker'da hic devreye girmiyor

- [ ] **Yer:** `docker-compose.yml:322`
- **Nedir:** backup_service restore'u non-superuser rolle kosturmak icin POSTGRES_RESTORE_USER/POSTGRES_RESTORE_PASSWORD env'lerini okuyor, ama docker-compose.yml'de `env_file` YOK ve bu iki degisken backend-api `environment:` blogunda listelenmemis; dolayisiyla uretimde her zaman POSTGRES_USER (superuser) ile pg_restore kosuyor.
- **Düzeltme:** docker-compose.yml backend-api `environment:` blogunun sonuna ekleyin: `POSTGRES_RESTORE_USER: ${POSTGRES_RESTORE_USER:-}` ve `POSTGRES_RESTORE_PASSWORD: ${POSTGRES_RESTORE_PASSWORD:-}`. Ayrica backup_service.restore icinde, POSTGRES_RESTORE_USER bos ise en azindan WARN log atin ("restore superuser ile kosuyor") ki sessiz kalmasin.

### B22. [DUSUK] Hypertable'a cevrilememis telemetry_history icin hicbir retention yolu yok

- [ ] **Yer:** `apps/backend-api/app/services/telemetry_retention.py:174`
- **Nedir:** Retention worker yalnizca telemetry, processed_messages ve system_events tablolarini temizliyor; telemetry_history'nin budanmasi tamamen TimescaleDB politikasina bagli, dolayisiyla 0019'un onaramadigi kurulumlarda tablo sinirsiz buyur ve sadece UI uyarisi cikar.
- **Düzeltme:** Retention worker'a dorduncu bir is ekle: `telemetry_history` hypertable DEGILSE (historian_service ile ayni introspection) `source_timestamp < now() - 90 gun` icin mevcut `_batch_delete` mantigina benzer LIMIT'li turlarla sil. Hypertable ise hicbir sey yapma (native policy zaten calisiyor).

### B23. [DUSUK] Prefs dogrulanamadiginda ajan her 30 saniyede bir tuneli indirip kaldiriyor ve izin verme hep 'verify_failed' donuyor

- [ ] **Yer:** `infra/appliance/e1-rad.py:505`
- **Nedir:** _at_target, `probe['verified']` false ise HER ZAMAN False donuyor; bu durumda cmd_report her turda _apply_access cagiriyor ve kapali dalda kosulsuz `tailscale down` + `up` kosuyor — yani `tailscale debug prefs` ShieldsUp vermedigi surece tunel 30 sn'de bir kopariliyor.
- **Düzeltme:** (1) _apply_access kapali dalinda down/up'i sartlandirin: yalnizca gercekten acik->kapali GECISINDE (onceki turda desired_open True idiyse veya olculen durum acik ise) tuneli dusurun; zaten kapali/dogrulanamayan durumda tekrar tekrar down/up kosmayin. (2) verified=False halinde ShieldsUp'i `tailscale status --json`/`tailscale set` cikti kodundan turetecek bir yedek yol ekleyin ya da en azindan cmd_apply'i `mismatch=='prefs_unreadable'` durumunda basarili sayip UI'da uyari gosterin. (3) RemoteAccessPage'de `access.mismatch` icin gorunur bir uyari seridi ekleyin.

### B24. [DUSUK] Denetim kaydinin created_at'i host ajaninin saatinden geliyor; saklama penceresi ve siralama bu kolona bagli

- [ ] **Yer:** `apps/backend-api/app/services/event_service.py:47`
- **Nedir:** record_event artik `created_at`'i disaridan (ajanin ended_at/at alanindan) alabiliyor; ayni kolon hem 730 gunluk saklama silmesinin hem de /events siralamasinin anahtari oldugu icin bozuk saatli bir cihazda denetim satiri gorunmez oluyor ve saklama pasi sonrasi tekrar tekrar yaziliyor.
- **Düzeltme:** created_at'i her zaman ekleme ani (now) olarak birak; ajanin bildirdigi gercek zamani ayri bir alanda tasi — ya metadata icine `occurred_at` olarak koy ya da SystemEvent'e `occurred_at` kolonu ekle (migration ile) ve UI'da onu goster. Boylece saklama/siralama monoton kalir, denetim zaman cizgisi de dogru olur.

### B25. [DUSUK] Izin verme/geri alma hatalarinda musteriye ham makine kodu gosteriliyor

- [ ] **Yer:** `apps/backend-api/app/api/remote_access.py:91`
- **Nedir:** Backend hata govdesini `{"code": ..., "message": <ayni kod>}` olarak donduruyor ve 'metin frontend'de i18n ile uretilir' diyor; ancak RemoteAccessPage hatayi oldugu gibi basiyor ve tr/en kaynaklarinda bu kodlar icin hic ceviri yok.
- **Düzeltme:** RemoteAccessPage'de hata kodunu i18n'e cevir (or. `t('remoteAccess.errors.code.' + code, { defaultValue: t('remoteAccess.errors.grant') })`) ve tr/en kaynaklarina `errors.code.request_pending`, `.tailscale_key_expired`, `.duration_exceeds_site_limit`, `.duration_below_minimum`, `.tailscale_not_registered`, `.daemon_not_running`, `.tailscale_not_installed`, `.state_dir_missing`, `.state_dir_not_writable`, `.agent_never_reported`, `.write_failed` girdilerini ekle. api.ts'te de `detail.code`'u Error'a tasi (mesaj yerine kod uzerinden eslestir).

### B26. [DUSUK] "Acik unutma" rozeti gecici bir durumda kalici olarak susturuluyor ve rol degistiginde geri gelmiyor

- [ ] **Yer:** `apps/frontend-web/src/features/remote-access/remoteAccessShared.ts:119`
- **Nedir:** stoppedRef bir kez true olunca hic sifirlanmiyor; App hicbir zaman unmount olmadigi icin (login ekrani ayni bilesenden erken return ediliyor) bu susturma logout/login sonrasi da surer ve ozelligin ana guvenlik uyarisi calismaz.
- **Düzeltme:** stoppedRef'i token/rol degisiminde sifirla (`useEffect(() => { stoppedRef.current = false; }, [token, role])`). available=false icin kalici susturma yerine geri cekilmeli (backoff) bir periyot kullan (orn. 5 dk'da bir yeniden dene); kalici susturmayi yalnizca 403 icin ve yine token degisiminde sifirlanacak sekilde birak.

---

## C. Doğrulanamadı — ayrıca bakılmalı (6)

Bu iddialar doğrulama aşamasına giremedi. Doğru da olabilir, yanlış da; **kabul edilmiş bulgu değildir.**

- [ ] [DUSUK] `apps/backend-api/app/api/events.py:24` — Olay kayitlarinin metadata'si (uzaktan bakim IP'si, tailnet hostname'i dahil) operator'a aciliyor
- [ ] [ORTA] `infra/appliance/e1-rad.py:728` — e1-rad'in iki systemd birimi kilitsiz olarak ayni lease/tailscale durumunu degistiriyor
- [ ] [ORTA] `apps/backend-api/app/services/backup_service.py:305` — Yedek artik telemetri ozetlerini de disarida birakiyor — "silinen telemetri icin ozet kalmali" kurali kurtarma sonrasi bozuluyor
- [ ] [DUSUK] `apps/frontend-web/src/features/remote-access/remoteAccessShared.ts:121` — 'Acik unutma' rozeti gecici bir kullanilamazlikta oturum boyunca kalici olarak susuyor
- [ ] [ORTA] `infra/appliance/e1-rad.py:758` — e1-rad: dogrulama basarisiz olunca lease silinmiyor — 'basarisiz' raporlanan istek 30 sn sonra kapiyi acabiliyor
- [ ] [ORTA] `apps/backend-api/app/api/health.py:51` — Kimlik dogrulamasiz /health, ham DB istisna metnini disari veriyor

---

## D. Çürütülen iddialar (3) — tekrar açmayın

- ~~"Diskin %10'u her zaman bos kalsin" kurali kodda hicbir yerde uygulanmiyor~~
- ~~Uzaktan bakim rozeti acikken tum App agaci saniyede bir yeniden render ediliyor~~
- ~~Ajan olurse UI kilitleniyor: pending sonsuza kadar formu kapatiyor, kapatma butonu ise gorunmuyor~~

İlki önemli: **disk %10 boş kalma kuralı `disk_guard` içinde gerçekten uygulanıyor.**

