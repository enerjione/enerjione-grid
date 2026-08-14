"""Backup restore ilerleme takibi — in-memory durum dict'i.

Restore endpoint asenkron tetiklenir; arka plan thread'de calisirken bu
modul adim adim ilerlemeyi gunceller. Frontend `GET /admin/backups/restore/status`
ile son durumu cekip modal'da kullaniciya gosterir.

Tek bir restore ayni anda calisir varsayimi — restore zaten "uzun ve riskli"
bir islem, paralel yapilmaz. Bu yuzden global tek state yeterli; multi-job
queue'ya gerek yok.

Adimlar:
  1. queued        — istek alindi, henuz baslamadi
  2. validating    — yedek dosyasi format + RCE pattern kontrolu
  3. preparing     — eski role'leri ensure et (legacy compat)
  4. restoring     — pg_restore calisiyor (en uzun adim)
  5. finalizing    — connection pool dispose + DB event log
  6. done / failed — bitti

State'ler thread-safe Lock ile korunur.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# Adim listesi — UI'da iste bu sirada gosterilir.
#
# GUVENLI RESTORE ILE GENISLEDI. Eski akis dogrudan uretime yaziyordu ve
# dort adimi vardi; yeni akis once bos bir staging veritabanina yukleyip
# dogruluyor, uretime yalnizca `cutover` adiminda dokunuyor
# (bkz. services/safe_restore.py).
#
# GERIYE UYUMLULUK: eski adim adlari (`validating`, `preparing`,
# `restoring`, `finalizing`) LISTEDE KALIYOR. Frontend adimlari bu listeden
# okuyup ilerleme cubugu ciziyor; kaldirsaydik eski bir arayuz surumu
# tanimadigi adimda ilerlemeyi kaybederdi. `set_step` taninmayan adimi
# zaten sessizce yok sayar.
STEPS = (
    "queued",
    "preflight",
    "validating_archive",
    "creating_staging",
    "restoring",
    "migrating",
    "validating_staging",
    "preparing_cutover",
    "cutover",
    "post_validation",
    "done",
    # --- eski adlar (geriye uyumluluk; yeni akis kullanmaz) ---
    "validating",
    "preparing",
    "finalizing",
)

#: Ilerleme yuzdesi hesabinda kullanilan GERCEK akis. `STEPS` geriye
#: uyumluluk icin eski adlari da tasidigi icin yuzde bu listeden hesaplanir;
#: aksi halde 14 adimlik bir listede gercek ilerleme yaniltici gorunurdu.
AKIS = (
    "queued",
    "preflight",
    "validating_archive",
    "creating_staging",
    "restoring",
    "migrating",
    "validating_staging",
    "preparing_cutover",
    "cutover",
    "post_validation",
    "done",
)


@dataclass
class _RestoreState:
    backup_id: int | None = None
    filename: str | None = None
    status: str = "idle"  # idle | queued | validating | preparing | restoring | finalizing | done | failed
    current_step: str = "queued"
    step_index: int = 0  # 0..len(STEPS)-1
    total_steps: int = len(AKIS) - 1  # 'done' hedef
    progress_percent: int = 0
    message: str = ""
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    started_by: str | None = None
    # Adim adim log — UI alt panelde gosterebilir.
    logs: list[dict[str, Any]] = field(default_factory=list)


_state = _RestoreState()
_lock = threading.Lock()


def start(backup_id: int, filename: str, username: str) -> None:
    """Yeni restore baslatildiginda cagirilir — state'i sifirla."""
    with _lock:
        _state.backup_id = backup_id
        _state.filename = filename
        _state.status = "queued"
        _state.current_step = "queued"
        _state.step_index = 0
        _state.progress_percent = 0
        _state.message = "Restore kuyruga alindi..."
        _state.error = None
        _state.started_at = time.time()
        _state.finished_at = None
        _state.started_by = username
        _state.logs = [_log_entry("queued", "Restore baslatildi", "info")]


def set_step(step: str, message: str = "") -> None:
    """Adim degisikligi — frontend bunu progress bar'da gosterir."""
    if step not in STEPS:
        return
    with _lock:
        _state.status = step
        _state.current_step = step
        # Yuzde GERCEK akistan hesaplanir; STEPS geriye uyumluluk icin
        # eski adlari da tasiyor ve onlarin indeksi ilerlemeyi bozardi.
        _state.step_index = AKIS.index(step) if step in AKIS else _state.step_index
        _state.progress_percent = int((_state.step_index / max(1, _state.total_steps)) * 100)
        _state.message = message or _default_message(step)
        _state.logs.append(_log_entry(step, _state.message, "info"))


def update_message(message: str, level: str = "info") -> None:
    """Sub-step mesaji — current_step degisiklik YOK, sadece message + log
    guncellenir. Restore icinde 'restoring' uzun bir adim; pg_restore'un
    pre-flight asamalarini (connection terminate, dispose, command launch)
    UI'da gostermek icin bu kullanilir."""
    with _lock:
        _state.message = message
        _state.logs.append(_log_entry(_state.current_step, message, level))


def fail(error: str) -> None:
    """Restore basarisiz oldu."""
    with _lock:
        _state.status = "failed"
        _state.error = error
        _state.finished_at = time.time()
        _state.message = f"Hata: {error[:200]}"
        _state.logs.append(_log_entry(_state.current_step, error[:500], "error"))


def finish_ok() -> None:
    """Restore basarili tamamlandi."""
    with _lock:
        _state.status = "done"
        _state.current_step = "done"
        _state.step_index = _state.total_steps
        _state.progress_percent = 100
        _state.finished_at = time.time()
        _state.message = "Restore tamamlandi."
        _state.logs.append(_log_entry("done", "Restore basariyla tamamlandi", "success"))


def snapshot() -> dict[str, Any]:
    """Frontend polling endpoint'i icin guncel state."""
    with _lock:
        elapsed = None
        if _state.started_at is not None:
            end = _state.finished_at if _state.finished_at else time.time()
            elapsed = round(end - _state.started_at, 1)
        return {
            "backup_id": _state.backup_id,
            "filename": _state.filename,
            "status": _state.status,
            "current_step": _state.current_step,
            "step_index": _state.step_index,
            "total_steps": _state.total_steps,
            "progress_percent": _state.progress_percent,
            "message": _state.message,
            "error": _state.error,
            "started_by": _state.started_by,
            "started_at": _state.started_at,
            "finished_at": _state.finished_at,
            "elapsed_sec": elapsed,
            "steps": list(STEPS),
            # Son 50 log satiri (uzun restore'da listeyi sismelemek icin cap)
            "logs": list(_state.logs[-50:]),
        }


def is_running() -> bool:
    """Su an aktif restore var mi? (duplicate request'i engellemek icin)"""
    with _lock:
        return _state.status not in ("idle", "done", "failed")


def _default_message(step: str) -> str:
    return {
        "queued": "Restore kuyruga alindi",
        "validating": "Yedek dosyasi dogrulaniyor...",
        "preparing": "Veritabani hazirlaniyor...",
        "restoring": "Veriler geri yukleniyor (pg_restore)...",
        "finalizing": "Baglantilar yenileniyor...",
        "preflight": "On kontroller yapiliyor...",
        "validating_archive": "Yedek arsivi dogrulaniyor...",
        "creating_staging": "Gecici veritabani olusturuluyor...",
        "migrating": "Sema guncelleniyor...",
        "validating_staging": "Geri yuklenen veri dogrulaniyor...",
        "preparing_cutover": "Gecise hazirlaniliyor...",
        "cutover": "Veritabani degistiriliyor (kisa kesinti)...",
        "post_validation": "Yeni veritabani dogrulaniyor...",
        "done": "Restore tamamlandi",
    }.get(step, step)


def _log_entry(step: str, message: str, level: str) -> dict[str, Any]:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "step": step,
        "level": level,
        "message": message,
    }
