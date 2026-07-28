# Yeni GitHub Organizasyonuna Taşınma

Hedef: `github.com/fikretsafak/EnerjiOneGrid` → **`github.com/enerjione/enerjione-grid`** (private)

Geçmiş korunur; mevcut VDS ve saha kurulumları güncelleme almaya devam eder.
Sırayla uygulayın, her adımın sonunda bir doğrulama var.

---

## 1. Yeni depoyu oluştur

`github.com/enerjione` → **New repository**

| Alan | Değer |
|---|---|
| Repository name | `enerjione-grid` |
| Visibility | **Private** |
| Initialize with README | **Hayır** (boş olmalı) |
| Add .gitignore / license | **Hayır** |

> Boş oluşturmak önemli: README ile başlatırsanız geçmiş push'u çakışır.

---

## 2. Geçmişi taşı

Yerelde, mevcut çalışma kopyanızda:

```bash
cd "/c/Users/fikret.safak/Documents/SoftwareFormElektrik/EnerjiOne Grid App/EnerjiOne Grid"

# Eski adresi ikinci bir remote olarak sakla (geri dönüş gerekirse dursun)
git remote rename origin eski-origin

# Yeni depoyu origin yap
git remote add origin https://github.com/enerjione/enerjione-grid.git

# Çalışılan dalı main olarak yayınla — yeni depoda "docker-linux-deploy"
# diye bir dal OLMAYACAK; tek gövde main.
git push origin docker-linux-deploy:main

# Yerel dalı da main'e çevir
git branch -m docker-linux-deploy main
git branch --set-upstream-to=origin/main main
```

**Doğrulama:** GitHub'da yeni depoda commit geçmişi görünmeli ve `main` tek
dal olmalı.

---

## 3. İlk sürüm etiketini at

Saha cihazları tag takip ettiği için **en az bir tag olmalı**; yoksa
kurulum `main` dalına düşer ve uyarı verir.

```bash
git tag v2.24.4
git push origin v2.24.4
```

Bu tag release workflow'unu tetikler: 9 imaj derlenip GHCR'a basılır.
**Actions** sekmesinden takip edin (ilk çalıştırma cache olmadığı için
en uzunudur, ~15–25 dk).

**Doğrulama:** Depo sayfasında sağda **Packages** altında 9 paket görünmeli.

---

## 4. Depo ayarları

### Secrets ve Variables
Settings → Secrets and variables → Actions:

| Tür | Ad | Değer |
|---|---|---|
| Secret | `VDS_HOST` | VDS IP'si |
| Secret | `VDS_USER` | SSH kullanıcısı |
| Secret | `VDS_SSH_KEY` | Deploy anahtarının özel kısmı |
| Secret | `VDS_HOST_KEY` | `ssh-keyscan -H <VDS_IP>` çıktısı |
| Variable | `VDS_PATH` | `/opt/enerjione-grid` |

Deploy anahtarı üretimi (yerelde):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/e1-deploy -N "" -C "github-actions-deploy"
# Açık kısmı VDS'e yetkilendir:
ssh-copy-id -i ~/.ssh/e1-deploy.pub <kullanici>@<VDS_IP>
# Özel kısmı VDS_SSH_KEY secret'ına yapıştır:
cat ~/.ssh/e1-deploy
```

VDS'te bu kullanıcı `update.sh`'i parolasız sudo ile çalıştırabilmeli:

```bash
echo '<kullanici> ALL=(ALL) NOPASSWD: /bin/bash /opt/enerjione-grid/update.sh' \
  | sudo tee /etc/sudoers.d/e1-deploy
sudo chmod 440 /etc/sudoers.d/e1-deploy
```

### Environment
Settings → Environments → **New environment** → `production`.
İsteğe bağlı: "Required reviewers" ekleyin — deploy onay ister.

### Branch protection
Settings → Rules → **New branch ruleset**, hedef `main`:

- Require a pull request before merging
- Require status checks to pass (CI işlerinin altısını da seçin)
- Require review from Code Owners
- Block force pushes

### Actions izinleri
Settings → Actions → General → Workflow permissions:
**Read and write permissions** (release, GHCR'a paket basar ve Release oluşturur).

---

## 5. Saha kurulumu için token

Settings → Developer settings → Personal access tokens → **Fine-grained tokens**:

- Resource owner: `enerjione`
- Repository access: yalnızca `enerjione-grid`
- Permissions → **Packages: Read-only**
- Süre: 1 yıl (takvime not düşün — dolduğunda saha güncellemeleri durur)

Bu token saha cihazlarına dağıtılır. **Depoya yazma yetkisi olan bir token
asla sahaya verilmez.**

---

## 6. Mevcut kurulumları yeni depoya bağla

### VDS

```bash
cd /opt/enerjione-grid
sudo git remote set-url origin https://github.com/enerjione/enerjione-grid.git
sudo bash update.sh
```

> `update.sh` bunu kendi de yapar: eski adresi tanır ve `origin`'i uyararak
> taşır. Yukarıdaki komut sadece açık olsun diye.

`.env`'e GHCR token'ını ekleyin (yoksa imajlar VDS'te derlenir):

```bash
sudo sh -c 'echo "GHCR_TOKEN=github_pat_xxx" >> /opt/enerjione-grid/.env'
sudo sh -c 'echo "GHCR_USERNAME=x-access-token" >> /opt/enerjione-grid/.env'
```

### Saha mini PC'leri

```bash
cd /opt/enerjione-grid && sudo bash update.sh
```

Adres taşıması otomatik. Token'ı `.env`'e eklemek isterseniz VDS ile aynı.

---

## 7. Kurulum kısayolunu güncelle

Eski kurulum adresi GitHub'a **302 yönlendiriyordu**. Depo private olduğu için
bu artık çalışmaz: `raw.githubusercontent.com` kimliksiz istemciye 404 döner ve
`curl | bash` boş gövdeyi çalıştırmaya kalkar.

Yeni adres scripti **VDS'ten servis eder**:

```
curl -fsSL https://enerjione.com/grid/install.sh | sudo bash
```

VDS'te:

```bash
cd /opt/enerjione-grid
sudo bash infra/host-nginx/setup-host-nginx.sh   # eski get-enerjione'ı kendisi devre dışı bırakır
sudo nginx -t && sudo systemctl reload nginx
```

DNS A kaydı: `enerjione.com` → VDS IP. SSL:

```bash
sudo certbot --nginx -d enerjione.com -d www.enerjione.com -d get.enerjione.com
```

**Doğrulama:**

```bash
curl -fsS https://enerjione.com/grid/install.sh | head -3   # shebang görünmeli
curl -fsS https://enerjione.com/grid/version.json           # {"version":"2.24.4",...}
```

Sürüm manifesti aynı zamanda backend'in güncelleme kontrolünü besler — private
depo olduğu için GitHub Releases API kimliksiz kullanılamıyor. VDS `.env`'ine:

```
UPDATE_CHECK_URL=https://enerjione.com/grid/version.json
```

---

## 8. Eski depoyu kapat

Her şey doğrulandıktan **sonra**:

1. Eski depo → Settings → Archive this repository (silmeyin — geçmiş referans)
2. README'sine tek satır not: *"Taşındı: github.com/enerjione/enerjione-grid"*
3. Yerelde eski remote'u kaldırın: `git remote remove eski-origin`

---

## Kontrol listesi

- [ ] Yeni depo private ve geçmiş taşındı
- [ ] `main` tek dal; `docker-linux-deploy` yok
- [ ] `v2.24.4` tag'i atıldı, Actions yeşil, 9 paket GHCR'da
- [ ] Secrets + environment + branch protection ayarlandı
- [ ] Salt-okunur GHCR token'ı üretildi
- [ ] VDS yeni depodan güncellendi ve ayakta
- [ ] En az bir saha PC'si yeni depodan güncellendi
- [ ] `https://enerjione.com/grid/install.sh` scripti servis ediyor (302 değil, 200)
- [ ] `https://enerjione.com/grid/version.json` sürümü döndürüyor
- [ ] Eski depo arşivlendi
