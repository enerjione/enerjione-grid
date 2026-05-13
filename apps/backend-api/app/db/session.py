from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

# Connection pool ayarlari config'ten okunur. 600 cihaz, ~10K msg/sn pik
# senaryosunda concurrent query sayisi 50-80 olabilir; pool_size=30 +
# max_overflow=20 = 50 stable + spike buffer.
#
# pool_pre_ping: her connection kullanilmadan once ping atip stale baglantilari
# atip yenisini alir (PostgreSQL restart, network hiccup koruma).
#
# pool_recycle: connection'lar bu kadar saniye sonra yeniden kurulur — bu
# postgres tarafindaki idle_in_transaction_session_timeout veya MySQL'deki
# wait_timeout altinda kalmali ki recycled connection patlamasin.
#
# pool_timeout: pool dolu iken yeni connection istegi bu kadar saniye bekler;
# sonra TimeoutError. 30s yuksek yuk altinda backend'in cok beklemesini onler.
engine = create_engine(
    settings.database_url,
    future=True,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle_sec,
    pool_timeout=settings.db_pool_timeout_sec,
    # application_name ile Postgres pg_stat_activity'de bu connection'i 'e1_backend'
    # olarak tani. Restore sirasinda backup_service'in terminate loop'u
    # `application_name NOT LIKE 'e1_%'` filtre ile bizim baglantilarimizi
    # korur, sadece worker servislerini (tag-engine vs.) kill eder.
    connect_args={"application_name": "e1_backend"},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
