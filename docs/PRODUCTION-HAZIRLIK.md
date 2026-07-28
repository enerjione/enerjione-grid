# Production Hazırlık Raporu

**Tarih:** 2026-07-28 · **Kapsam:** `enerjione/enerjione-grid` @ `main` (bc62770)
**Hedef:** *minimum* production-ready — sahaya güvenle çıkabilecek eşik

---

## Özet

Sistem beklediğimden iyi durumda. Uygulama katmanı (auth, container sertleştirme,
rate limit, üretim guard'ları) büyük ölçüde hazır; **asıl açıklar süreç ve
operasyon tarafında** — sürüm senkronu, sızmış anahtarlar, yedek doğrulaması.

| Alan | Durum |
|---|---|
| Uygulama güvenliği | 🟢 İyi — denetim maddelerinin çoğu uygulanmış |
| CI/CD | 🟢 7 kapı yeşil, tag → imaj → deploy çalışıyor |
| Sürüm yönetimi | 🟡 `main` yayından 4 commit önde |
| Sır yönetimi | 🔴 Geçmişte canlı anahtar var, iptal edilmedi |
| Yedekleme | 🟡 Zamanlayıcı var, **geri yükleme hiç denenmedi** |
| Depo hijyeni | 🟡 44 ölü worktree, 46 dal |
| Dokümantasyon | 🔴 Güvenlik yol haritası gerçeği yansıtmıyor |

**Minimum eşik için 5 iş yeterli** (aşağıda A grubu, ~3 saat).

---

## A. Sahaya çıkmadan ÖNCE — zorunlu

### A1 🔴 Sızmış anahtarları iptal et

`tools/installer-gui/tokenler.txt` `8757a04` commit'inde depoya girdi; içinde
**canlı GitHub token'ı ve canlı Tailscale OAuth secret'ı** var. Dosya sonradan
kaldırıldı ama **geçmişte duruyor** — depo private olduğu için risk sınırlı,
yine de bu iki anahtar artık "yayınlanmış" sayılmalı.

```
GitHub    : Settings → Developer settings → Tokens → Revoke
Tailscale : console.tailscale.com → Settings → Keys → Revoke
```

Sonra yenilerini üretip kurulum aracına girin. **Süre: 10 dk.**

> Geçmişi temizlemek (`git filter-repo`) da mümkün ama private depoda
> rotasyon yeterli ve çok daha az riskli.

### A2 🔴 Yedekten geri yükleme provası yap

`backup_scheduler` çalışıyor ve `update.sh` her güncellemede otomatik yedek
alıyor. Ama **hiç geri yüklenmedi.** Denenmemiş yedek, yedek değildir.

```bash
# VM'de: yedek al, veriyi boz, geri yükle, doğrula
sudo enerjione-grid backup
sudo docker compose exec -T postgres psql -U enerjione_grid -d enerjione_grid \
  -c "SELECT count(*) FROM devices;"
# ... restore akışını çalıştır, sayı tutuyor mu bak
```

**Süre: 1 saat.** Bu maddeyi atlarsanız ilk veri kaybında geri dönüş yok.

### A3 🟡 `main` ile yayın arasındaki farkı kapat

Yayınlanan `v2.24.5`, `main`'den **4 commit geride**. İçlerinde sahayı
doğrudan etkileyen bir düzeltme var:

```
de5ee0d  fix(db): ilk kurulumda Postgres henuz hazir degilken baglaniyorduk
f775123  feat(tools): sonradan kurulabilen katmanlar icin butonlar
c0ab246  feat(tools): tam ekran acilis, sabit eylem seridi
bc62770  feat(tools): kaydirma kaldirildi
```

`de5ee0d` olmadan **temiz kurulumlar Postgres yarışına takılabilir** — zaten
bir kez yaşandı. Test bitince `v2.24.6` çıkarın. **Süre: 20 dk.**

### A4 🟡 Yayın öncesi ilk şifre değişimini doğrula

`installer / ChangeMe123!` ile ilk giriş yapılıyor; `must_change_password`
alanı modelde var ve giriş ekranında zorlanıyor. **Sahada bir kez uçtan uca
deneyin** — bu akış bozulursa varsayılan şifreli bir sistem sahada kalır.

`apps/backend-api/scripts/seed_engineer.py` de aynı şifreyi gömüyor; bu script
production'da çalıştırılmamalı. **Süre: 15 dk.**

### A5 🟡 VDS otomatik deploy'u ya bağla ya kapat

`VDS_HOST` / `VDS_SSH_KEY` secret'ları tanımlı değil; release'te deploy adımı
**hata vermeden atlanıyor**. Bu bilinçli bir tasarım ama şu an "deploy oldu"
sanma riski var. Ya secret'ları ekleyin ([docs/CI-CD.md](CI-CD.md) §Gerekli
GitHub ayarları) ya da adımı kaldırın. **Süre: 30 dk.**

---

## B. İlk hafta içinde

### B1 🔴 Güvenlik yol haritasını gerçekle hizala

[docs/security-roadmap.md](security-roadmap.md) 2026-05-08 tarihli ve **19
maddenin tamamı "⏳ Yapılacak"** görünüyor. Oysa örneklem denetiminde
baktığım yedi maddenin **yedisi de uygulanmış**:

| Madde | Tabloda | Gerçekte |
|---|---|---|
| 1.1 Default secret prod reddi | ⏳ | ✅ `_validate_production_safeguards` |
| 1.2 CORS wildcard guard | ⏳ | ✅ config.py |
| 1.4 /login rate limit | ⏳ | ✅ slowapi |
| 2.1 IEC104 peer whitelist | ⏳ | ✅ |
| 2.5 Şifre değiştirme zorunluluğu | ⏳ | ✅ model alanı |
| 2.6 Sabit-zaman token compare | ⏳ | ✅ `compare_digest` |
| 3.8 Container hardening | ⏳ | ✅ compose |

Yanlış yön gösteren bir güvenlik dokümanı, hiç doküman olmamasından **daha
tehlikeli**: ya yapılmış iş tekrar yapılır ya da gerçekten açık olan madde
kalabalıkta kaybolur. Tabloyu bir oturuşta gözden geçirip tarih + commit ile
işaretleyin.

### B2 🟡 Depo hijyeni

```
44 ölü worktree  (git worktree list → prunable)
46 dal           (çoğu worktree-agent-*)
5 commit edilmemiş dosya (frontend + installer)
```

```bash
git worktree prune
git branch --list 'worktree-agent-*' | xargs -r git branch -D
```

Ölü referanslar `git branch`/`git worktree` çıktısını okunmaz yapıyor ve
yanlışlıkla eski bir dalda çalışma riski doğuruyor. **Süre: 10 dk.**

### B3 🟡 Yeni sürüm bildirimini aç

`UPDATE_CHECK_URL` boş → arayüzdeki "yeni sürüm var" uyarısı **hiç
çalışmıyor**. Depo private olduğu için GitHub Releases API kimliksiz
kullanılamıyor; ya cihaza okuma anahtarı verilir ya da public bir manifest
yayınlanır (`infra/scripts/linux/publish-installer.sh` bunu üretiyor).

### B4 🟡 Off-site yedek

`BACKUP_OFFSITE_DIR` boş → yedekler **sadece cihazın kendi diskinde**. Disk
arızası veya fidye yazılımı ikisini birden götürür. NAS veya ikinci disk
tanımlayın.

---

## C. Bilinen ve kabul edilmiş sınırlar

Bunlar açık değil, **bilinçli kararlar** — production'ı bloklamaz:

- **Branch protection yok.** GitHub Free + private depo kombinasyonu izin
  vermiyor (`403: Upgrade to GitHub Pro`). CI çalışıyor ama *zorlayıcı* değil.
  Pro (~4 $/ay) alınırsa 10 dakikada kurulur.
- **Token blacklist bellekte** (`auth.py:328`). Çok replikalı deploy'da Redis
  gerekir. Şu an tek düğüm — sorun değil, ama yatay ölçeklemeden önce şart.
- **Kaynak kod imajın içinde okunabilir.** `.deb` depoyu gizler ama Python
  kodu imajdan çıkarılabilir. Değerlendirme: [docs/PAKET.md](PAKET.md) sonu.
- **Kurulum anahtarı depo okuma yetkisi veriyor.** Salt-okunur ama sahadaki
  her cihazda duruyor; sızarsa kaynak koda erişim demek.

---

## D. İyi durumda olanlar — bozmayın

Denetimde doğruladığım, korunması gereken pozitifler:

- **Üretim guard'ları**: placeholder secret veya `CORS=*` ile production'da
  boot **reddediliyor** (`_validate_production_safeguards`).
- **Container sertleştirme**: `no-new-privileges`, `cap_drop: ALL`,
  `read_only`, tmpfs — worker'ların hepsinde.
- **Ağ yüzeyi dar**: backend 8000 host'a açılmıyor; RabbitMQ yönetim arayüzü
  ve NATS monitoring yalnızca localhost.
- **CI 7 kapı**: frontend build · backend ruff+pytest · alembic tek-head ·
  shellcheck · compose · sürüm tutarlılığı · Debian paketi (temiz konteynerde
  gerçekten kurularak).
- **Sürüm tutarlılığı zorunlu**: tag ≠ VERSION ≠ package.json ise yayın
  reddediliyor.
- **`apt purge` bile veri silmiyor** — volume'lar ve `.env` yerinde kalıyor.
- **Otomatik onarım**: sağlıksız RabbitMQ/NATS, Postgres parola şifreleme
  uyumsuzluğu ve Postgres açılış yarışı kendiliğinden çözülüyor.

---

## Plan

### Bugün (~3 saat) → minimum eşik
1. **A1** Anahtarları iptal et, yenilerini üret (10 dk)
2. **A3** Testler bitince `v2.24.6` çıkar (20 dk)
3. **A4** İlk şifre değişimini uçtan uca dene (15 dk)
4. **A5** VDS secret'larını ekle veya adımı kaldır (30 dk)
5. **A2** Yedekten geri yükleme provası (1 saat)

> Bu beşi bitince **sahaya çıkabilirsiniz.**

### Bu hafta
6. **B1** Güvenlik yol haritasını gerçekle hizala (1 saat)
7. **B2** Worktree/dal temizliği (10 dk)
8. **B4** Off-site yedek dizini tanımla (30 dk)
9. **B3** Sürüm bildirimini aç (1 saat)

### Sonraki tur
- GitHub Pro → branch protection
- İkinci saha kurulumundan önce: kurulum anahtarının kapsamını daralt
- Yatay ölçekleme gündeme gelirse: Redis tabanlı token blacklist

---

## Ölçüt

Aşağıdakilerin hepsi "evet" ise minimum production-ready sayılır:

- [ ] Sızmış anahtarlar iptal edildi, yenileri dağıtıldı
- [ ] Bir yedek gerçekten geri yüklendi ve veri doğrulandı
- [ ] Sahada çalışan sürüm = yayınlanmış tag (drift yok)
- [ ] `installer` varsayılan şifresi ilk girişte değiştirildi
- [ ] Deploy yolu belirli: otomatik çalışıyor **veya** bilerek kapalı
- [ ] Cihaz yeniden başlatıldığında sistem kendiliğinden ayağa kalkıyor
