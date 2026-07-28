# Saha Kurulum Aracı (GUI)

Cihazı ethernet ile bilgisayarınıza bağlayın, bilgileri girin, kurulumu
başlatın ve sağdaki terminalden canlı izleyin.

```
┌──────────────────────┬──────────────────────────────────────────┐
│ Cihaz bağlantısı     │ Kurulum çıktısı                          │
│  IP / port / kullanıcı│                                          │
│  parola veya anahtar │  ✓ SSH bağlantısı kuruldu                │
│                      │  ✓ Kurulum dosyası gönderildi            │
│ Kurulum anahtarları  │  [2/6] Repo hazırlanıyor…                │
│  GitHub / Tailscale  │  Kaynak kod indiriliyor [███░░░] 12 sn    │
│                      │  …                                        │
│ Saha kimliği         │                                          │
│  müşteri / saha      │                                          │
│                      │                                          │
│ [Bağlantıyı Test Et] │                                          │
│ [Kurulumu Başlat]    │                                          │
└──────────────────────┴──────────────────────────────────────────┘
```

## Kurulum (bir kez, kendi bilgisayarınıza)

```powershell
py -3.11 -m pip install -r tools\installer-gui\requirements.txt
```

## Çalıştırma

```powershell
py -3.11 tools\installer-gui\e1_installer.py
```

Windows'ta çift tıklamayla açmak için `EnerjiOne-Kurulum.bat` kullanın.

## Kullanım

1. **Cihazı bağlayın.** Ethernet kablosu + cihazın IP'si. Cihazda SSH açık
   olmalı (Ubuntu Server'da varsayılan açıktır).
2. **Bağlantıyı Test Et.** İşletim sistemi, RAM, disk, Docker durumu ve varsa
   kurulu sürüm listelenir. Kurulumdan önce buraya bakın — 4 GB'ın altında
   RAM varsa kurulum sıkışır.
3. **Anahtarları girin.** GitHub anahtarı zorunlu (depo ve imajlar özel).
   Tailscale boş bırakılabilir; o zaman uzaktan bakım VPN'i kurulmaz.
4. **Kurulumu Başlat.** Onay sorulur, sonra süreç sağda akar.

Bittiğinde arayüz adresi ve ilk giriş bilgileri terminale yazılır.

## Ne yapıyor

1. SSH ile bağlanır
2. Anahtarları gömülü tek bir kurulum dosyası üretir ve `/tmp`'ye gönderir
   (chmod 600)
3. `sudo bash` ile çalıştırır; dosya iş bitince cihazdan silinir
4. Kurulum dosyası anahtarları `/etc/enerjione-grid/install.env`'e yazar,
   `install.sh`'i özel depodan çeker ve kurulumu yürütür

## Güvenlik

- **Anahtarlar diske yazılmaz.** Profil kaydı yalnızca IP, kullanıcı, müşteri
  gibi alanları tutar; parola ve token'lar hiçbir zaman saklanmaz.
- **Terminalde maskelenir.** Bir anahtar çıktıda görünürse `ghp_…99` biçimine
  indirilir. Kaydettiğiniz log dosyasında da maskeli kalır.
- **Cihazda kalıcı dosya yok.** Kurulum dosyası çalıştıktan sonra siliniyor;
  yalnızca `/etc/enerjione-grid/install.env` kalıyor (chmod 600) — güncellemeler
  için gerekli.
- Bilinmeyen SSH host key'i otomatik kabul edilir. Doğrudan ethernet ile
  bağlanan bir cihaz için makul; **internet üzerinden kullanacaksanız** host
  key'i sabitlemek gerekir.

## Sorun giderme

| Belirti | Sebep |
|---|---|
| "Cihaza ulaşılamadı" | IP yanlış, kablo takılı değil veya SSH kapalı |
| "Kimlik doğrulama başarısız" | Kullanıcı/parola hatalı ya da anahtar yanlış |
| "sudo: parola gerekli" ve takılma | Parola alanını doldurun; `sudo -S` onu kullanır |
| Kurulum scripti indirilemedi | GitHub anahtarının süresi dolmuş veya `Contents: Read` yetkisi yok |
| Ekranda hareket yok | Uzun adımlar (imaj indirme) sessiz olabilir; 15 saniyede bir süre satırı düşer |

Çıktıyı **Kaydet** ile dosyaya alıp destekle paylaşabilirsiniz — anahtarlar
maskeli olduğu için güvenle gönderilebilir.
