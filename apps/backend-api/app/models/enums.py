from enum import Enum


class UserRole(str, Enum):
    OPERATOR = "operator"
    ENGINEER = "engineer"
    INSTALLER = "installer"
    # ops_manager: hatlar/cihazlar/sinyaller goruntuleyebilir; sadece operator
    # rolunde kullanici olusturabilir/silebilir; ekip yonetimi tam yetki +
    # toplu bildirim gonderme yetkisi. Cihaz/sinyal/konfigurasyon yazma yok.
    OPS_MANAGER = "ops_manager"


class CommunicationStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"
