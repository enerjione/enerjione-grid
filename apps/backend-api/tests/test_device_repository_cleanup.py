from types import SimpleNamespace

from app.models.alarm_rule import AlarmRule
from app.repositories.device_repository import DeviceRepository


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    def __init__(self, rules):
        self.rules = rules

    def scalars(self, _stmt):
        return _Scalars(self.rules)


def _rule(value: str | None, active: bool = True):
    return SimpleNamespace(device_code_filter=value, is_active=active)


def test_rule_filter_cleanup_uses_exact_device_codes():
    target = _rule("DEV-1, DEV-10")
    untouched = _rule("DEV-100")
    repository = DeviceRepository(_Db([target, untouched]))

    changed = repository._remove_device_from_rule_filters("DEV-1")

    assert changed == 1
    assert target.device_code_filter == "DEV-10"
    assert target.is_active is True
    assert untouched.device_code_filter == "DEV-100"


def test_rule_filter_cleanup_disables_empty_rule():
    target = _rule("DEV-1")
    repository = DeviceRepository(_Db([target]))

    changed = repository._remove_device_from_rule_filters("DEV-1")

    assert changed == 1
    assert target.device_code_filter is None
    assert target.is_active is False
