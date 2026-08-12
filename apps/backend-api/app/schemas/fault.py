"""Fault (ariza) sema'lari — frontend "Hat Arizalari" sayfasi icin."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FaultTriggerAlarm(BaseModel):
    """Arizayi doguran alarm — "bu ariza NEDEN acildi" sorusunun cevabi.

    Ariza kaydi hangi DIREK ARALIGI oldugunu soyluyordu ama hangi olayin
    onu actigini soylemiyordu; operator alarm sayfasina gidip zaman
    damgasindan eslestirmek zorunda kaliyordu.

    `signal_source` kritik: bir Horstmann SN2 govdesi uc ayri faz
    sensorudur (master + sat01 + sat02) ve her biri hattin FARKLI bir
    fazina takilir. "Hangi fazda ariza var" bilgisi dogrudan alarmin
    signal_key prefix'inden gelir.
    """

    id: int
    title: str
    description: str | None = None
    level: str
    signal_key: str | None = None
    #: signal_key prefix'i: "master" | "sat01" | "sat02" (yoksa None).
    signal_source: str | None = None
    device_id: int
    device_code: str | None = None
    device_name: str | None = None
    acknowledged: bool = False
    created_at: datetime


class FaultBranchRef(BaseModel):
    """Ariza bolgesinin ICINDE kalan bir bransman kolu.

    NEDEN: hat tek bir zincir degil — dallanma diregine bagli kollar var ve
    kol AYRI bir hattir (`Line.branched_from_pole_id`). Ana hattaki ariza
    araligi bir dallanma diregini kapsiyorsa o kol da enerjisiz kalir ya da
    ariza dogrudan kolda olabilir; ekip sahaya ciktiginda kolu da kontrol
    etmelidir. Bu bilgi hicbir yerde gorunmuyordu: operator ana hattaki
    araliga bakip kolu atliyordu.
    """

    line_id: int
    line_name: str
    #: Kolun ayrildigi direk — arama bu direkten baslar.
    branch_pole_seq: int | None = None
    branch_pole_name: str | None = None
    #: Kolda O AN acik bir ariza kaydi var mi (yani ariza kolda dogrulandi).
    has_own_fault: bool = False


class FaultEventRead(BaseModel):
    id: int
    line_id: int
    line_name: str
    region_id: int
    region_name: str

    last_red_device_id: int
    last_red_device_code: str | None = None
    last_red_device_name: str | None = None
    first_green_device_id: int | None = None
    first_green_device_code: str | None = None
    first_green_device_name: str | None = None

    from_pole_id: int
    to_pole_id: int
    from_pole_seq: int | None = None
    to_pole_seq: int | None = None

    # Tel mesafesi (metre) — hat basindan olculur, kus ucusu degil hat boyunca.
    # NULL: topoloji/koordinat eksik ya da kayit henuz recompute'tan gecmedi.
    zone_start_m: float | None = None
    zone_end_m: float | None = None
    zone_length_m: float | None = None

    #: ARALIGIN KIMLIGI — "L12/D34>D35". Ariza bir hattin degil, iki cihaz
    #: arasindaki araligin olayidir; tekrar sayimi ve risk puani bu kodla
    #: yapilir. Araya cihaz eklenirse kod degisir (yeni aralik). Eski
    #: kayitlarda NULL olabilir.
    zone_code: str | None = None

    status: str
    opened_at: datetime
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    note: str | None = None
    #: Kapanis gerekcesi (salt okunur; `closed` disinda bos).
    resolution_note: str | None = None

    assigned_to_username: str | None = None
    assigned_at: datetime | None = None
    assigned_to_full_name: str | None = None
    #: Atanan KISININ profil fotografi. Bas harf rozeti yerine yuz gosterilir:
    #: sahayla telefonda konusan kisi "kim gidiyor" sorusunu isimden once
    #: yuzden cevapliyor. Yoksa arayuz bas harflere duser.
    assigned_to_avatar_url: str | None = None
    #: EKIBE ATAMA — kisi yerine bir sorumluluk alani (ekip) sorumlu olabilir.
    #: Kisi ile ekip AYNI ANDA dolu olmaz (bkz. assign ucu).
    assigned_to_area_id: int | None = None
    assigned_to_area_name: str | None = None

    comment_count: int = 0

    #: Arizayi doguran ACIK alarmlar (son "gordum" diyen cihazdan), en yeni
    #: once. Bos liste: alarm bu arada normale dondu ya da kayit eski.
    trigger_alarms: list[FaultTriggerAlarm] = []

    #: Ariza araliginin icinde kalan bransman kollari — sahada kontrol
    #: edilmesi gereken ek hatlar. Bos liste: aralikta dallanma yok.
    affected_branches: list[FaultBranchRef] = []
    #: Bu ariza kaydinin KENDISI bir bransman kolunda mi? Arayuz basligi
    #: "ANA HAT > BR-1 kolu" seklinde gosterir.
    is_branch_line: bool = False
    parent_line_id: int | None = None
    parent_line_name: str | None = None

    #: ARALIK IKI HATTI BIRLESTIREN TELDE MI?
    #:
    #: Bir bransman kolunun ilk acikligi, ana hattaki dallanma diregi ile
    #: kolun ilk diregi arasindaki teldir. Bu tel HICBIR hattin direk
    #: siralamasina ait degildir: `from_pole_seq` ana hattin numarasi,
    #: `to_pole_seq` kolun numarasidir. Ikisini tek bir aralik gibi okumak
    #: ("Direk #2 — Direk #1") anlamsizdir ve aralik zannedip icini tarayan
    #: kod yanlis sonuc uretir.
    #:
    #: Bu bayrak acikken arayuz araligi "ANA HAT #2 <-> KOL-B #1" seklinde,
    #: iki UCLU bir baglanti olarak gosterir.
    is_link_span: bool = False
    #: Araligin BASLANGIC diregi hangi hatta — `is_link_span` iken ana hat.
    from_pole_line_name: str | None = None

    # ---- ANALIZ ALANLARI ----
    #: Insanin girdigi sebep (katalogdan). NULL = henuz doldurulmadi.
    cause_code: str | None = None
    cause_detail: str | None = None
    #: Kuralin CIHAZ VERISINDEN turettigi oneri. Insan etiketinden AYRI
    #: durur; arayuz bunu "onerilen" olarak gosterir, kilitlemez.
    auto_cause_code: str | None = None
    fault_kind: str | None = None
    phase: str | None = None
    fault_direction: str | None = None
    #: Ariza aninda aktif olan sinyaller (kaynak oneki dahil) — "cihaz ne
    #: gordu" sorusunun ham cevabi.
    trigger_signals: list[str] | None = None
    fault_current_a: float | None = None
    load_current_before_a: float | None = None
    conductor_temp_c: float | None = None
    momentary_fault_count: int | None = None
    permanent_fault_count: int | None = None
    measured_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class FaultEventNoteUpdate(BaseModel):
    note: str | None = None


class FaultEventAssignUpdate(BaseModel):
    """Atama: KISI ya da EKIP. Ikisi birden gonderilirse uc 400 doner.

    Ikisi de bos (None) = atamayi KALDIR. Alanlardan yalnizca birini
    gondermek digerini "degistirme" anlamina GELMEZ; atama tek bir sorunun
    cevabidir ("sorumlu kim") ve her istek o cevabin tamamini yazar.
    """

    assigned_to_username: str | None = None
    assigned_to_area_id: int | None = None


class FaultEventStatusUpdate(BaseModel):
    status: str  # "in_progress" | "resolved" | "closed" | ...
    #: Yalnizca `closed`a gecerken ZORUNLU: arizanin nasil giderildigi.
    #: Kapanis gerekcesi olmayan bir ariza kaydi, gecmise bakildiginda
    #: "neden kapandi" sorusunu cevaplamaz.
    resolution_note: str | None = None


class FaultCauseUpdate(BaseModel):
    """Saha ekibinin girdigi ariza sebebi.

    AYRI BIR UC: sebep, durum degisiminden BAGIMSIZ girilebilmeli. Ekip
    arizayi kapatirken sebebi bilmeyebilir (kablo altyapisi sonradan
    kazilir) ya da kapattiktan sonra ogrenebilir. Sebebi `status` ucuna
    baglamak, "kapatildi ama sebep girilemedi" ya da tersi "sebep girmek
    icin durumu degistir" gibi yapay kisitlar dogururdu.

    `cause_code` NULL gonderilebilir — girilmis bir sebebi GERI ALMAK
    (yanlis secildiyse) mumkun olmali.
    """

    cause_code: str | None = None
    cause_detail: str | None = None
    #: Elle duzeltme: cihaz verisinden turetilen tur/faz yanlissa insan
    #: ezebilmeli. Gonderilmezse mevcut deger KORUNUR (None "sil" demek
    #: degil, "dokunma" demek) — bu yuzden ayri bir bayrakla ayrisir.
    fault_kind: str | None = None
    phase: str | None = None


class FaultCommentCreate(BaseModel):
    body: str


class FaultCommentRead(BaseModel):
    id: int
    fault_id: int
    author_username: str
    body: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
