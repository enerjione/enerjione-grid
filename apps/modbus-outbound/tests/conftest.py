"""pytest kok konfigurasyon: `modbus_outbound` paketini yola ekler.

iec104-outbound'da ayni eksiklik CI'a alinir alinmaz
`ModuleNotFoundError` uretti: testler yerelde kalinti `__pycache__` sayesinde
tesadufen kosuyordu. Bu servis henuz CI'da degil; eklenince ayni tuzaga
dusmemesi icin simdiden konuldu.

NOT: `tests/test_smoke.py` icindeki `test_tcp_end_to_end` bir `async def` ve
bu pakette `pytest-asyncio` YOK — pytest onu calistiramaz, "async def
functions are not natively supported" ile duser. Dosya `python -m
tests.test_smoke` ile dogrudan kosturulabilecek sekilde de yazilmis; CI'a
alinirken ya pytest-asyncio eklenmeli ya da o giris noktasi kullanilmali.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
