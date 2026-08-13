"""Sinyal adlari — EKRANDA ne yaziyorsa raporda da o yazsin.

Sinyal ANAHTARLARI sabittir (`master.fault_current`, `sat01.voltage_loss`)
ama katalogtaki `label` alani INGILIZCE girilmis durumda: arayuz onu
`tr.json > signals.<sonek>` sozlugu ile cevirip gosteriyor (bkz. frontend
`shared/signalLabel.ts`). Sunucuda uretilen rapor ayni cevirivi yapmazsa
kullanici ekranda "Asiri Akim Acmasi" gorup raporda "Overcurrent Tripped"
indirir — ve sahada "hangisi dogru" sorusu cikar.

Bu modul o dosyanin Python karsiligidir. Sozluk
`app/data/signal_labels_tr.json` dosyasindan okunur; dosya frontend
`tr.json > signals` blogunun AYNASIDIR ve
`tests/test_cihaz_raporu_pdf.py` ikisinin ayrismasini engeller — ayni
yontem olay etiketlerinde de kullaniliyor (bkz. `event_labels.py`).

CEVIRI SONEK UZERINDEN yapilir: `master.fault_current` ile
`sat07.fault_current` ayni satiri paylasir, yani yeni bir cihaz kaynagi
(uydu, unite) sozluge dokunmadan kapsanir.

SOZLUKTE OLMAYAN SINYAL KIRILMAZ: katalog adina, o da yoksa sonekin
kendisine dusulur. Sonradan eklenen ozel bir sinyal, cevirisi gelene kadar
Ingilizce gorunur — raporun o satiri hic gostermemesinden iyidir.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_LABELS_PATH = Path(__file__).resolve().parent.parent / "data" / "signal_labels_tr.json"


@lru_cache(maxsize=1)
def _labels() -> dict[str, str]:
    try:
        with _LABELS_PATH.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        # Sozluk okunamazsa rapor yine cikmali: katalog adlarina duseriz.
        return {}
    return data if isinstance(data, dict) else {}


def signal_suffix(signal_key: str) -> str:
    """`sat01.fault_current` -> `fault_current`. Nokta yoksa anahtarin kendisi."""
    index = signal_key.find(".")
    return signal_key[index + 1 :] if index >= 0 else signal_key


def signal_source(signal_key: str) -> str:
    """`sat01.fault_current` -> `sat01`. Nokta yoksa bos."""
    index = signal_key.find(".")
    return signal_key[:index] if index >= 0 else ""


def signal_label(signal_key: str, fallback: str | None = None) -> str:
    """Sinyalin Turkce adi; sozlukte yoksa katalog adi, o da yoksa sonek."""
    suffix = signal_suffix(signal_key)
    return _labels().get(suffix) or (fallback or suffix)


def source_label(source: str) -> str:
    """Unite (kaynak) adi — arayuzdeki `sourceLabel` ile ayni metin.

    `master` cihaz turune gore farkli sey ifade eder (SN 2.0'da olcum yapan
    ana unite, Pole Master Kit'te ortak RTU); bu ayrimi rapor BOLUM BASLIGI
    ile yapar, etiketin kendisi sabit kalir — ekranda da oyle.
    """
    if source == "master":
        return "Master"
    if source.startswith("sat") and source[3:].isdigit():
        return f"Satellite {int(source[3:]):02d}"
    return source or "—"


__all__ = ["signal_label", "signal_source", "signal_suffix", "source_label"]
