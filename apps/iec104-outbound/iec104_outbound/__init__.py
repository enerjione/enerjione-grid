"""IEC 60870-5-104 outbound servisi.

Tag-engine'in `telemetry.received` event'lerini RabbitMQ'dan tuketir;
backend-api'den okudugu IEC104 adresleme acik sinyalleri bir veya daha fazla
TCP IEC 104 slave portundan dis SCADA'ya yayinlar. Backend-api'dan bagimsiz,
paralel calisir.
"""

__version__ = "0.1.0"
