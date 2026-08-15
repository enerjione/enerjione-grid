"""device_commands teslim kirasi + dayanikli kabul (F3C)

KAPATILAN ARIZA
Backend `/pending` yanitini uretirken komutu `sent` isaretliyordu. Iki pencere
acikti ve ikisi de SESSIZ komut kaybina cikiyordu:

  A) backend `sent` COMMIT etti, HTTP yaniti gateway'e ULASMADI (ag/timeout)
  B) gateway yaniti bellege aldi, dayanikli deftere yazmadan oldu

Her ikisinde de: backend `sent`, gateway defteri bos, cihaz komutu hic almadi,
komut sonsuza kadar `sent` kaliyor. Operator "gonderildi" goruyor; kesici
surulmemis.

CIFT CALISTIRMA RISKI YOK
Bu kolonlar teslimi yeniden denenebilir kilar; FIZIKSEL CALISTIRMAYI DEGIL.
Gateway defteri (`command_id` PRIMARY KEY + INSERT OR IGNORE) ayni komutun
ikinci kez CROB uretmesini zaten imkansiz kiliyor. Yeniden sunulan bir komut
gateway'de yalnizca ACK/sonuc kurtarmasi tetikler.

  Backend -> Gateway TESLIM     : yeniden denenebilir
  Gateway -> Cihaz   CALISTIRMA : ASLA otomatik yeniden denenmez
  Gateway -> Backend SONUC      : yeniden denenebilir

KOLONLAR
  delivery_token          teslim kimligi; ILK kiralamada uretilir, ACK'e kadar
                          DEGISMEZ (kira yenilense de dondurulmez)
  delivery_lease_until    bu ana kadar komut baska bir poll'a sunulmaz
  delivery_attempt        kac kez teslim denendi; ayni zamanda "hic kiralandi
                          mi" sorusunu cevaplar
  delivery_gateway_epoch  ilk kiralama anindaki gateway DEFTER KIMLIGI; defter
                          sifirlanirsa (tekrar-onleme kaniti kaybolur)
                          otomatik teslim durdurulur

MEVCUT SATIRLAR
Hepsi NULL / 0 ile baslar; bu tam olarak "hic kiralanmadi" demektir ve eski
davranisla ayni sonucu verir. Veri donusumu YOK, geri doldurma YOK.

`delivery_attempt` NOT NULL + server_default='0': NULL birakmak her okuma
noktasinda `COALESCE` gerektirirdi ve bir yerde unutulursa karsilastirma
sessizce False donerdi.

Protokol: docs/f3c-command-delivery-protocol.md

Revision ID: 0069
Revises: 0068
"""

from alembic import op
import sqlalchemy as sa

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


#: Eklenen kolonlar — upgrade/downgrade tek listeden yurur ki ikisi
#: birbirinden ayrisamasin.
_KOLONLAR = (
    ("delivery_token", sa.String(length=64), True, None),
    ("delivery_lease_until", sa.DateTime(timezone=True), True, None),
    ("delivery_attempt", sa.Integer(), False, "0"),
    ("delivery_gateway_epoch", sa.String(length=64), True, None),
)


def upgrade() -> None:
    # VARLIK KONTROLU — savunma derinligi.
    #
    # Temiz kurulumda sema `create_all` + `stamp head` ile olusuyor ve
    # migration'lar HIC kosmuyor; kolonlar model uzerinden zaten yaratilmis
    # oluyor. Ayrica eski bir yedegin (alembic_version < 0069) yeni bir
    # kuruluma yuklenmesi ayni durumu uretir. Kosulsuz `add_column` bu iki
    # yolda "DuplicateColumn" ile duserdi; migration konteyner CMD'sinde
    # uvicorn'dan ONCE kostugu icin sonuc KALICI CRASH-LOOP olurdu.
    mevcut = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("device_commands")}
    for ad, tip, nullable, varsayilan in _KOLONLAR:
        if ad in mevcut:
            continue
        op.add_column(
            "device_commands",
            sa.Column(ad, tip, nullable=nullable, server_default=varsayilan),
        )

    # Kira suresi dolmus / hic kiralanmamis komutlari bulan sorgu her komut
    # poll'unda (gateway basina 1 Hz) kosuyor.
    #
    # VARLIK KONTROLU: indeks modelde de tanimli (`__table_args__`), yani temiz
    # kurulumda `create_all` onu ZATEN yaratir. Kosulsuz `create_index` o yolda
    # "DuplicateTable" ile duserdi.
    mufettis = sa.inspect(op.get_bind())
    indeksler = {i["name"] for i in mufettis.get_indexes("device_commands")}
    if "ix_device_commands_delivery_lease" not in indeksler:
        op.create_index(
            "ix_device_commands_delivery_lease",
            "device_commands",
            ["gateway_code", "status", "delivery_lease_until"],
        )


def downgrade() -> None:
    mufettis = sa.inspect(op.get_bind())
    indeksler = {i["name"] for i in mufettis.get_indexes("device_commands")}
    if "ix_device_commands_delivery_lease" in indeksler:
        op.drop_index("ix_device_commands_delivery_lease", table_name="device_commands")
    mevcut = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("device_commands")}
    for ad, _tip, _nullable, _varsayilan in _KOLONLAR:
        if ad in mevcut:
            op.drop_column("device_commands", ad)
