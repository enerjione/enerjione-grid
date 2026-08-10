"""E-posta sablonlarini HTML dosyasina render eder — tarayicida bak.

Mail tasarimini degistirirken tek geri bildirim yolu "SMTP kur, kendine
gonder, telefondan bak" olmamali. Bu script sablonlari temsili veriyle
render edip bir klasore yazar ve index.html uretir; tarayicida acip
duzeni, koyu temayi (isletim sistemi temasini degistirerek) ve mobil
kirilmayi (pencereyi daraltarak) dogrudan gorursun.

Kullanim (apps/backend-api icinden, venv aktif):

    python scripts/preview_emails.py                # gecici klasore yazar
    python scripts/preview_emails.py --out out/mail # belirtilen klasore
    python scripts/preview_emails.py --open         # bitince tarayicida ac

Not: gercek mailde harita `cid:` ile gomulu ek olarak gelir; tarayicida
gorunmeyecegi icin onizlemede temsili bir statik harita gorseliyle
degistirilir (yalnizca bu script'e ozel).
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

# `python scripts/preview_emails.py` ile de calissin: paket koku sys.path'e.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.email_templates import (  # noqa: E402
    render_alarm_email,
    render_assignment_email,
    render_fault_email,
)

#: Onizlemede `cid:` yerine konan temsili gorsel (gercek mailde inline ek).
_PLACEHOLDER_MAP = (
    "https://staticmap.openstreetmap.de/staticmap.php?center=39.925,32.866"
    "&zoom=14&size=640x320&maptype=mapnik&markers=39.925,32.866,red-pushpin"
)

NOW = datetime(2026, 8, 10, 14, 32, 18, tzinfo=timezone.utc)
PROJECT = "Beydağ OSB Dağıtım"


def _samples() -> list[tuple[str, str, str]]:
    """(dosya adi, konu, html) uclulerinden olusan onizleme kumesi."""
    out: list[tuple[str, str, str]] = []

    # 1. Kritik alarm — olcum + esik + harita + CTA (en dolu hal)
    out.append(
        ("01-alarm-kritik",)
        + render_alarm_email(
            project_title=PROJECT,
            rule_name="Aşırı Akım — Faz L2",
            description=(
                "Faz L2 akımı ayarlanan eşiği 3 ölçüm boyunca üst üste aştı. "
                "Hat üzerindeki yük dağılımı kontrol edilmeli."
            ),
            level="critical",
            device_name="DM-14 Kavşak",
            device_code="SN2-0042871",
            signal_source="sat07",
            line_name="Beydağ Fideri 3",
            region_name="Merkez",
            value=612.4,
            value_string=None,
            threshold=500,
            operator="gt",
            occurred_at=NOW,
            alarm_link="https://grid.example.com/alarms/8812",
            map_cid="fault-map-1",
        )
    )

    # 2. Bilgi seviyesi alarm — olcum YOK, kaynak YOK, link YOK.
    #    Bos bloklarin duzeni bozmadigini burada gor.
    out.append(
        ("02-alarm-bilgi",)
        + render_alarm_email(
            project_title=PROJECT,
            rule_name="Cihaz haberleşmesi yeniden kuruldu",
            description=None,
            level="info",
            device_name=None,
            device_code="SN2-0042871",
            signal_source="master",
            line_name=None,
            region_name=None,
            value=None,
            value_string=None,
            threshold=None,
            operator=None,
            occurred_at=NOW - timedelta(minutes=7),
        )
    )

    # 3. Uyari seviyesi — metinsel deger (value_string yolu)
    out.append(
        ("03-alarm-uyari",)
        + render_alarm_email(
            project_title=None,  # proje adi girilmemis kurulum -> PRODUCT_NAME
            rule_name="Batarya durumu düşük",
            description="Cihaz batarya seviyesi servis eşiğinin altında.",
            level="warning",
            device_name="DM-09 Tepe",
            device_code="SN2-0042113",
            signal_source="sat02",
            line_name="Beydağ Fideri 1",
            region_name="Kuzey",
            value=None,
            value_string="Zayıf",
            threshold=None,
            operator=None,
            occurred_at=NOW - timedelta(hours=2),
            alarm_link="https://grid.example.com/alarms/8790",
        )
    )

    # 4. Ariza atamasi
    out.append(
        ("04-atama-ariza",)
        + render_assignment_email(
            project_title=PROJECT,
            kind="fault",
            recipient_full_name="Mehmet Yılmaz",
            title="Beydağ Fideri 3 — direk #12 ↔ #13 arası arıza",
            description=(
                "Son aktif cihaz DM-14, ilk sağlam cihaz DM-15. Ekip "
                "sevk edildi."
            ),
            level="critical",
            actor_username="f.safak",
            link_path="/faults/311",
        )
    )

    # 5. Alarm atamasi — severity YOK, link YOK
    out.append(
        ("05-atama-alarm",)
        + render_assignment_email(
            project_title=PROJECT,
            kind="alarm",
            recipient_full_name="ayse.demir",
            title="Aşırı Akım — Faz L2",
            description=None,
            level=None,
            actor_username=None,
            link_path=None,
        )
    )

    # 6. Hat arizasi — konum + harita + yol tarifi
    out.append(
        ("06-ariza-konumlu",)
        + render_fault_email(
            project_title=PROJECT,
            line_name="Beydağ Fideri 3",
            line_code="BF-03",
            region_name="Merkez",
            last_red_device_name="DM-14 Kavşak",
            last_red_device_code="SN2-0042871",
            first_green_device_name="DM-15 Sanayi",
            first_green_device_code="SN2-0042872",
            from_pole_seq=12,
            to_pole_seq=13,
            latitude=39.925533,
            longitude=32.866287,
            opened_at=NOW,
            fault_link="https://grid.example.com/faults/311",
            assigned_to="Mehmet Yılmaz",
        )
    )

    # 7. Hat arizasi — konum YOK, gorevli YOK, link YOK
    out.append(
        ("07-ariza-konumsuz",)
        + render_fault_email(
            project_title=PROJECT,
            line_name="Beydağ Fideri 1",
            line_code=None,
            region_name=None,
            last_red_device_name=None,
            last_red_device_code="SN2-0042113",
            first_green_device_name=None,
            first_green_device_code=None,
            from_pole_seq=None,
            to_pole_seq=None,
            latitude=None,
            longitude=None,
            opened_at=NOW - timedelta(minutes=41),
            fault_link=None,
            assigned_to=None,
        )
    )
    return out


def _index_page(items: list[tuple[str, str, int]]) -> str:
    """Onizleme listesi — konu satirini da gosterir (mail listesi gibi)."""
    rows = "\n".join(
        f'<li><a href="{name}.html">{name}</a>'
        f'<span class="s">{subject}</span>'
        f'<span class="k">{size // 1024} KB</span></li>'
        for name, subject, size in items
    )
    return f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8">
<title>E-posta onizleme</title>
<style>
 body {{ font:14px/1.6 -apple-system,'Segoe UI',sans-serif; margin:40px auto; max-width:760px;
        color:#0f172a; background:#f8fafc; }}
 h1 {{ font-size:19px; }}
 p.n {{ color:#64748b; }}
 ul {{ list-style:none; padding:0; }}
 li {{ display:flex; gap:14px; align-items:baseline; padding:11px 14px; background:#fff;
       border:1px solid #e2e8f0; border-radius:10px; margin-bottom:8px; }}
 a {{ font-weight:600; color:#2563eb; text-decoration:none; white-space:nowrap; }}
 .s {{ flex:1; color:#475569; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
 .k {{ color:#94a3b8; font-size:12px; }}
</style></head><body>
<h1>EnerjiOne Grid — e-posta sablonu onizlemesi</h1>
<p class="n">Koyu temayi denemek icin isletim sistemi temasini degistir.
Mobil kirilmayi gormek icin pencereyi 480px altina daralt.
Gercek mailde harita <code>cid:</code> ile gomulu ektir; burada temsili
gorselle degistirildi.</p>
<ul>
{rows}
</ul>
</body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(Path(tempfile.gettempdir()) / "e1-email-preview"),
        help="cikti klasoru (varsayilan: gecici klasor)",
    )
    parser.add_argument(
        "--open", action="store_true", help="bitince index.html'i tarayicida ac"
    )
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[tuple[str, str, int]] = []
    for name, subject, html_body in _samples():
        # `cid:` referansi tarayicida cozulmez — onizleme icin degistir.
        preview_html = re.sub(r'src="cid:[^"]+"', f'src="{_PLACEHOLDER_MAP}"', html_body)
        path = out_dir / f"{name}.html"
        path.write_text(preview_html, encoding="utf-8")
        written.append((name, subject, len(preview_html.encode("utf-8"))))
        print(f"  {name:<20} {len(preview_html) // 1024:>3} KB  {subject}")

    index = out_dir / "index.html"
    index.write_text(_index_page(written), encoding="utf-8")
    print(f"\n{len(written)} sablon yazildi -> {index}")

    if args.open:
        webbrowser.open(index.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
