"""Cihaz yapilandirma dosyasi I/O semalari."""

from datetime import datetime

from pydantic import BaseModel, Field


class ConfigRow(BaseModel):
    """Dosyadaki tek bir ayar — arayuzun gosterdigi satir."""

    cat_index: str
    group: str
    index: str
    length: int
    # Kisa alanlar sayi, uzun alanlar metin olarak anlamli. Ikisi de doner ki
    # arayuz hangisini gosterecegine karar verebilsin.
    value_int: int | None = None
    value_text: str | None = None
    raw_hex: str
    # Katalog (Explorer XML) yuklenmemisse None kalir; katalog eksikligi
    # dosyayi goruntulenemez yapmamali.
    meaning: str | None = None
    unit: str | None = None
    # Ayarin NE ANLAMA geldigi (Horstmann manuelinden, katalogdaki "desc").
    # Arayuz tooltip'te gosterir; icerigi olmayan girdide None.
    description: str | None = None


class ConfigVersionRead(BaseModel):
    id: int
    device_id: int
    version: int
    source: str
    template_id: int | None = None
    note: str | None = None
    created_by: str | None = None
    created_at: datetime
    applied_at: datetime | None = None
    size_bytes: int
    # Okunan dosyanin saglama toplami tutuyor muydu? None = ayak yoktu.
    # "Gecerli", "gecersiz" ve "bilinmiyor" UC AYRI durumdur.
    checksum_valid: bool | None = None
    # Bu surum FTP'deki dosyaya da yazildi mi? Ekranda gorulen dosya ile
    # cihazin okuyacagi dosya AYNI olmali (kullanici sarti). True=yazildi,
    # False=denendi ama BASARISIZ (arayuz uyarir), None=bu istekte
    # denenmedi (orn. listeleme).
    ftp_written: bool | None = None

    model_config = {"from_attributes": True}


class ConfigApplicationRead(BaseModel):
    """Yapilandirmanin CIHAZA UYGULANMA sureci — arayuzun dogruyu soylemesi icin.

    NEDEN AYRI ALAN: `version.applied_at` bir BELGE alanidir ve artik
    YALNIZCA cihazin kendi kaniti goruldugunde dolar. Arasindaki butun
    asamalar (dosya hazir / cihaz bekleniyor / komut sirada / iletildi)
    burada tasinir.

    Arayuz bu alanlar olmadan yalnizca iki sey soyleyebilirdi: "uygulandi"
    ya da "hicbir sey yok". Uyuyan bir cihazda ikisi de yanlisti.
    """

    #: `cihaz_bekleniyor` | `kuyrukta` | `iletildi` | `dogrulandi` |
    #: `basarisiz` | `gecersiz_kilindi`
    state: str
    version: int
    requested_at: datetime
    requested_by: str | None = None
    #: Neden hala bekliyor (`device_session_readiness` gerekce sabitleri).
    #: `uykuda`, `erisilemez`, `bayat_gozlem`, `yeni_kanit_bekleniyor`...
    reason: str | None = None
    queued_at: datetime | None = None
    delivered_at: datetime | None = None
    verified_at: datetime | None = None
    #: `cihaz_dosyasi` (KESIN) | `damga_degisti` (ZAYIF). Kanit sinifi
    #: gorunur olmali: "dogrulandi" yazip nedenini gizlemek, sonradan
    #: guvenilirligi tartisilamaz hale getirirdi.
    verified_by: str | None = None
    failure_reason: str | None = None
    attempt: int = 0


class ConfigCurrentRead(BaseModel):
    """Cihazin guncel yapilandirmasi + gosterilecek satirlar.

    `filename` None olabilir: cihazin serisi (kayit/telemetri/kod) hicbir
    kaynaktan cozulemiyorsa dosya adi uretilmez. Bu durum kartin ACILMASINI
    ENGELLEMEZ (eskiden 500 veriyordu ve kart hic gelmiyordu); arayuz seri
    eksikligini acikca soyler, FTP esitleme ve uygulama devre disi kalir.
    """

    version: ConfigVersionRead
    filename: str | None = None
    rows: list[ConfigRow]
    # Cihazin KENDI bildirdigi son yapilandirma guncelleme zamani
    # (`master.info_last_configuration_update` telemetrisi, ham metin).
    # Kullanici "guncelleme gercekten oldu mu" sorusunu bununla dogrular:
    # komut sonrasi bu damga degistiyse cihaz dosyayi uygulamistir.
    device_last_update: str | None = None

    # --- DIAL-IN: ISTENEN vs CIHAZDAKI ---
    #
    # Arayuz "kaydettim = sahada gecerli" izlenimi VERMEMELI. Bu uc alan o
    # ayrimi tasir; ucu de tek kaynaktan (`device_config_service`) gelir.
    #
    #: OTORITE: operatorun Cihaz Ayarlari'nda sectigi deger. Gateway'in
    #: gecikme hesabi da, cihaza yazilan `2010C6` da budur.
    dial_in_configured_min: int | None = None
    #: Cihazin KENDI yazdigi dosyadan okunan deger — YALNIZCA TANILAMA.
    #: Saha kosullarinda readback guvenilir degil, bu yuzden hicbir gateway
    #: karari buna dayanmaz. Adlandirma bilerek `applied` DEGIL.
    dial_in_readback_min: int | None = None
    #: `eslesiyor` | `farkli` | `yok`. `farkli` bir ARIZA IDDIASI DEGILDIR:
    #: cihaz dosyasini henuz yazmamis ya da eski bir kopya yazmis olabilir.
    dial_in_readback_status: str = "yok"

    #: Devam eden ya da en son tamamlanan uygulama sureci. `None` = bu cihaz
    #: icin hic uygulama denenmemis.
    application: ConfigApplicationRead | None = None


class ConfigDiffRow(BaseModel):
    cat_index: str
    meaning: str | None = None
    before: str | None = None
    after: str | None = None
    before_int: int | None = None
    after_int: int | None = None


class ConfigChangeRequest(BaseModel):
    """CatIndex -> yeni sayisal deger. Ornek: {"2010C6": 720}"""

    changes: dict[str, int] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=500)


class TemplateRead(BaseModel):
    id: int
    name: str
    device_model: str
    source_filename: str | None = None
    note: str | None = None
    is_default: bool
    created_by: str | None = None
    created_at: datetime
    size_bytes: int

    model_config = {"from_attributes": True}


class BulkApplyRequest(BaseModel):
    """Bir sablonu SECILI cihazlara uygular.

    `device_ids` ZORUNLU ve acikca verilir — "tumu" gibi bir kisayol YOK.
    Yanlis sablonu 500 cihaza basmak geri alinmasi saatler suren bir hatadir;
    hedefin her zaman sayilabilir olmasi bilincli bir tercih.
    """

    template_id: int
    device_ids: list[int] = Field(min_length=1, max_length=1000)
    note: str | None = Field(default=None, max_length=500)


class BulkApplyResult(BaseModel):
    applied: list[int]
    # Basarisizlar SESSIZCE atlanmaz; hangi cihaz neden alamadi doner.
    failed: list[dict]
