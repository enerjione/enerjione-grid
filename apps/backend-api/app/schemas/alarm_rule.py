from typing import Literal

from pydantic import BaseModel, Field, model_validator


AlarmLevel = Literal["info", "warning", "critical"]
AlarmComparator = Literal[
    "gt", "gte", "lt", "lte", "eq", "ne", "between", "outside", "boolean_true", "boolean_false"
]
RuleKind = Literal["simple", "composite"]
CompositeLogic = Literal["AND", "OR"]


class CompositeTerm(BaseModel):
    """Composite kuralda tek bir terim (AND/OR'in argumani).

    Faz 1: kind = 'compare' (sadelik icin field olarak tasimiyoruz, hep
    compare kabul ediyoruz). Faz 2'de 'agg' ve Faz 3'te 'formula' eklenir.
    """

    signal_key: str = Field(min_length=1, max_length=80)
    # "*" => kuralin tetikleyicisi olan anchor cihaz. Spesifik cihaz kodu
    # vermek istersek dogrudan yazariz.
    device_code: str = Field(default="*", min_length=1, max_length=80)
    comparator: AlarmComparator
    threshold: float = 0.0
    threshold_high: float | None = None


class CompositeExpression(BaseModel):
    """expression_json icinde saklanan composite kural ifadesi (Faz 1).

    En az 1, en fazla 8 terim. Daha karmasik ic ice yapilar Faz 3'te
    formula ile cozulur.
    """

    logic: CompositeLogic = "AND"
    terms: list[CompositeTerm] = Field(min_length=1, max_length=8)


class AlarmRuleBase(BaseModel):
    signal_key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    level: AlarmLevel = "warning"
    rule_kind: RuleKind = "simple"
    # Composite ise dolu; simple ise None. Validator asagida tutarliligi kontrol eder.
    expression: CompositeExpression | None = None
    comparator: AlarmComparator = "gt"
    threshold: float = 0.0
    threshold_high: float | None = None
    hysteresis: float = 0.0
    debounce_sec: int = 0
    device_code_filter: str | None = None
    is_active: bool = True
    # Kural-bazli bildirim kanallari (web bildirimi her zaman gider).
    # Default false: kullanici acmadan email/sms/telegram gitmez.
    notify_email: bool = False
    notify_sms: bool = False
    notify_telegram: bool = False

    @model_validator(mode="after")
    def _validate_kind_consistency(self):
        if self.rule_kind == "composite" and self.expression is None:
            raise ValueError("composite rule requires 'expression'")
        if self.rule_kind == "simple" and self.expression is not None:
            # simple kurallarda expression yokmus gibi davran — sessizce temizle
            # (eski client'lar gonderirse hata yerine ignore daha pratik).
            self.expression = None
        return self


class AlarmRuleCreate(AlarmRuleBase):
    pass


class AlarmRuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    level: AlarmLevel | None = None
    rule_kind: RuleKind | None = None
    expression: CompositeExpression | None = None
    comparator: AlarmComparator | None = None
    threshold: float | None = None
    threshold_high: float | None = None
    hysteresis: float | None = None
    debounce_sec: int | None = None
    device_code_filter: str | None = None
    is_active: bool | None = None
    notify_email: bool | None = None
    notify_sms: bool | None = None
    notify_telegram: bool | None = None


class AlarmRuleRead(AlarmRuleBase):
    id: int

    class Config:
        from_attributes = True
