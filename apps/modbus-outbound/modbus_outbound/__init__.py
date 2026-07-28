"""Modbus TCP outbound servisi.

tag-engine'in NATS JetStream'e yazdigi normalize telemetriyi tuketir ve
backend-api'den aldigi ADRES PLANINA gore bir veya daha fazla Modbus TCP
sunucusundan dis SCADA'ya yayinlar. Salt okunur (FC1/2/3/4).

Iki adresleme modu (hedef bazinda secilir):
  block : tek unit id, cihazlar register bloklarina dagilir (655 cihaz)
  unit  : her cihaz kendi unit id'sinde, ayni offset duzeni (247 cihaz)

Backend-api'den bagimsiz, paralel calisir; SCADA'nin tarama hizi telemetri
akisini etkilemez.
"""

__version__ = "0.1.0"
