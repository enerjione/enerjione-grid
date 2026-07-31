"""pytest kok konfigurasyon: `alarm_service` paketini yola ekler.

Bu serviste daha once HIC test yoktu; CI yalnizca backend-api'yi kosuyordu.
Denetim raporundaki A5 (bellek tasmasi) tam da testsiz kalmis bir davranisti.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
