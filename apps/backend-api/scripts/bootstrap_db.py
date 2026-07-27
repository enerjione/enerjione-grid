"""Yerel gelistirme icin DB olusturucu (sadece Windows/local dev).

DB adini ARTIK hard-code ETMEZ; `DATABASE_URL` (yoksa `app.core.config`
default'u) icinden okur. Boylece .env'de POSTGRES_DB degistiginde bu script
yanlis isimde bir DB acmaz.

Kullanim:
    cd apps/backend-api
    python scripts/bootstrap_db.py

Production'da KULLANILMAZ — Docker'da postgres imaji DB'yi initdb sirasinda
kendisi olusturur (bkz. docker-compose.yml POSTGRES_DB).
"""

import os
import sys
from urllib.parse import unquote, urlparse

import psycopg2
from psycopg2 import sql

DEFAULT_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/enerjione_grid"


def _parse_url(url: str) -> dict:
    """SQLAlchemy URL -> psycopg2 baglanti parcalari."""
    # `postgresql+psycopg2` seman: urlparse '+psycopg2' kismini anlamaz, at.
    if "://" in url and "+" in url.split("://", 1)[0]:
        proto, rest = url.split("://", 1)
        url = proto.split("+", 1)[0] + "://" + rest
    u = urlparse(url)
    return {
        "host": u.hostname or "localhost",
        "port": u.port or 5432,
        "user": unquote(u.username or "postgres"),
        "password": unquote(u.password or ""),
        "dbname": (u.path or "/").lstrip("/") or "enerjione_grid",
    }


def try_bootstrap() -> int:
    cfg = _parse_url(os.getenv("DATABASE_URL") or DEFAULT_URL)
    target_db = cfg["dbname"]

    # URL'deki parola once denenir; yerel kurulumlarda sik kullanilan
    # varsayilanlar fallback olarak kalir.
    passwords = [cfg["password"], "postgres", "1234", "admin", ""]
    seen: set[str] = set()
    for password in passwords:
        if password in seen:
            continue
        seen.add(password)
        try:
            conn = psycopg2.connect(
                host=cfg["host"],
                port=cfg["port"],
                dbname="postgres",  # sistem DB'si — hedef DB henuz olmayabilir
                user=cfg["user"],
                password=password,
            )
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (target_db,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db)))
                print(f"Database created: {target_db}")
            else:
                print(f"Database already exists: {target_db}")
            cur.close()
            conn.close()
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"Attempt failed for user '{cfg['user']}': {str(exc).splitlines()[0]}")

    print(
        f"'{cfg['user']}' ile baglanilamadi. DATABASE_URL'i dogru parola ile "
        "set edip tekrar deneyin."
    )
    return 2


if __name__ == "__main__":
    sys.exit(try_bootstrap())
