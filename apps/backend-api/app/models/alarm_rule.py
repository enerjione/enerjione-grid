from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AlarmRule(Base):
    """Sinyal bazli alarm template'i.

    Her kural bir `signal_key`'e bagli bir eslestirme kuralidir. Okunan telemetri
    bu kurala uyarsa ve sinyal `supports_alarm=True` ise alarm uretilir. Ayni
    sinyal icin birden fazla kural tanimlanabilir (farkli seviyelerle: warning,
    critical vs.).

    İki mod desteklenir:
      - 'simple'    : signal_key + comparator + threshold (mevcut/legacy davranis)
      - 'composite' : expression_json icindeki tanima gore evaluate edilir
                      (birden fazla sinyalin AND/OR ile birlesimi vb.)
    """

    __tablename__ = "alarm_rules"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # Composite kurallar 'signal_key' alaninda anchor sinyalini tutar — engine
    # bu sinyal her geldiginde kurali yeniden degerlendirir. Tipik olarak
    # composite ifade icinde kullanilan ana sinyallerden biri secilir.
    signal_key: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    level: Mapped[str] = mapped_column(String(20), default="warning")  # info | warning | critical

    # Kural tipi: 'simple' (varsayilan, legacy) veya 'composite' (expression_json).
    # Mevcut kurallar otomatik 'simple' kabul edilir; yeni alan eski client'lari
    # bozmaz.
    rule_kind: Mapped[str] = mapped_column(String(20), default="simple", nullable=False)

    # Composite ifade — JSON formatinda. rule_kind='composite' ise kullanilir.
    # Shema (Faz 1 / AND-OR):
    #   {
    #     "logic": "AND" | "OR",
    #     "terms": [
    #       {
    #         "signal_key": "sat01.current_phase_a",
    #         "device_code": "DEV-12" | "*",   // "*" = anchor cihaz
    #         "comparator": "gt|gte|lt|lte|eq|ne|between|outside|boolean_true|boolean_false",
    #         "threshold": 50.0,
    #         "threshold_high": 80.0           // between/outside icin
    #       },
    #       ...
    #     ]
    #   }
    # Faz 2/3'te ayni alana 'kind': 'agg'/'formula' terimleri eklenir.
    expression_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Karsilastirma tipleri (simple mode):
    # gt, gte, lt, lte, eq, ne = tek esik
    # between = threshold <= x <= threshold_high
    # outside = x < threshold or x > threshold_high
    # boolean_true, boolean_false = binary sinyal icin
    comparator: Mapped[str] = mapped_column(String(20), default="gt")
    threshold: Mapped[float] = mapped_column(Float, default=0.0)
    threshold_high: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Histerezis ve debounce
    hysteresis: Mapped[float] = mapped_column(Float, default=0.0)
    debounce_sec: Mapped[int] = mapped_column(Integer, default=0)

    # Kapsam: default tum cihazlarda aktif; device_code_filter virgulle ayrili
    # cihaz kodlari ile sinirlanabilir (bos = hepsi)
    device_code_filter: Mapped[str | None] = mapped_column(String(500), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Bildirim kanallari — kural bazli secim. Web bildirimi her zaman gider
    # (sistemin baslangictaki davranisi); diger kanallar opsiyonel.
    # Default'lar False: kullanici kuraldan acmadan email/sms/telegram
    # gitmez. Boylece "her alarm spam'lemesin" garantisi.
    notify_email: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notify_sms: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notify_telegram: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
