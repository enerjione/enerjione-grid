"""pytest kok konfigurasyon: `src` yolunu hazir hale getirir.

backend-api paketinin kendi icinde `app/` altinda yasadigi icin testler
`apps/backend-api` dizininden kosturulmali. Bu conftest `app` paketinin
bulunabilmesi icin `sys.path`'i ayarlar.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
