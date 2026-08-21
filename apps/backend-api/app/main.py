import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import select as _select, text

from app.core.license_gate import LicenseGateMiddleware
from app.core.rate_limit import limiter

from app.api import alarm_rules, alarms, api_keys, auth, backups, bulk_notifications, device_configs, device_models, devices, events, faults, field_tools, firewall, ftp_settings as ftp_settings_api, gateways, grid_topology, health, internal, licensing, map_tiles, network, notification_settings, notifications as notifications_api, outbound_targets, project_settings as project_settings_api, public, remote_access, responsibility_areas, sessions as sessions_api, signals, system_admin, system_status, telemetry, telemetry_quarantine, user_notification_preferences, users, ws_live
from app.core.config import settings
from app.core import service_role
from app.core.service_role import leader
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import alarm, alarm_rule, api_key as api_key_model, backup as backup_model, device, device_model_settings as device_model_settings_model, fault as fault_model, gateway, gateway_ingest_batch, notification as notification_model, notification_settings as notification_settings_model, outbound_target, outbox_event, processed_message, project_settings as project_settings_model, responsibility_area as responsibility_area_model, signal_catalog, system_event, telemetry as telemetry_model, user, user_notification_preference as user_notif_pref_model  # noqa: F401
from app.services.iec104.bootstrap import deploy_all_active_targets, undeploy_all as iec104_undeploy_all
from app.services.signal_catalog_seed import seed_default_signals
from app.services import alarm_reconciliation, backup_scheduler, gateway_staleness_watchdog, gateway_update_notifier, outbox_flush_worker, telemetry_consumer, telemetry_retention

app = FastAPI(
    title=settings.app_name,
    description=(
        "EnerjiOne Grid backend. Web/mobile için JWT, dış sistemler için "
        "**API Key (Personal Access Token)** desteği var.\n\n"
        "**Public API:** `/api/v1/public/*` altında, `Authorization: Bearer hsl_pat_<token>` "
        "ile çağrılır. Token yönetimi için `/api/v1/api-keys` endpoint'lerine bakın."
    ),
    openapi_tags=[
        {"name": "auth", "description": "Kullanıcı oturumu (JWT)."},
        {"name": "api-keys", "description": "Kullanıcının Personal Access Token (PAT) yönetimi."},
        {
            "name": "public-api",
            "description": (
                "Dış sistemler için read-only REST endpoint'leri. API Key ile korunur. "
                "Path: `/api/v1/public/*`."
            ),
        },
        {"name": "devices", "description": "Cihaz yönetimi (web UI)."},
        {"name": "signals", "description": "Sinyal kataloğu yönetimi (web UI)."},
        {"name": "alarms", "description": "Alarm event ve yorumlar."},
        {"name": "alarm-rules", "description": "Alarm kuralları."},
        {"name": "outbound-targets", "description": "Outbound hedef (REST/MQTT/IEC104)."},
        {"name": "gateways", "description": "Gateway yönetimi."},
        {"name": "internal", "description": "Servis-token korumalı internal endpoint'ler."},
        {
            "name": "remote-access",
            "description": (
                "Uzaktan bakim izni (sureli). Erisim VARSAYILAN KAPALI; izni "
                "yalnizca `engineer` rolu verir ve sure dolunca host ajani "
                "otomatik kapatir."
            ),
        },
    ],
)

# Rate limiter — login ve diger endpoint'lerde brute-force koruma.
# Decorator (`@limiter.limit("5/minute")`) ile endpoint-specific limit;
# default limit yok (sadece explicit isaretlenen route'lar kontrolde).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Lisans kilidi — lisanssiz kurulumda api_prefix altindaki her sey 403.
# CORS'tan ONCE eklenir ki CORS middleware'i DISTA kalsin: aksi halde 403
# yanitina CORS basliklari eklenmez ve tarayici hatayi okuyamaz (opak hata
# "Failed to fetch" olarak gorunur). Beyaz liste icin bkz. license_gate.py.
app.add_middleware(LicenseGateMiddleware)

_cors_origins = settings.cors_origin_list
# X-Total-Count: /events sayfalama toplami; Content-Disposition: export dosya
# adi. Cross-origin (Vite dev :5173) istemcisinin okuyabilmesi icin expose
# edilmeli — same-origin production'da zaten gorunur.
_cors_expose = ["X-Total-Count", "Content-Disposition"]
if "*" in _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=".*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=_cors_expose,
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=_cors_expose,
    )

app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(devices.router, prefix=settings.api_prefix)
app.include_router(device_models.router, prefix=settings.api_prefix)
app.include_router(device_configs.router, prefix=settings.api_prefix)
app.include_router(ftp_settings_api.router, prefix=settings.api_prefix)
app.include_router(licensing.router, prefix=settings.api_prefix)
app.include_router(responsibility_areas.router, prefix=settings.api_prefix)
app.include_router(gateways.router, prefix=settings.api_prefix)
app.include_router(telemetry.router, prefix=settings.api_prefix)
app.include_router(alarms.router, prefix=settings.api_prefix)
app.include_router(faults.router, prefix=settings.api_prefix)
app.include_router(user_notification_preferences.router, prefix=settings.api_prefix)
app.include_router(user_notification_preferences.admin_router, prefix=settings.api_prefix)
app.include_router(events.router, prefix=settings.api_prefix)
app.include_router(users.router, prefix=settings.api_prefix)
app.include_router(notification_settings.router, prefix=settings.api_prefix)
app.include_router(bulk_notifications.router, prefix=settings.api_prefix)
app.include_router(outbound_targets.router, prefix=settings.api_prefix)
app.include_router(signals.router, prefix=settings.api_prefix)
app.include_router(alarm_rules.router, prefix=settings.api_prefix)
app.include_router(internal.router, prefix=settings.api_prefix)
app.include_router(project_settings_api.router, prefix=settings.api_prefix)
app.include_router(grid_topology.router, prefix=settings.api_prefix)
app.include_router(system_status.router, prefix=settings.api_prefix)
app.include_router(network.router, prefix=settings.api_prefix)
app.include_router(field_tools.router, prefix=settings.api_prefix)
app.include_router(remote_access.router, prefix=settings.api_prefix)
app.include_router(firewall.router, prefix=settings.api_prefix)
app.include_router(map_tiles.router, prefix=settings.api_prefix)
app.include_router(notifications_api.router, prefix=settings.api_prefix)
app.include_router(sessions_api.router, prefix=settings.api_prefix)
app.include_router(backups.router, prefix=settings.api_prefix)
app.include_router(system_admin.router, prefix=settings.api_prefix)
app.include_router(telemetry_quarantine.router, prefix=settings.api_prefix)
# API Key yonetimi (kullanici kendi PAT'larini olusturup revoke eder).
app.include_router(api_keys.router, prefix=settings.api_prefix)
# Public REST API (dis sistemlerin tukettigi, API Key korumali, read-only).
# Path: /api/v1/public/* — versiyonlama icin ileride /api/v2/public... acilabilir.
app.include_router(public.router, prefix=settings.api_prefix)
# WebSocket endpoint: api_prefix altinda /ws/live-values
app.include_router(ws_live.router, prefix=settings.api_prefix)


@app.on_event("startup")
def komut_kimligi_tabanini_yukselt():
    """Bilinen en yuksek komut kimligini surec sayacina isler.

    TEK BASINA BIR GUVENCE DEGILDIR ve oyle sunulmamali: `max(id)` degeri de
    yedegin ICINDEDIR, yani veritabani geri alindiginda o da geriye doner.
    Restore'a karsi guvence kimligin DUVAR SAATI bileseni (bkz.
    `command_identity`).

    Buradaki dar amac: saat bir sekilde geri gitmisken (NTP duzeltmesi)
    uretilecek kimligin, AYNI veritabaninda ZATEN duran bir kimlikle
    cakismasini engellemek.
    """
    try:
        from sqlalchemy import func, select

        from app.models.device_command import DeviceCommand
        from app.services import command_identity

        db = SessionLocal()
        try:
            command_identity.taban_yukselt(
                db.scalar(select(func.max(DeviceCommand.id)))
            )
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        # Taban okunamazsa kimlik uretimi YINE DE calisir (saat bileseni
        # yeterlidir); backend'i ayaga kalkmaktan alikoymamali.
        print(f"[startup] komut kimligi tabani okunamadi: {exc}")


@app.on_event("startup")
def reconcile_remote_access_audit():
    """Backend kapaliyken kapanan uzaktan bakim oturumlarini denetime yaz.

    Sure dolunca kapatmayi host ajani (e1-rad) yapar; backend bunu ancak bir
    durum okumasinda ogrenir. Sayfa hic acilmasa bile guncelleme/restart
    sonrasi yakalansin diye burada bir kez daha uzlastiriyoruz.
    """
    try:
        from app.services import remote_access_service

        db = SessionLocal()
        try:
            remote_access_service.reconcile_audit(db)
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        # Denetim uzlastirmasi backend'i ayaga kalkmaktan ALIKOYMAMALI.
        print(f"[startup] uzaktan bakim denetim uzlastirmasi atlandi: {exc}")


@app.on_event("startup")
def dogrula_sema_uyumlulugu():
    """Semanin bu imajla uyumlu oldugunu DOGRULAR — degistirmez.

    ESKIDEN BURADA NE VARDI
    -----------------------
    `create_tables()` acilista `Base.metadata.create_all()` cagirip ardindan
    ~124 idempotent DDL ifadesi (106 ALTER TABLE, 11 CREATE TABLE, 12 CREATE
    INDEX, 2 ALTER TYPE) kosuyordu. Yani eksik sema calisma zamaninda SESSIZCE
    "onariliyordu" ve sema otoritesi fiilen buradaydi.

    O blok 0072 ile Alembic'e devredildi: 0072 guncel semanin tamamini explicit
    operasyonlarla kurar. Artik acilista sema DEGISMEZ, yalnizca DOGRULANIR.

    Uyumsuzlukta surec CANLI kalir ama `/health/ready` 503 doner (bkz.
    `app/db/schema_guard.py` — crash-loop yerine gorunur NOT READY).
    """
    import logging as _logging

    from app.db import schema_guard

    # Kontrolun KENDISI de acilisi durdurmamali: bu bir startup event'idir ve
    # buradan cikan exception uvicorn'u sonlandirip crash-loop uretir. Sema
    # gercekten uyumsuzsa dogru yanit NOT READY'dir (503), karanlik bir
    # appliance degil. Gercek engelleyici durum zaten `migrate_db` tarafindan
    # uvicorn BASLAMADAN once yakalanir.
    try:
        schema_guard.dogrula_ve_isaretle(engine)
    except Exception:  # noqa: BLE001
        _logging.getLogger(__name__).exception(
            "sema_uyumluluk_kontrolu_yapilamadi — backend YINE DE aciliyor"
        )


@app.on_event("startup")
def seed_fabrika_verisi():
    """Fabrika VERISINI tohumlar (sema DEGIL).

    Sema DDL'i 0072'ye tasindi; bu kanca yalnizca uygulama verisi kurar:
    sinyal katalogu senkronu ve SN2 fabrika config sablonu. Ikisi de
    idempotent ve kullanici degisikliklerine saygilidir.
    """
    import logging as _logging

    # NEDEN try/except: bu kanca bir startup event'idir. Buradan cikan her
    # exception uvicorn'u "Application startup failed." ile sonlandirir ve
    # `restart: unless-stopped` altinda appliance SONSUZ CRASH-LOOP'a girer —
    # operator arayuze ulasip teshis bile yapamaz.
    #
    # Tohumlama EKSIKLIGI bunu hak etmez: sinyal katalogu ya da fabrika
    # sablonu kurulamazsa sistem calismaya devam eder, eksik olan sey
    # arayuzden gorulur ve elle duzeltilebilir. Karanlik bir kutu daha
    # kotudur. (Bu koruma eskiden `create_tables`'in try/except'inden
    # geliyordu; blok kaldirilinca burada ACIKCA yeniden kuruldu.)
    try:
        db = SessionLocal()
        try:
            # strict=False: ACILISTA SILME YOK.
            #
            # Eskiden `strict=True` idi ve JSON listesinde olmayan HER sinyali
            # siliyordu — ama `POST /signals` kurulumcuya sinyal YARATMA izni
            # veriyor. Yani operatorun ekledigi sinyaller ilk yeniden baslatmada
            # sessizce yok oluyordu. Fabrikaya donmek isteyen icin zaten ayri ve
            # BILINCLI bir uc var: `POST /signals/reset-to-defaults`.
            #
            # `respect_user_overrides` (varsayilan True) ise elle degistirilmis
            # ALANLARI korur: `_MUTABLE_FIELDS` tam da arayuzden duzenlenen
            # alanlari (label, scale, offset, iec104_ioa, dnp3_index...) icerdigi
            # icin her acilis, kaydedilmis ve denetim kaydi tutulmus degisiklikleri
            # sessizce geri aliyordu. Fabrika duzeltmeleri DOKUNULMAMIS alanlara
            # gelmeye devam eder.
            result = seed_default_signals(db, strict=False)
            if not result.get("skipped"):
                import logging

                logging.getLogger(__name__).info(
                    "signal_catalog seed sync -> inserted=%d updated=%d removed=%d "
                    "korunan_alan=%d",
                    result.get("inserted", 0),
                    result.get("updated", 0),
                    result.get("removed", 0),
                    result.get("kept", 0),
                )
            # NOT: Burada bir `flush_outbox(db)` cagrisi vardi ve KALDIRILDI
            # (2026-08-03). `leader`/`service_role` korumasi YOKTU: bu yol her
            # surecte kosuyor, yani `--workers N`e gecildiginde N surec acilista
            # ayni satirlari yayinlamaya kalkiyordu. Yayin artik TEK yerden,
            # `leader.register("outbox_flush", ...)` arkasindaki worker'dan yapilir
            # ve o en gec OUTBOX_FLUSH_INTERVAL_SEC (0.3 sn) icinde ayni isi yapar.

            # Fabrika config sablonu: SN2 icin HIC sablon yoksa depoyla gelen
            # dogrulanmis dosya varsayilan yapilir. Sablonsuz kurulumda "yeni
            # cihaza otomatik config" kancasi hic tetiklenemiyordu; bos durumdaki
            # "Sablondan olustur" dugmesi de buna dayanir. Coklu surec guvenli:
            # varlik kontrolu var, yaris halinde en kotu iki sablon olusur ve
            # tek varsayilan kurali zaten uygulanir.
            try:
                from app.services import device_config_service as _cfg_svc

                if _cfg_svc.seed_factory_template(db) is not None:
                    db.commit()
                    import logging

                    logging.getLogger(__name__).info("fabrika config sablonu yuklendi")
            except Exception:  # noqa: BLE001 - seed eksikligi acilisi engellemez
                import logging

                logging.getLogger(__name__).warning(
                    "fabrika config sablonu yuklenemedi", exc_info=True
                )
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        _logging.getLogger(__name__).exception(
            "fabrika_verisi_seed_basarisiz — backend YINE DE aciliyor. "
            "Sinyal katalogu/fabrika sablonu eksik olabilir; Sistem Durumu "
            "ekranindan kontrol edin."
        )


@app.on_event("startup")
async def reapply_gateway_rabbitmq_permissions():
    """Eski sistem icin gateway RabbitMQ user permission yenileme.

    Gateway artik telemetriyi JetStream'e basiyor, RabbitMQ kullanmiyor;
    bu task'in islevi yok. Geriye uyumluluk icin no-op olarak birakildi
    (eski .env'lerde RABBITMQ_ADMIN_* set edilmis olsa bile baska bir
    sebebe sebep olmasin). Eski olusturulan RabbitMQ user'lar pasif kalir;
    operator istiyorsa rabbitmqctl ile elle silebilir.
    """
    pass


#: `start_iec104_servers`ta yakalanan FastAPI event loop'u. IEC 104
#: sunuculari bu loop uzerinde yasar; liderlik SONRADAN devralinirsa deploy
#: bu loop'a gonderilir (bkz. `_start_iec104_on_leadership`).
_iec104_loop: "asyncio.AbstractEventLoop | None" = None

#: Sunucular bu surecte acildi mi. Iki giris yolu var (acilis hizli yolu ve
#: liderligi devralma yolu); bayrak olmasa ikisi ust uste gelip
#: `manager.deploy` once undeploy yaptigi icin bagli SCADA oturumunu bosuna
#: koparirdi.
_iec104_deployed = False


async def _deploy_iec104_once() -> None:
    """Aktif IEC 104 hedeflerini bu surecte BIR KEZ ayaga kaldirir."""
    global _iec104_deployed
    import logging

    if _iec104_deployed:
        return
    _iec104_deployed = True
    db = SessionLocal()
    try:
        deployed = await deploy_all_active_targets(db, loop=asyncio.get_running_loop())
        if deployed:
            logging.getLogger(__name__).info("iec104_servers_deployed count=%d", deployed)
    except Exception:  # noqa: BLE001
        # Bayragi geri al: sonraki bir liderlik denemesi tekrar deneyebilsin.
        _iec104_deployed = False
        logging.getLogger(__name__).exception("iec104_startup_failed")
    finally:
        db.close()


def _start_iec104_on_leadership() -> None:
    """LIDERLIK ALINDIGINDA IEC 104 sunucularini ac — DEVRALMA yolu.

    NEDEN KAYITLI BIR IS
    --------------------
    `start_iec104_servers` kilidi acilista bir kez dener. Kilit o an baska bir
    oturumdaysa eskiden sessizce vazgeciliyordu ve BIR DAHA denenmiyordu —
    oysa `try_start` 15 sn'de bir yeniden dener ve bu surec pekala LIDER
    olabilir:

      * `update.sh` sonrasi eski container SIGKILL edilmisse Postgres onun
        advisory lock oturumunu TCP keepalive suresi boyunca (varsayilan
        saatler) acik tutar; yeni surec acilirken kilidi ALAMAZ, saniyeler
        sonra alir.
      * `--workers N` ile IEC 104'u acan worker olur ve baska bir worker
        liderligi devralir.

    Her iki durumda da surec LIDER olur, telemetri/outbox/retention calisir,
    `/health` "is_leader: true" der — ama 2404 HIC baglanmaz. SCADA'ya giden
    butun ariza-gecis ve kalite bildirimleri sessizce kaybolurdu.

    SONUC BEKLENMEZ: Starlette senkron startup handler'ini (bkz.
    `start_background_jobs`) dogrudan event loop THREAD'INDE kosturur;
    `future.result()` orada kilitlenirdi. Hata `_deploy_iec104_once` icinde
    zaten loglaniyor.
    """
    import logging

    loop = _iec104_loop
    if loop is None or _iec104_deployed:
        return
    try:
        asyncio.run_coroutine_threadsafe(_deploy_iec104_once(), loop)
    except RuntimeError:  # loop kapanmis
        logging.getLogger(__name__).exception("iec104_devralma_planlanamadi")


# Kayit SIRASI baslatma sirasidir; IEC 104 en basa gelsin ki telemetri
# tuketicisi deger basmaya basladiginda sunucular ayakta olsun. Durdurma
# `stop_iec104_servers` shutdown event'inde yapiliyor, burada no-op.
leader.register("iec104_servers", _start_iec104_on_leadership, lambda: None)


@app.on_event("startup")
async def start_iec104_servers():
    """Aktif IEC 104 outbound target'lari icin TCP server'lari baslat.

    `create_tables` tamamlandiktan sonra calisir (FastAPI startup event'lari
    tanımlanma sirasina göre kosturulur). IEC 104 sunucularinin yaşam
    dongusu FastAPI loop icindedir; thread dongusundeki
    `outbound_dispatch_service._dispatch_iec104` `call_soon_threadsafe` ile
    degerleri guvenli iletir.
    """
    global _iec104_loop

    # Loop LIDERLIK KONTROLUNDEN ONCE yakalanir: kilit su an baskasinda olsa
    # bile bu surec sonradan lider olabilir ve o zaman deploy'un gonderilecegi
    # bir loop gerekir. Yalnizca async startup event'inde elde edilebilir.
    _iec104_loop = asyncio.get_running_loop()

    # TEK SURECTE: IEC104 sunuculari TCP portu baglar.
    #
    # Burada eskiden `service_role.wants_background()` vardi ve YETMIYORDU:
    # o bayrak rol `all` iken HER uvicorn worker'inda True'dur. `--workers 4`
    # ile dort surec de `deploy_all_active_targets` cagirip ayni netns icinde
    # 2404'e bind denerdi; ucu EADDRINUSE alip sessizce (try/except) duserdi.
    # Kazanan surec rastgeledir ve o surec LIDER DEGILSE bagli SCADA istemcisi
    # acik ama SESSIZ bir sunucuya baglanirdi — tam bir sessiz ariza.
    #
    # `leader.claim()` advisory lock'u ALIR, yani kumede tek surecte True
    # doner. Isleri baslatmaz; onu asagidaki `start_background_jobs` yapar ve
    # ayni kilidi tekrar almaya calismaz.
    if not leader.claim():
        # Kilit su an BASKASINDA. Vazgecmek YETMEZ: liderlik 15 sn'de bir
        # yeniden denenir ve bu surec devralabilir. O yol `_start_jobs`ten
        # gecer; IEC 104 kayitli bir is oldugu icin orada acilir.
        return

    await _deploy_iec104_once()


@app.on_event("shutdown")
async def stop_iec104_servers():
    import logging

    try:
        await iec104_undeploy_all()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("iec104_shutdown_failed")


@app.on_event("startup")
def start_jetstream_bus():
    """NATS JetStream — dual-publish/dual-consume aktifse baslatir.

    NATS_DUAL_PUBLISH_ENABLED ve NATS_CONSUME_ENABLED ikisi de kapali ise
    bu fonksiyon hicbir sey yapmaz (log: skipped). nats-py paketi yoksa
    veya NATS server'a baglanilamiyorsa warning log + skip — backend
    calismaya devam eder, RabbitMQ akisi etkilenmez.

    Telemetry consumer'dan ONCE baslatilir ki consume tarafi acildiginda
    bus hazir olsun.
    """
    import logging

    try:
        from app.services.jetstream_bus import start_bus_if_enabled

        start_bus_if_enabled()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("jetstream_bus_startup_unexpected_error")

    # RabbitMQ vhost izolasyonu — production'da `/` reddedilir, `e1` (veya
    # operator'in tanimladigi) vhost dedicated. Backend startup'inda admin
    # API uzerinden vhost'u idempotent olarak ensure edip kendi
    # kullanicisina yetki verir. Yetersizse warning + devam (broker offline
    # senaryosunda backend hala healthcheck ile yukselebilsin); ileride
    # publish/consume cagrisi 403/404 atinca operator goruler.
    try:
        from app.core.config import settings as _s
        from app.services.rabbitmq_admin import RabbitMqAdminClient

        if _s.rabbitmq_vhost and _s.rabbitmq_vhost != "/":
            admin = RabbitMqAdminClient(
                management_url=_s.rabbitmq_management_url,
                admin_username=_s.rabbitmq_admin_username,
                admin_password=_s.rabbitmq_admin_password,
            )
            admin.ensure_vhost(_s.rabbitmq_vhost)
            try:
                admin.grant_admin_on_vhost(
                    vhost=_s.rabbitmq_vhost,
                    admin_username=_s.rabbitmq_admin_username,
                )
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).debug(
                    "rabbitmq_admin_grant_failed (idempotent ok if pre-existing)",
                    exc_info=True,
                )
            logging.getLogger(__name__).info(
                "rabbitmq_vhost_ensured vhost=%s", _s.rabbitmq_vhost
            )
    except Exception as exc:  # noqa: BLE001
        # Vhost ensure starting'i bloklamasin — broker olmasa bile backend
        # ayagi kalsin (health endpoint zaten broker durumunu raporlar).
        logging.getLogger(__name__).warning(
            "rabbitmq_vhost_ensure_failed error=%s "
            "(broker reachable degil olabilir; vhost manual yaratilirsa devam eder)",
            exc,
        )


@app.on_event("shutdown")
def stop_jetstream_bus():
    import logging

    try:
        from app.services.jetstream_bus import stop_bus

        stop_bus()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).debug("jetstream_bus_shutdown_error", exc_info=True)


@app.on_event("startup")
def start_ws_fanout_bridge():
    """Canli deger yayinini surecler arasi tasiyan NATS koprusu.

    TUKETICIDEN ONCE baslatilir: tuketici ilk mesaji yayinladiginda kopru
    hazir olsun, yoksa o mesajlar bellek-ici yola duser (zararsiz ama
    gereksiz).

    Kopru kurulamazsa (NATS kopuk / nats-py yok / ayar kapali) yayin
    bellek-ici calismaya devam eder — TEK surecte davranis aynidir. Coklu
    surece gecildiginde ise kopru ZORUNLUDUR: olmadan tuketici baska bir
    surecte oldugu icin WS istemcilerine hicbir sey ulasmaz ve bu ariza
    SESSIZ olur (soket bagli gorunur, deger akmaz). Bu yuzden
    `/health` ve ws_broadcaster.stats() icinde `bridge_ready` raporlanir.
    """
    from app.services.ws_broadcaster import bridge, broadcaster

    bridge.start(on_message=broadcaster._deliver_local)


@app.on_event("shutdown")
def stop_ws_fanout_bridge():
    from app.services.ws_broadcaster import bridge

    bridge.stop()


# --------------------------------------------------------------------------
# ARKA PLAN ISLERI — TEK SURECTE
#
# Asagidaki isler daha once her biri ayri bir startup event'i olarak
# aciliyordu. Tek uvicorn worker varken bu dogruydu; olcek buyudukce (600
# cihaz) API'yi coklu worker ile calistirmak ve arka plani ayri bir
# container'a almak gerekiyor. Ikisi de ayni tehlikeyi doguruyor: is birden
# fazla surecte TEKRAR calisir. Sonuclar sessiz ve pahali — yedek iki kez
# alinir, toplu bildirim iki kez gider, retention ayni satirlari iki kez
# silmeye calisir.
#
# Artik isler `leader`a kaydediliyor ve yalnizca Postgres advisory lock'u
# ALAN surecte aciliyor. Rol yapilandirmasi (all/api/worker) niyeti soyler,
# kilit gercekten tekil OLDUGUNU garanti eder. Bkz. core/service_role.py.
#
# Kayit SIRASI onemli: baslatma bu sirada, durdurma TERS sirada yapilir.
# --------------------------------------------------------------------------
# TELEMETRI TUKETICISI BILEREK LEADER'DA DEGIL — surec basina calisir.
#
# Leader kilidi "kumede TEK KOPYA calismali" isler icindir (retention,
# zamanlanmis isler...). Tuketici tam tersi: JetStream durable'lari paralel
# uyeler arasinda mesaj PAYLASIR ve persist kodu paralel tuketiciye
# dayaniklidir (dedup IN on-kontrolu, ON CONFLICT, source_timestamp
# korumasi, savepoint karantinasi). Kilit altindayken UVICORN_WORKERS=4
# acmak 3 sureci bosta biraktiriyordu; 400 cihazda tek surec ~2.4k msj/sn
# tavanindaydi. Artik arka plan rolu tasiyan HER surec tuketir —
# UVICORN_WORKERS dogrudan persist kapasitesini carpar.
# Baslatma/durdurma startup/shutdown kancalarinda (bkz. asagida
# _telemetri_tuketici_baslat/_durdur).

# Outbox flush — telemetri yayinini ingest request yolundan ayirir. Ingest
# sadece DB'ye yazar; bu worker arka planda RabbitMQ'ya yayinlar. 200 cihaz
# yukunde gateway 'Read timed out' onlenir.
leader.register("outbox_flush", outbox_flush_worker.start, outbox_flush_worker.stop)


def _start_outbound_batcher() -> None:
    """REST webhook batch dispatcher — 5sn penceresinde biriktirip tek POST."""
    from app.services import outbound_telemetry_batcher

    outbound_telemetry_batcher.start()


def _stop_outbound_batcher() -> None:
    from app.services import outbound_telemetry_batcher

    outbound_telemetry_batcher.stop()


leader.register("outbound_telemetry_batcher", _start_outbound_batcher, _stop_outbound_batcher)


def _start_mqtt() -> None:
    """Her aktif MQTT target icin kalici client + periyodik flush."""
    from app.services import mqtt_publisher_service

    mqtt_publisher_service.start()


def _stop_mqtt() -> None:
    from app.services import mqtt_publisher_service

    mqtt_publisher_service.stop()


leader.register("mqtt_publisher", _start_mqtt, _stop_mqtt)


def _start_bulk_notifications() -> None:
    """Zamanlanmis toplu bildirim — her 30sn'de bir due job'lari isler."""
    from app.services import bulk_notification_scheduler

    bulk_notification_scheduler.start()


def _stop_bulk_notifications() -> None:
    from app.services import bulk_notification_scheduler

    bulk_notification_scheduler.stop()


leader.register("bulk_notification_scheduler", _start_bulk_notifications, _stop_bulk_notifications)

# Telemetri kayan penceresi — eskileri otomatik temizler (varsayilan: son 30
# dakika, 5 dakikada bir DELETE).
leader.register("telemetry_retention", telemetry_retention.start, telemetry_retention.stop)

# Acik alarmlarin periyodik mutabakati — kosul artik karsilanmiyorsa onaylanmis
# ise siler, onaylanmamis ise reset=True yapar. alarm-service'in bellek-ici
# durumundan bagimsiz self-healing saglar.
leader.register("alarm_reconciliation", alarm_reconciliation.start, alarm_reconciliation.stop)

# Gateway susarsa cihazlari yesil birakma. `communication_status` yalnizca
# telemetri okumasi geldiginde guncelleniyor; gateway tamamen sustugunda hic
# okuma gelmez ve tum cihazlar son durumlarinda (genellikle ONLINE) donar.
# Bkz. gateway_staleness_watchdog — CIHAZ bazli veri-yasi kontrolu BILEREK
# yapilmiyor, o yanlis alarm uretiyordu.
leader.register(
    "gateway_staleness",
    gateway_staleness_watchdog.start,
    gateway_staleness_watchdog.stop,
)

# Gateway icin yeni imaj yayinlandiginda TUM kullanicilara bir kez bildirim.
# "Bir kez" onemli: kontrol periyodik, her turda bildirim gondermek bildirim
# merkezini kullanilamaz hale getirirdi (bkz. gateway_update_notifier).
leader.register(
    "gateway_update_notifier",
    gateway_update_notifier.start,
    gateway_update_notifier.stop,
)

# Periyodik yedekleme. IKI KEZ CALISMASI ozellikle pahali: her tetikte pg_dump
# kosar, diski ve CPU'yu iki katina cikarir ve retention sayimini bozar.
leader.register("backup_scheduler", backup_scheduler.start, backup_scheduler.stop)

# Harici FTP yoklayicisi — FTP modu "harici" iken musterinin sunucusundaki
# `<seri>_Configuration.csv` dosyalarini tarayip surume cevirir. Gomulu modda
# hicbir sey yapmaz (orada olaylar ftp-server callback'lerinden aninda gelir).
# Tek surecte kosmali: iki yoklayici ayni dosyayi iki kez indirir ve ayni
# anda iki surum yaratmaya kalkabilirdi (unique kisitta patlar).
from app.services import ftp_poll_worker  # noqa: E402

leader.register("ftp_config_poll", ftp_poll_worker.start, ftp_poll_worker.stop)

# Bekleyen HAT ARIZASI bildirimleri. Ariza motoru kaydi acar ama gonderim
# yapmaz; gonderimi tetikleyen tek yer notification-worker'in ALARM yoluydu.
# Ariza kaydi debounce yuzunden o dispatch'ten SONRA olusabildigi icin tekil
# arizalarda bildirim hic gitmiyordu (bkz. fault_notify_sweeper docstring'i).
# Tek surecte kosmali: iki sureduren ayni arizayi ayni anda gonderebilirdi
# (`notified_at` damgasi yazilmadan once ikisi de "bekliyor" gorur).
from app.services import fault_notify_sweeper  # noqa: E402

leader.register(
    "fault_notify_sweeper", fault_notify_sweeper.start, fault_notify_sweeper.stop
)

# Sonucu hic gelmeyen cihaz komutlarini sonlandirir (F3C-C). `sent` durumunda
# kalan bir komutu HICBIR SEY sonlandirmiyordu: gateway sonucu teslim edemezse
# komut sonsuza kadar "gonderildi" gorunuyor, operator kesicinin surulup
# surulmedigini ogrenemiyordu. Tek surecte kosmali — aksi halde her API
# worker'i ayni komut icin ayri bir olay uretirdi.
from app.services import command_result_sweeper  # noqa: E402

leader.register(
    "command_result_sweeper", command_result_sweeper.start, command_result_sweeper.stop
)

# Kritik ALTYAPI olaylarinin operatore bildirilmesi. `record_event(...)` tek
# basina kimseye bir sey gondermiyordu: bildirim zinciri yalnizca CIHAZ
# ALARMLARI icin isliyordu. Yani "telemetri tamponu tasti, olcum kayboldu"
# ya da "disk kritik" olaylari, birinin arayuze girip Olaylar ekranina
# bakmasina bagliydi (bkz. infra_notify_sweeper docstring'i).
#
# NEDEN BURADA (arka plan surecinde) ve NEDEN olayi URETEN yerde DEGIL:
# gonderim ag I/O'sudur; disk_guard'in ya da telemetri tuketicisinin icine
# konsaydi yanit vermeyen bir SMTP relay'i onlari bloke ederdi.
#
# Tek surecte kosmali: iki tarayici ayni olayi ayni anda gonderebilir ve
# soguma damgasi yazilmadan once ikisi de "bildirilmemis" gorurdu.
from app.services import infra_notify_sweeper  # noqa: E402

leader.register(
    "infra_notify_sweeper", infra_notify_sweeper.start, infra_notify_sweeper.stop
)

# Yarim kalmis restore / artik staging veritabani tespiti. Guvenli restore
# akisi cutover'i iki `ALTER DATABASE RENAME` ile yapiyor; tam o pencerede
# guc giderse uretim veritabani `_pre_<zaman>` adinda kalir ve backend
# acilamaz. Bu kontrol durumu okuyup operatore GORUNUR kilar.
#
# HICBIR VERITABANI OTOMATIK SILINMEZ — yalnizca tespit ve olay kaydi.
# Tek surecte kosmali: her uvicorn sureci ayri ayri tespit etseydi her
# acilista N kopya olay kaydi olusurdu.
from app.services import safe_restore as _safe_restore  # noqa: E402

leader.register(
    "restore_recovery_check",
    _safe_restore._acilista_kurtarma_baslat,
    lambda: None,
)


@app.on_event("startup")
def start_background_jobs():
    """Kilidi almayi dener; alirsa kayitli tum arka plan islerini baslatir.

    Kilit baska bir surecte ise burada HICBIR SEY acilmaz ve periyodik olarak
    yeniden denenir — worker container'i yeniden baslatildiginda devralmayi
    saglayan sey budur.
    """
    leader.try_start()


@app.on_event("startup")
def _telemetri_tuketici_baslat():
    """Telemetri tuketicisi LEADER'SIZ, surec basina calisir.

    JetStream durable'i mesajlari paralel uyeler arasinda paylasir; persist
    kodu paralel tuketiciye dayanikli (dedup, ON CONFLICT, damga korumasi).
    UVICORN_WORKERS=N ile worker container'i N tuketici surec calistirir —
    persist kapasitesi dogrudan carpilir. API rolunde ACILMAZ.
    """
    if service_role.wants_background():
        telemetry_consumer.start()


@app.on_event("shutdown")
def stop_background_jobs():
    if service_role.wants_background():
        telemetry_consumer.stop()
    leader.stop()
