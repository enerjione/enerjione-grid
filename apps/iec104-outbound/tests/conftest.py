"""pytest kok konfigurasyon: `iec104_outbound` paketini yola ekler.

Testler daha once YALNIZCA yerelde kosuyordu ve orada tesadufen calisiyordu
(kalinti `__pycache__` / onceki kurulum). CI'a alinir alinmaz
`ModuleNotFoundError: iec104_outbound` ile duser. Bu dosya iki ortamda da
ayni davranisi garanti eder.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
