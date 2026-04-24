"""IEC 60870-5-104 sunucu ve adresleme yardimcilari.

Bu paket outbound tarafinda IEC 104 dis sistemlere yayin yapar:
  * `encoder`  : APCI (I/S/U format) + ASDU frame encode/decode
  * `server`   : asyncio TCP server (interrogation + spontaneous transmission)
  * `registry` : Sinyal kataloguna gore cihaz x sinyal -> (TypeID, IOA) haritasi

Tasarim: backend-api process'i FastAPI lifespan icinde aktif IEC 104
OutboundTarget'lari icin ayri server'lar calistirir. `outbound_dispatch_service`
bir telemetri event'ini alinca server'in in-memory nokta tablosunu gunceller;
server degisim icin abone olmus client'lara kendiliginden (spontaneous, COT=3)
ASDU gonderir. Interrogation geldiginde (C_IC_NA_1, TypeID 100) tum degerleri
COT=20 (interrogated by station) ile yayinlar.
"""

from app.services.iec104.encoder import (
    APCI_I_FORMAT,
    APCI_S_FORMAT,
    APCI_U_START_ACT,
    APCI_U_START_CONFIRM,
    APCI_U_STOP_ACT,
    APCI_U_STOP_CONFIRM,
    APCI_U_TEST_ACT,
    APCI_U_TEST_CONFIRM,
    COT_ACTIVATION_CON,
    COT_ACTIVATION_TERM,
    COT_INTERROGATION,
    COT_SPONTANEOUS,
    TYPE_C_CS_NA_1,
    TYPE_C_IC_NA_1,
    TYPE_M_IT_NA_1,
    TYPE_M_ME_NC_1,
    TYPE_M_SP_NA_1,
    build_i_frame_asdu,
    build_s_frame,
    build_u_frame,
    encode_asdu_counter,
    encode_asdu_float,
    encode_asdu_single_point,
    encode_interrogation_confirm,
    parse_apci,
)

__all__ = [
    "APCI_I_FORMAT",
    "APCI_S_FORMAT",
    "APCI_U_START_ACT",
    "APCI_U_START_CONFIRM",
    "APCI_U_STOP_ACT",
    "APCI_U_STOP_CONFIRM",
    "APCI_U_TEST_ACT",
    "APCI_U_TEST_CONFIRM",
    "COT_ACTIVATION_CON",
    "COT_ACTIVATION_TERM",
    "COT_INTERROGATION",
    "COT_SPONTANEOUS",
    "TYPE_C_CS_NA_1",
    "TYPE_C_IC_NA_1",
    "TYPE_M_IT_NA_1",
    "TYPE_M_ME_NC_1",
    "TYPE_M_SP_NA_1",
    "build_i_frame_asdu",
    "build_s_frame",
    "build_u_frame",
    "encode_asdu_counter",
    "encode_asdu_float",
    "encode_asdu_single_point",
    "encode_interrogation_confirm",
    "parse_apci",
]
