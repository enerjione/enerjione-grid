"""Ariza (Fault) lokasyon kaydi.

Mantik:
  Bir AlarmEvent tek bir cihazin "ariza akimi gordum" sinyalidir. Birden
  cok AlarmEvent (ayni hat'ta sirayla yer alan cihazlar) bir ARIZA NOKTASI
  ifade eder. Ariza, son RED cihaz ile ilk GREEN cihaz arasindaki POLE
  aralidir.

  FaultEvent kaydi bu ariza-aralik bilgisini canli olarak saklar:
    - line_id, region_id (kapsam icin)
    - last_red_device_id (son alarm veren)
    - first_green_device_id (sonraki alarm vermeyen — yoksa NULL)
    - from_pole_id, to_pole_id (cihazlarin oturdugu slot ucu)
    - status: "open" (aktif ariza), "resolved" (cozuldu/normale dondu)
    - opened_at, resolved_at

  Bir fault aktif iken (open):
    - alarm-engine reconciliation veya yeni alarm geldikce guncellenir
    - notification dispatcher web+email+sms gonderir (kullanici tercihi
      ve sorumluluk alanina gore)
  Cozuldugunde:
    - status="resolved", resolved_at set
    - tarihce/raporlama icin DB'de kalir

  Frontend "Arizalar" sayfasi:
    - GET /faults?status=open  -> aktif ariza listesi (cizelge)
    - GET /faults?status=all   -> tarih dahil
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FaultEvent(Base):
    __tablename__ = "fault_events"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Topoloji icindeki konum.
    # Hat/region/direk silindiginde ariza kaydi da otomatik silinir
    # (gecmis kayit barindirma yerine kaskat tercih edildi — silinmis hat
    # icin ariza kaydi mantiksiz). Cihaz silinmesinde de cascade.
    line_id: Mapped[int] = mapped_column(
        ForeignKey("lines.id", ondelete="CASCADE"), index=True
    )
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="CASCADE"), index=True
    )

    # Ariza aralik uc noktalari (son RED ile ilk GREEN cihaz arasinda)
    # last_red_device_id NULL olamaz — bu cihaz sayesinde fault tespit edildi.
    last_red_device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # ilk green cihaz: NULL olabilir (hat ucunda alarm: sonraki cihaz yok).
    first_green_device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    # Direk araligi (cihazlarin oturdugu slot uclari)
    from_pole_id: Mapped[int] = mapped_column(
        ForeignKey("poles.id", ondelete="CASCADE"), index=True
    )
    to_pole_id: Mapped[int] = mapped_column(
        ForeignKey("poles.id", ondelete="CASCADE"), index=True
    )
    # Pole sequence_no'larini aciklayici metin/UI icin saklayalim (denormalized).
    from_pole_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_pole_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Tel mesafesi (METRE) --------------------------------------------
    # Direk koordinatlari + cihazin slot icindeki konumu (device_position_t)
    # kullanilarak hat boyunca (kus ucusu DEGIL) hesaplanir; bkz.
    # `line_distance_service`. Ariza her recompute'ta yeniden yazilir, yani
    # topoloji koordinati duzeltilirse bir sonraki turda guncellenir.
    #   zone_start_m : hat basindan son RED cihaza (arizanin en yakin sinirina)
    #   zone_end_m   : hat basindan ilk GREEN cihaza (yoksa hattin ucuna)
    #   zone_length_m: belirsizlik araligi = zone_end_m - zone_start_m
    # Koordinat/topoloji eksikse NULL kalir — UI mesafe blogunu gizler.
    zone_start_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_end_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_length_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    # status:
    #   "open"        - yeni acildi, henuz kimse atanmadi
    #   "assigned"    - bir kullaniciya otomatik veya manuel atandi
    #   "in_progress" - atanan kullanici uzerinde calisiyor
    #   "resolved"    - sahada cozuldu (cihaz alarmi kalkti)
    #   "closed"      - kullanici raporu tamamladi ve kapatti (resolved sonrasi)
    status: Mapped[str] = mapped_column(String(20), index=True, default="open")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Ticket atamasi: arizadan sorumlu kullanici (otomatik atanir;
    # engineer/installer manuel degistirebilir).
    assigned_to_username: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Genel aciklama / sahaya gidis sonrasi rapor (opsiyonel; uzun aciklama
    # icin FaultComment kullanin — bu alan basit ozet).
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: COZUM NOTU — arizayi kapatan kisinin "ne yapildi" cevabi.
    #:
    #: NEDEN `note`DAN AYRI: `note` ariza acikken tutulan serbest calisma
    #: notudur ve degisir. Bu alan ise KAPANIS kaydidir: bir kez yazilir ve
    #: arizanin nasil giderildiginin kalici cevabidir. Ayni alana yazsaydik
    #: kapanis gerekcesi sonraki bir not duzenlemesiyle sessizce silinebilirdi.
    #:
    #: Ariza `closed` durumuna gecerken ZORUNLUDUR (bkz. faults API).
    resolution_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Ariza bildiriminin (e-posta/SMS/WhatsApp) gonderildigi an. NULL =
    # bildirim BEKLIYOR.
    #
    # Neden ayri bir kolon: ariza kaydini `fault_recompute_service` uretir ve
    # o servis ariza motorunun icinde kosar. SMTP/HTTP cagrisini oraya koymak
    # yanit vermeyen bir relay'de arizanin COMMIT EDILMEMESINE yol aciyordu;
    # bu yuzden satir ici dispatch `notification_inline_dispatch_enabled`
    # bayragina baglandi ve bayrak production'da False. Ancak notification-
    # worker yalnizca `alarm.created` tuketip ALARM dispatch'i tetikliyordu —
    # ariza icin hicbir yol yoktu, yani ariza bildirimi production
    # varsayilaninda HIC gonderilmiyordu.
    # Cozum: recompute yalnizca kaydi acar ve bildirimi "bekliyor" birakir;
    # gonderimi worker'in tetikledigi dispatch ucu yapar ve bu alani damgalar.
    # Damga ayni zamanda idempotency saglar (worker retry'inda ikinci mail yok).
    notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # ---------------- ANALIZ KATMANI ----------------
    #
    # NEDEN BU ALANLAR: "hangi hat en cok ariza cikariyor" sorusu bugun de
    # cevaplanabiliyor (line_id + opened_at yeter). Cevaplanamayan soru
    # "NEDEN" — ve bunun tek kaynagi serbest metin `note` ile yorumlardi.
    # Serbest metinden istatistik cikmaz: ayni sebep on farkli cumleyle
    # yazilir. Bu alanlar metnin YERINE degil YANINA gelir; `note` ve
    # FaultComment aynen kalir.
    #
    # Hepsi NULLABLE ve varsayilan YOK: gecmis kayitlar ile saha ekibinin
    # doldurmadigi kayitlar "bilinmiyor" olarak ayrismali. Bos gecilen bir
    # alani "diger" saymak, analiz katmanina uydurma etiket beslemek olurdu.

    #: Yapilandirilmis ariza sebebi. Serbest metin DEGIL, katalogdan secilir
    #: (bkz. app/data/fault_causes.py). Cikarim katmaninin ogrenecegi etiket
    #: budur; bu alan dolmadan model egitilecek bir sey yoktur.
    cause_code: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    #: Sebebe ait serbest ek ("3. direkteki mesnet izolatoru catlak" gibi).
    cause_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: transient (gecici — kendiliginden duzeldi) | permanent (kalici, mudahale
    #: gerekti) | unknown. Sebep dagilimindan BAGIMSIZ bir eksen: ayni sebep
    #: (orn. agac temasi) ruzgarda gecici, dal kirilinca kalici olur.
    #:
    #: ELLE SORULMAZ: cihaz bunu zaten soyluyor (`master.permanent_fault` /
    #: `master.momentary_fault`). Saha ekibine sormak hem gereksiz hem daha
    #: hatali olurdu.
    fault_kind: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    #: Etkilenen faz: a | b | c | ab | ac | bc | abc | unknown.
    phase: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # --- CIHAZIN ALARM IMZASI ---
    #
    # SEBEP CIKARIMININ ASIL KAYNAGI BURASI. Saha ekibinin yazdigi metin
    # gecikmeli, eksik ve ozneldir; cihazin ariza aninda hangi bayraklari
    # kaldirdigi ise olculmus veridir ve sebebi buyuk olcude belirler:
    #
    #   current_loss (asiri akim YOK)          -> iletken kopmasi
    #   conductor_temperature_alarm + yuksek I -> asiri yuk
    #   tamper_detection                       -> ucuncu sahis
    #   delta_i_delta_t_tripped (asiri akim YOK)-> yuksek empedansli ariza
    #                                             (tipik olarak agac temasi)
    #   overcurrent_tripped + yuksek fault_current -> kisa devre
    #
    #: Ariza aninda AKTIF olan sinyal anahtarlari (JSON dizisi).
    #: DENORMALIZE: cihaz silinse ya da alarm kaydi dusse bile imza kalir.
    trigger_signals: Mapped[list | None] = mapped_column(JSON, nullable=True)

    #: Yukaridaki imzadan KURAL ILE turetilen sebep onerisi. `cause_code`
    #: (insanin girdigi) ile AYRI tutulur — ikisini karsilastirmak kurallarin
    #: ne kadar isabetli oldugunu olcer. Bir cikarim katmani eklemeden once
    #: bilmen gereken sey tam olarak budur; ustelik LLM'siz de calisir.
    auto_cause_code: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )

    #: Ariza yonu: green_a | red_b | unknown (FCI yon bayraklarindan).
    fault_direction: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # --- Olcum anlik goruntusu ---
    #
    # NEDEN KOPYALANIYOR: ham telemetri 90 gunde dusuyor (saatlik ozet 2 yil
    # kaliyor ama ondan BELIRLI bir arizanin tepe akimini geri cikarmak
    # kayipli — kova ortalamasi/tepe degeri arizaya ait olmayabilir).
    # Ariza analizi yillar boyunca anlamli olmali; bu yuzden arizayi tanimlayan
    # birkac sayi kaydin KENDISINE yazilir. Bu bilincli bir denormalizasyondur.
    #: Ariza aninda olculen ariza akimi (A) — `master.fault_current`.
    fault_current_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Ariza oncesi normal yuk akimi (A) — "asiri yuk muydu" sorusu icin.
    load_current_before_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Iletken sicakligi (°C) — asiri yuk / termal ariza ayrimi.
    conductor_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Cihazin ANLIK ariza sayaclari. Ardisik iki ariza arasindaki FARK, o
    #: olayda kac kez tekrar kapama denendigini verir — "bu aciklikta surekli
    #: gecici ariza var" bulgusunun olculebilir hali.
    momentary_fault_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    permanent_fault_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Yukaridaki olcumlerin alindigi an.
    measured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # Sıklık analizi: "bu hatta/bolgede son 12 ayda kac ariza".
        Index("ix_fault_line_opened", "line_id", "opened_at"),
        Index("ix_fault_region_opened", "region_id", "opened_at"),
        # TEKRARLAYAN ACIKLIK: "ayni iki direk arasi tekrar tekrar ariza
        # yapiyor". Direk ID'leri kalicidir (sequence_no yeniden
        # siralanabilir, ID siralanmaz) — bu yuzden anahtar ID ciftidir.
        Index("ix_fault_span", "from_pole_id", "to_pole_id"),
        # Sebep dagilimi.
        Index("ix_fault_cause_opened", "cause_code", "opened_at"),
    )


class FaultComment(Base):
    """Ariza ticket'ina baglı yorum/rapor satirlari.

    Atanan kullanici sahaya gittiginde ne yaptigini (gozlem, bakim islemi,
    onarim adimlari, parca degisimi vb) buraya ekler. Bir fault icin coklu
    yorum birikir (zaman zaman raporlar)."""

    __tablename__ = "fault_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    fault_id: Mapped[int] = mapped_column(
        ForeignKey("fault_events.id", ondelete="CASCADE"), index=True
    )
    author_username: Mapped[str] = mapped_column(String(120), index=True)
    body: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
