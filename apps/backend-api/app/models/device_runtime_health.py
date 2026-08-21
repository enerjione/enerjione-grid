from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DeviceRuntimeHealth(Base):
    """Gateway'in bildirdigi CIHAZ BASINA calisma-zamani sagligi.

    Sozlesme: `device_health_v1` — Gateway PR #33 (HENUZ ACIK).
    Vendor kopyasi: `docs/gateway-contract/device-health-api-pr33.md`.

    NEDEN AYRI TABLO — `devices`e kolon EKLENMEDI:
      `devices` satiri cihaz KAYDIDIR (operatorun girdigi kimlik, konum,
      profil) ve nadiren degisir. Buradaki veri gateway'in saniyeler icinde
      degisen gozlemidir; ayni satira koymak, operator verisini tasiyan
      satiri her saglik partisinde yeniden yazmak (Postgres MVCC) ve iki
      farkli SAHIPLIGI tek satirda toplamak demekti. `gateway_health` ve
      `gateway_updates` ayni gerekceyle ayrilmisti.

    NEDEN `telemetry_latest` DEGIL:
      O tablo SINYAL degerlerinin sahibidir (tag-engine yazar). Bu kanal
      DNP3 sinyali tasimaz; oturum/erisilebilirlik gozlemidir. Ayrica
      `devices.communication_status` telemetri hattindan turetilir ve BU
      KANAL ONU EZMEZ: `smart_idle` saglikli bir uyku halidir, telemetri
      hattinin "haberlesme yok" karariyla ayni kovaya konursa uyuyan filo
      SCADA'da arizali gorunur (sozlesme bolum 5).

    Cihaz basina TEK satir, upsert edilir: "bu cihaz SU AN ne durumda"
    sorusunun cevabi budur. Gecis gecmisi TUTULMAZ — gateway de tutmaz
    (parti icinde cihaz basina yalnizca son durum birlestirilir).

    FK YOK — bilincli. Gateway backend'in henuz silmedigi/hic gormedigi bir
    cihaz kodu bildirebilir; FK bu partinin TAMAMINI dusururdu. Ortada
    kalan satir kalici degildir: cihaz gateway config'inden cikinca sonraki
    tam snapshot'ta bulunmaz ve uzlastirma onu siler.
    """

    __tablename__ = "device_runtime_health"

    #: Cihaz kodu birincil anahtar: cihaz basina TEK satir.
    device_code: Mapped[str] = mapped_column(String(50), primary_key=True)

    #: Bu gozlemi hangi gateway bildirdi. Uzlastirma (silinen cihaz tespiti)
    #: HER ZAMAN gateway basina yapilir; kodu satirda tutmadan "bu gateway'in
    #: cihazlari" kumesi cikarilamaz. Cihaz baska bir gateway'e tasinirsa
    #: upsert bu alani gunceller ve eski gateway'in uzlastirmasi satiri
    #: ARTIK GORMEZ — yani tasinan cihaz yanlislikla silinmez.
    gateway_code: Mapped[str] = mapped_column(String(50), index=True)

    # ---- sozlesme bolum 4: cihaz kaydi -----------------------------------
    #: online | smart_idle | recovering | lost | listener_error | unknown
    #:
    #: BAGLANTI KARARININ TEK KAYNAGI BUDUR. Sonda alanlari (asagida) salt
    #: teshistir. Deger sozlesmedeki kumeye ZORLANMAZ: PR acik ve yeni bir
    #: durum eklenebilir; bilinmeyeni `unknown`a cevirmek yeni bir durumu
    #: sessizce yutmak olurdu (ileri uyumluluk).
    #:
    #: `server_default` 0074 ile AYNI olmak ZORUNDA: kolon NOT NULL ve
    #: varsayilan yalnizca Python tarafinda kalirsa, ORM disindan gelen bir
    #: INSERT (restore, elle SQL) bir kurulumda patlar digerinde calisir.
    connection_state: Mapped[str] = mapped_column(
        String(24), default="unknown", server_default="unknown"
    )
    #: TCP link acik mi.
    connected: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    #: Komut gonderilebilir mi. Uyuyan (Smart) cihaz icin `False` — bu
    #: SAGLIKLI bir durumdur, ariza degil.
    reachable: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )

    #: continuous | smart | auto (operatorun yapilandirdigi)
    configured_session_policy: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: continuous | smart | unknown (`auto` henuz cozulmediyse `unknown`)
    effective_session_policy: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: smart | boost | unknown. Satellite mod ve "Boost Mode Enabled"
    #: YETENEGI bu kanalda GELMEZ (sozlesme bolum 5).
    operation_mode: Mapped[str | None] = mapped_column(String(24), nullable=True)

    #: Beklenen rapor araligi (dakika). None = tanimsiz.
    dial_in_interval_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Unix epoch (saniye, UTC). None = "HIC OLMADI" — gateway 0 GONDERMEZ,
    #: biz de 0'a cevirmeyiz (panelde 1970 tarihleri cikmasin diye).
    next_expected_report_epoch: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Gecikme (saniye); 0.0 = gecikme yok.
    report_overdue_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: UYARI BAYRAGI — DURUM DEGIL. `report_late=True` iken
    #: `connection_state` HALA `smart_idle` olabilir ve genelde oyledir.
    #: `lost` ile ayni kovaya konursa gunluk sahte alarm uretir.
    report_late: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )

    #: Son GECERLI DNP3 kaniti / son frame. None = hic olmadi.
    last_valid_contact_epoch: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_frame_epoch: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ----- Gateway 1.15.1: CIHAZ RTC SAGLIGI + OTURUM KANITI --------------
    #
    # HEPSI OPSIYONEL VE NULLABLE. 1.15.0 gateway'i bu alanlari GONDERMEZ;
    # o zaman `None` kalirlar ve hicbir karar onlara "varmis gibi" davranmaz.
    #
    #: `unknown` | `ok` | `invalid` | `need_time` (sozlesme 1.15.1 bolum 5).
    #:
    #: BAGLANTI DURUMUNU ETKILEMEZ. Sahada bir Horstmann'in RTC'si 2066
    #: yilina kaymisti; cihaz `online` idi, olcum gonderiyordu ve komut
    #: kabul ediyordu. Etkilenen tek sey CIHAZIN KENDI OLAY DAMGASINA
    #: duyulan guvendir. `invalid` gorup cihazi kopuk saymak saglikli
    #: filoyu arizali gosterir.
    device_clock_status: Mapped[str | None] = mapped_column(String(24), nullable=True)

    #: `cihaz_saati - gateway_saati` (saniye, ISARETLI).
    #: Pozitif = cihaz ileri, negatif = geri. `0.0` GECERLI bir degerdir
    #: (tam senkron) ve `None` ile karistirilmamalidir.
    device_clock_offset_sec: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: Cihazin KENDI bildirdigi son zaman damgasi (unix epoch, saniye).
    last_device_time_epoch: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: IIN1.4 (NEED_TIME) bayragi. UC DURUMLU:
    #:   True  = cihaz saat istiyor
    #:   False = istemiyor
    #:   None  = HIC IIN GORULMEDI (ya 1.15.0 gateway ya da hic yanit yok)
    #: `False`a cevrilmemeli: "istemiyor" ile "bilmiyoruz" ayni sey degil.
    #: Saat yanlis AMA cihaz saat istemiyorsa durum KENDILIGINDEN DUZELMEZ —
    #: sahada gorulen tam olarak buydu, o yuzden ayrim onemli.
    need_time_iin: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    #: Acik DNP3 oturumunun basladigi an (unix epoch, saniye).
    #: OTURUM KAPALIYKEN `None` — `smart_idle` bir cihazda normal olarak
    #: `None` gorulur, bu bir hata DEGILDIR.
    #:
    #: GOZLEM SINIFI: bu alanin degismesi delta TETIKLEMEZ (sozlesme 1.15.1
    #: bolum 6). Guncel degeri her zaman periyodik snapshot'ta bulunur.
    #: "Degismedi -> oturum yok" gibi bir cikarim YAPILMAMALIDIR.
    session_started_epoch: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: SALT TESHIS — durum belirlemez. `ip_probe_status="unreachable"`
    #: gormek NORMALDIR: ICMP saha aglarinda/APN'lerde sikca engellidir ve
    #: Smart bir modem mesru olarak uykudadir.
    ip_probe_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: open | connecting | unknown
    tcp_probe_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    last_probe_epoch: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: listening | initiating
    ip_endpoint_type: Mapped[str | None] = mapped_column(String(24), nullable=True)

    # ---- sozlesme bolum 6: siralama ve snapshot ---------------------------
    #: Gateway'in KALICI kimligi (restart'ta DEGISMEZ). Yalnizca kayit/teshis
    #: icin saklanir; SIRALAMAYA GIRMEZ — girseydi restart sonrasi
    #: `sequence=1` partisi "eski" sanilip atilirdi.
    gateway_instance_id: Mapped[str | None] = mapped_column(String(80), nullable=True)

    #: Bayat yazma korumasinin ikilisi. Karsilastirma LEKSIKOGRAFIKTIR:
    #: eski calismanin `sequence=9999`u yeni calismanin `sequence=1`inden
    #: KUCUKTUR, cunku `boot_id` her acilista artar. DUVAR SAATI KULLANILMAZ
    #: (RTC'si bos acilan gateway ve NTP sicramalari gercek).
    boot_id: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sequence: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    #: Bu satiri en son yazan TAM snapshot'in kimligi (`{boot_id}-{sayac}`)
    #: ve o snapshot icindeki parti sirasi. Uzlastirma bunlarla yapilir:
    #: snapshot'in TUM partileri gelince, ayni `snapshot_id`yi tasimayan
    #: satirlar gateway config'inden CIKMIS demektir.
    #:
    #: `device_total` BILEREK SAKLANMIYOR: yarim kalan eski snapshot ile
    #: yenisi ayni toplami tasir; ona guvenen "eksikleri sil" mantigi VAR
    #: OLAN CIHAZLARI SILER (sozlesme bolum 6).
    #:
    #: DELTA BU IKI ALANA DOKUNMAZ. Dokunsaydi, snapshot devam ederken gelen
    #: bir delta cihazin snapshot damgasini silecek ve snapshot tamamlandigi
    #: anda o cihaz "snapshot'ta yok" sanilip SILINECEKTI.
    snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_batch_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: BACKEND saati — "ne zaman haber aldik". Gateway saatine GUVENILMEZ
    #: (ayni gerekce `gateway_health.reported_at`ta da yazili).
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Bayat yazma korumasi her istekte "bu gateway'in en yuksek
        # (boot_id, sequence) ikilisi" sorusunu sorar. Kolon sirasi ONEMLI:
        # sorgu `WHERE gateway_code=? ORDER BY boot_id DESC, sequence DESC
        # LIMIT 1` — bu index onu tam olarak tek satir okumaya indirir.
        Index(
            "ix_device_runtime_health_kursor",
            "gateway_code",
            "boot_id",
            "sequence",
        ),
        # Uzlastirma: "bu gateway'de su snapshot'i tasimayan satirlar".
        Index(
            "ix_device_runtime_health_snapshot",
            "gateway_code",
            "snapshot_id",
        ),
    )
