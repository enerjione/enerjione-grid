"""pytest kok konfigurasyon: `ftp_server` paketini yola ekler.

iec104-outbound'da ayni eksiklik CI'a alinir alinmaz `ModuleNotFoundError`
uretti: testler yerelde kalinti `__pycache__` sayesinde tesadufen
kosuyordu. Bu servis henuz CI'da degil; eklenince ayni tuzaga dusmemesi
icin simdiden konuldu.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
