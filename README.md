<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.png">
    <img src="docs/assets/logo.png" alt="EnerjiOne" width="360">
  </picture>
</p>

<h1 align="center">Grid</h1>

<p align="center">
  Endüstriyel akıllı şebeke izleme platformu.<br>
  Orta gerilim hatlarındaki arıza-geçiş göstergelerini izler, arızayı<br>
  <b>hangi iki direk arasında</b> olduğuna kadar daraltır.
</p>

<p align="center">
  <a href="https://github.com/enerjione/enerjione-grid/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/enerjione/enerjione-grid/actions/workflows/ci.yml/badge.svg"></a>
  <a href="VERSION"><img alt="Sürüm" src="https://img.shields.io/badge/dynamic/regex?url=https%3A%2F%2Fraw.githubusercontent.com%2Fenerjione%2Fenerjione-grid%2Fmain%2FVERSION&search=(.*)&replace=v%241&label=s%C3%BCr%C3%BCm&color=e97800"></a>
  <img alt="Platform" src="https://img.shields.io/badge/platform-Ubuntu%2022.04%20%2F%2024.04%20%C2%B7%20Debian%2012-0f172a">
</p>

---

## Ne yapar

Sahadaki **Horstmann Smart Navigator 2.0** arıza-geçiş göstergeleri, üzerlerinden
geçen arıza akımını görür. EnerjiOne Grid bu cihazları tek merkezden izler ve
arızanın **hattın neresinde** olduğunu haritada gösterir.

| | |
|---|---|
| **Arıza yeri tespiti** | Son "gördüm" diyen cihaz ile ilk "görmedim" diyen cihaz arasındaki hat parçası haritada vurgulanır. Ekip doğrudan oraya gider. |
| **Çoklu arıza bölgesi** | Aynı hatta birbirinden bağımsız birden fazla arıza varsa hepsi ayrı ayrı gösterilir. |
| **Canlı izleme** | Cihaz durumu, batarya, haberleşme ve ölçümler WebSocket ile anlık akar. |
| **Alarm kuralları** | Eşik, değişim hızı (dV/dt) ve AND/OR bileşik mantık. |
| **Bildirim** | E-posta, SMS, Telegram, WhatsApp ve mobil push. |
| **Şebeke modeli** | Bölge → Hat → Direk → Cihaz hiyerarşisi, harita üzerinde düzenlenir. |
| **SCADA çıkışı** | IEC 60870-5-104 ve Modbus TCP ile dış sistemlere yayın. |
| **Çevrimdışı harita** | İnternetin olmadığı sahalar için harita alanı önceden indirilir. |

---

## Kurulum

Saha cihazına kurulum için **Saha Kurulum Aracı**'nı kullanın — Windows'ta
çalışan, tek dosyalık bir program. Cihaza SSH ile bağlanır, kurulumu baştan
sona yürütür.

### **[→ Saha Kurulum Aracı'nı indir](https://github.com/enerjione/enerjione-grid-kurulum-araci/releases/latest)**

Araç şunları tek seferde halleder: kurulum, WiFi/IP ayarı, müşteri adıyla
WiFi ağı, uzaktan bakım VPN'i ve operatör ekranı (kiosk).

Sunucuya elle kurulum yapacaksanız: **[docs/SAHA-KURULUM.md](docs/SAHA-KURULUM.md)**
(adım adım) veya **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** (nginx, SSL,
çoklu uygulama, sorun giderme).

Güncelleme ve geri alma:

```bash
cd /opt/enerjione-grid
sudo bash update.sh                 # en son yayına geç
sudo bash update.sh --version 2.24.6   # belirli bir sürüme dön
```

---

## Mimari

```
Frontend (React + Vite)  ──►  nginx :80
                                 │  /api/v1
                          Backend API (FastAPI)
                                 │
        ┌────────────┬───────────┼────────────┬─────────────┐
    PostgreSQL    RabbitMQ   NATS JetStream   │             │
                                              │             │
                                    ┌─────────┴───┐   ┌─────┴──────┐
                                    │ tag-engine  │   │  alarm     │
                                    │ notification│   │  service   │
                                    │ iec104 /    │   └────────────┘
                                    │ modbus      │
                                    └─────────────┘
```

Olay güdümlü mikroservis mimarisi; Docker Compose + systemd ile çalışır.
Telemetri akışı NATS JetStream üzerinden gider, alarm olayları RabbitMQ ile
dağıtılır.

**Roller**

| Rol | Kapsam |
|---|---|
| `installer` | Süper yönetici — her şey |
| `engineer` | Mühendis — yedek geri yükleme hariç her şey |
| `ops_manager` | Kullanıcı/ekip yönetimi, toplu bildirim |
| `operator` | Saha personeli — alarm ve arıza görüntüleme, yorum |

---

## Saha cihazı

Mini PC olarak kurulduğunda cihaz "açınca çalışan kutu" hâline gelir:

- **Müşteri adıyla WiFi ağı** (`E1GRID-TPAO`) — telefonla bağlanıp arayüze girilir
- **`e1-grid.local`** — kablolu ağdan sabit isimle erişim
- **Operatör ekranı** — açılışta otomatik giriş, tam ekran arayüz, yetkisiz hesap
- **Arayüzden IP/DNS ayarı** — yanlış ayar girilirse cihaz eski ayarına kendiliğinden döner
- **Uzaktan bakım** — port açmadan, müşteri onayıyla ve süreli

Ayrıntı: [docs/APPLIANCE.md](docs/APPLIANCE.md) · [docs/TAILSCALE.md](docs/TAILSCALE.md)

---

## Geliştirme

```bash
# Backend (Python 3.11+)
cd apps/backend-api
python -m venv .venv && .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --reload --port 8000

# Frontend (Node 20+)
cd apps/frontend-web
npm install && npm run dev          # :5173, API'yi :8000'e proxy eder
```

Testler:

```bash
cd apps/backend-api && pytest
cd apps/frontend-web && npx tsc --noEmit
```

Yapılandırma **[`.env.example`](.env.example)** dosyasında açıklanmıştır —
her değişkenin ne işe yaradığı yanında yazar.

---

## Belgeler

| | |
|---|---|
| [SAHA-KURULUM.md](docs/SAHA-KURULUM.md) | Saha kurulumu, adım adım |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Sunucu kurulumu, nginx, SSL, sorun giderme |
| [APPLIANCE.md](docs/APPLIANCE.md) | Mini PC katmanı: WiFi AP, mDNS, ağ ajanı |
| [TAILSCALE.md](docs/TAILSCALE.md) | Uzaktan bakım VPN'i ve erişim izni |
| [MODBUS.md](docs/MODBUS.md) | Modbus TCP adres planı |
| [CI-CD.md](docs/CI-CD.md) | Sürüm çıkarma ve imaj yayınlama |

---

<p align="center">
  <sub><b>EnerjiOne Grid</b> · Form Elektrik</sub>
</p>
