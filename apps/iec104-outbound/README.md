# IEC 104 Outbound Service

Tag engine'in normalize ettigi `telemetry.received` event'lerini RabbitMQ'dan
tuketir; backend-api'dan okudugu `outbound_targets` (protocol=`iec104`) icin
ayri TCP IEC 60870-5-104 slave server'lari acar ve dis SCADA sistemlerine
spontaneous + general interrogation cevaplari yayinlar.

Backend-api'dan **bagimsiz** bir process olarak calisir; backend yalnizca
config kaynagi olarak kullanilir.

## Akis

```
+-----------+   telemetry.received    +-------------------+   IEC104 TCP    +--------+
| tag-engine|------------------------>| iec104-outbound   |---------------->| SCADA  |
+-----------+   (RabbitMQ exchange)   |                   |  (port 2404...) +--------+
                                      |  - consumer thr   |
                                      |  - asyncio loop   |
                                      |  - server/target  |
                                      +---------+---------+
                                                |
                                                | HTTP /internal/{signals,devices,outbound-targets}
                                                v
                                        +---------------+
                                        |  backend-api  |
                                        +---------------+
```

## Paralel Calisma

- Birden fazla `outbound_targets` kaydi varsa her biri **ayri TCP port** uzerinde
  paralel calisir (asyncio loop tek; her server kendi `asyncio.Server` instance'i).
- Servisi yatay olcekleme: ayni RabbitMQ exchange + ayni `IEC104_QUEUE` ile
  birden cok instance baslatilamaz (ayni TCP portu acmaya calisirlar). Daha
  fazla yuk dagitimi gerekirse target listesini env ile bolup farkli queue +
  farkli port havuzlari ile paralel instance calistirin (`IEC104_QUEUE`,
  `WORKER_HEALTH_PORT` benzersiz olmali).

## Calistirma

### 1. Bagimliliklar

```powershell
cd apps/iec104-outbound
py -3.10 -m pip install -r requirements.txt
```

### 2. Ortam

`.env.example` dosyasini `.env` olarak kopyalayin ve guncelleyin:

| Key                          | Amac                                                               |
| ---------------------------- | ------------------------------------------------------------------ |
| `RABBITMQ_URL`               | Tag-engine'in yayinladigi exchange'in oldugu broker                |
| `IEC104_INCOMING_TOPIC`      | Tuketilecek topic — varsayilan `telemetry.received`                |
| `IEC104_QUEUE`               | Bu servisin own queue adi (paralel instance'larda benzersiz)       |
| `IEC104_PREFETCH`            | Tek seferde alinabilen mesaj sayisi (varsayilan 50)                |
| `BACKEND_API_BASE`           | Backend `/api/v1` taban URL'i                                      |
| `INTERNAL_SERVICE_TOKEN`     | Backend `internal_service_token` ile ayni                          |
| `IEC104_CATALOG_REFRESH_SEC` | Outbound target / signals / devices yenileme periyodu (sn)         |
| `WORKER_HEALTH_PORT`         | `/health` portu (varsayilan 8013, paralel instance icin benzersiz) |
| `IEC104_DEFAULT_LISTEN_HOST` | Target'ta `listen_host` NULL ise kullanilan host                   |

### 3. Calistir

```powershell
py -3.10 -m iec104_outbound
```

### 4. Saglik

```powershell
curl http://127.0.0.1:8013/health
```

Yanit ornegi:

```json
{
  "status": "ok",
  "service": "iec104-outbound",
  "version": "0.1.0",
  "started_at": "2026-04-30T12:30:00+00:00",
  "active_servers": 2,
  "deployed_targets": 2,
  "messages_processed": 1453,
  "last_consumer_error": null,
  "config": {
    "backend_api_base": "http://127.0.0.1:8000/api/v1",
    "queue": "hsl.iec104_outbound.telemetry",
    "incoming_topic": "telemetry.received",
    "catalog_refresh_sec": 30,
    "default_listen_host": "0.0.0.0"
  }
}
```

## Outbound Target Tanimlama

Backend `outbound_targets` tablosuna `protocol='iec104'` kaydi acin ve sunlari
doldurun:

| Alan                         | Anlam                                                          |
| ---------------------------- | -------------------------------------------------------------- |
| `name`                       | Operatorun gorecegi takma ad                                   |
| `protocol`                   | `iec104`                                                       |
| `is_active`                  | `true`                                                         |
| `listen_host`                | Bu makinedeki IP / `0.0.0.0` / `::`. NULL ise env default.    |
| `listen_port`                | TCP port (default 2404). Paralel target'larda farkli olmali.   |
| `iec104_common_address`      | ASDU Common Address (genelde 1)                                |
| `iec104_ioa_device_stride`   | Cihaz basina IOA blok buyuklugu (default 10000)                |

Sinyal kataloguna IEC104 adresleme olusturulmasi icin `signal_catalog` satirinda
`iec104_type_id` (1/13/15) ve `iec104_ioa_offset` (cihaz icindeki goreli adres)
dolu olmalidir. NULL olanlar dis dunyaya yayilmaz.

> Mutlak IOA = `device_index * iec104_ioa_device_stride + iec104_ioa_offset`
> (device_index alfabetik `device.code` siralamasinda 0'dan baslar).

## Tasarim Notlari

- `SignalCatalog` ya da `Device` MODELLERINE bagimli degil — backend
  `/internal/{signals,devices,outbound-targets}` endpoint'leri JSON dondurur,
  servis bunu plain dict olarak isler. Boylece backend-api process'i
  bagimsizdir; sadece HTTP+RabbitMQ ile konusurlar.
- `CatalogSyncer` periyodik olarak imza (signature) hesaplar; cihaz/sinyal/target
  degismediyse server'a dokunmaz. Degisiklik varsa **sadece o target** yeniden
  deploy edilir (diger server'lar etkilenmez).
- IEC 104 server'lari `asyncio.start_server` uzerinde calisir; consumer
  thread'i `manager.update_point_threadsafe` ile loop'a kopru atar.
- Backend-api uygulamasi icin `apps/backend-api/app/services/iec104/server.py`
  yapisini bu servisin paralel calismasi icin kapatabilirsiniz; iki yer ayni
  TCP portunu paylasirsa biri bind ederken digeri patlar.
