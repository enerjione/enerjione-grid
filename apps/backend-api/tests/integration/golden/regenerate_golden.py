"""`known_device_equivalence.json` dosyasini BASELINE commit'ten yeniden uretir.

NE YAPAR
--------
Baseline `telemetry_consumer.py` git'ten (`--baseline`, varsayilan e8c2f7e)
cikarilip GECICI bir dosyaya yazilir ve ayri bir modul olarak yuklenir.
Ayni fixture seti hem onunla hem MEVCUT kodla gercek PostgreSQL uzerinde
kosturulur; ciktilar ayni ise golden yazilir, degilse fark raporlanip
SIFIRDAN FARKLI kod ile cikilir.

BASELINE KODU REPO'YA KOPYALANMAZ — yalnizca uretim aninda git'ten
materyalize edilir.

NE ZAMAN CALISTIRILIR
---------------------
Bilinen cihaz yolunun gozlemlenebilir davranisi BILEREK degistirildiginde.
Golden'i "test gecsin diye" yenilemek, testin tum anlamini yok eder:
once degisikligin kasitli oldugundan emin olun.

KULLANIM
--------
    E1_TEST_PG_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:15433/postgres \
        python tests/integration/golden/regenerate_golden.py

`apps/backend-api` dizininden calistirilmali.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))

VARSAYILAN_BASELINE = "e8c2f7e84db6fd3498b9f8858fea21b2aa8c6f18"
HEDEF = Path(__file__).parent / "known_device_equivalence.json"


def _baseline_modulu(revizyon: str):
    kaynak = subprocess.run(
        ["git", "show", f"{revizyon}:apps/backend-api/app/services/telemetry_consumer.py"],
        cwd=BACKEND, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    tmp = Path(tempfile.gettempdir()) / "baseline_telemetry_consumer.py"
    tmp.write_text(kaynak, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("baseline_telemetry_consumer", tmp)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["baseline_telemetry_consumer"] = mod
    spec.loader.exec_module(mod)
    return mod


def _kosu(etiket: str, tuketici) -> dict:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    import app.models  # noqa: F401
    from app.db.base import Base
    from tests.integration import equivalence_harness as H
    from tests.integration import pg_target

    ad = pg_target.yeni_db_adi(f"equiv_{etiket}")
    yon = create_engine(pg_target.pg_url(), isolation_level="AUTOCOMMIT")
    with yon.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{ad}"'))
        c.execute(text(f'CREATE DATABASE "{ad}" TEMPLATE template0'))
    yon.dispose()
    pg_target.kaydet_olusturuldu(ad)

    eng = create_engine(pg_target.url_for(ad))
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=True, expire_on_commit=False)
    try:
        H.setup_schema(Session)
        tuketici.SessionLocal = Session
        sonuc = H.capture(Session, tuketici._persist_batch)
        # SIRA ONEMLI: gidis-donus olcumu DB durumuna duyarli. Test tarafi da
        # ayni sirayi izler (once capture, sonra roundtrips).
        sonuc["roundtrips"] = H.capture_roundtrips(Session, tuketici._persist_batch)
        return sonuc
    finally:
        eng.dispose()
        yon = create_engine(pg_target.pg_url(), isolation_level="AUTOCOMMIT")
        with yon.connect() as c:
            c.execute(text(
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname='{ad}' AND pid <> pg_backend_pid()"
            ))
            c.execute(text(f'DROP DATABASE IF EXISTS "{ad}"'))
        yon.dispose()
        pg_target.unut(ad)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default=VARSAYILAN_BASELINE)
    args = ap.parse_args()

    from tests.integration import pg_target

    if not pg_target.pg_url():
        print(f"HATA: {pg_target.ENV_ADI} tanimli degil")
        return 2

    from app.services import telemetry_consumer as guncel

    base_mod = _baseline_modulu(args.baseline)
    if hasattr(base_mod, "process_valid_telemetry"):
        print("UYARI: baseline'da `process_valid_telemetry` VAR — verilen "
              "revizyon refactor ONCESI degil; karsilastirma anlamsiz olabilir.")

    baseline = _kosu("baseline", base_mod)
    current = _kosu("current", guncel)

    farkli = [k for k in sorted(set(baseline) | set(current))
              if baseline.get(k) != current.get(k)]
    if farkli:
        print("FARK VAR — golden YAZILMADI. Sapan alanlar:", farkli)
        for a in farkli:
            b, c = baseline.get(a), current.get(a)
            if isinstance(b, list) and isinstance(c, list):
                for i, (x, y) in enumerate(zip(b, c)):
                    if x != y:
                        print(f"  [{a}][{i}]\n    baseline={x}\n    current ={y}")
                        break
            else:
                print(f"  [{a}] baseline={b!r} current={c!r}")
        return 1

    HEDEF.write_text(
        json.dumps(baseline, indent=1, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"ESDEGER — {baseline['fixture_count']} fixture, golden yazildi: {HEDEF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
