from typing import Literal

from pydantic import BaseModel, Field, model_validator


AlarmLevel = Literal["info", "warning", "critical"]
AlarmComparator = Literal[
    "gt", "gte", "lt", "lte", "eq", "ne", "between", "outside", "boolean_true", "boolean_false"
]
#: `comm_loss` = urunle gelen STANDART kural (haberlesme arizasi). Bir
#: sinyalin degerine degil cihazin KALITESINE bakar; `signal_key` alaninda
#: katalogda karsiligi olmayan bir nisan deger tasir ve esik/karsilastirma
#: alanlari onda anlamsizdir. Kullanici olusturmaz, silemez — duzenler.
RuleKind = Literal["simple", "composite", "comm_loss"]
CompositeLogic = Literal["AND", "OR"]


AggFn = Literal["avg", "min", "max", "sum", "count_above", "count_below"]


class FormulaVar(BaseModel):
    """Formul ifadesindeki bir degisken (Faz 3).

    Su an sadece 'signal' kaynagi destekleniyor: degisken anlik sinyal
    degerine bagli. İleride 'agg' (pencere) da eklenebilir; mimari ayni
    kalir."""

    name: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    signal_key: str = Field(min_length=1, max_length=80)
    device_code: str = Field(default="*", min_length=1, max_length=80)


class CompositeTerm(BaseModel):
    """Composite kuralda tek bir terim (AND/OR'in argumani).

    Uc tur terim destekli:
      - kind='compare' (default): anlik sinyal -> comparator(threshold)
      - kind='agg'             : son N saniyede ortalama/min/max/sum/count
                                  -> comparator(threshold)
      - kind='formula' (Faz 3)  : aritmetik ifade (degiskenler sinyallerdir)
                                  -> comparator(threshold)
    """

    # Faz 1 ile geriye uyum: alan yoksa 'compare' kabul edilir.
    kind: Literal["compare", "agg", "formula"] = "compare"

    # 'compare' ve 'agg' icin: kuralin tetiklendigi sinyal-anahtar.
    # 'formula' icin yine bir sinyal-anahtari beklenir (kural cache'inde
    # rules_by_signal map'i icin); ifadede gerekirse bu sinyal kullanilir.
    signal_key: str = Field(min_length=1, max_length=80)
    # "*" => kuralin tetikleyicisi olan anchor cihaz. Spesifik cihaz kodu
    # vermek istersek dogrudan yazariz.
    device_code: str = Field(default="*", min_length=1, max_length=80)
    comparator: AlarmComparator
    threshold: float = 0.0
    threshold_high: float | None = None

    # 'agg' icin ek alanlar:
    agg_fn: AggFn | None = None
    agg_window_sec: int = Field(default=60, ge=1, le=86400)  # 1 sn .. 1 gun
    # count_above / count_below icin esik (sayilacak deger). avg/min/max/sum'da
    # kullanilmaz.
    agg_arg: float = 0.0

    # 'formula' icin: ifade + degisken eslemeleri.
    # Ifade orn: "(I_a + I_b + I_c) / 3" veya "max(V1, V2) - min(V1, V2)".
    # Izinli sozdizimi: +, -, *, /, %, **, unary -, parantez, sayi sabitleri,
    # tanimli degisken adlari, ve whitelist fonksiyonlar (min, max, abs).
    formula_expr: str | None = Field(default=None, max_length=500)
    formula_vars: list[FormulaVar] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def _validate_kind(self):
        if self.kind == "agg" and self.agg_fn is None:
            raise ValueError("agg term requires 'agg_fn'")
        if self.kind == "formula":
            if not self.formula_expr or not self.formula_expr.strip():
                raise ValueError("formula term requires non-empty 'formula_expr'")
            if not self.formula_vars:
                raise ValueError("formula term requires at least one 'formula_vars' entry")
            # Degisken adlari unique olmali.
            names = [v.name for v in self.formula_vars]
            if len(set(names)) != len(names):
                raise ValueError("formula_vars names must be unique")
        return self


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
    # Virgulle ayrili cihaz MODELI kodlari; bos = tum modeller.
    # Sinyaller modeller arasinda ortak degildir (bkz. models/alarm_rule.py).
    device_model_filter: str | None = None
    is_active: bool = True
    # Kural-bazli bildirim kanallari (web bildirimi her zaman gider).
    # Default false: kullanici acmadan email/sms/telegram gitmez.
    notify_email: bool = False
    notify_sms: bool = False
    notify_telegram: bool = False
    notify_whatsapp_web: bool = False
    # "Bu alarm gercek hat arizasi uretir mi?" True (default): harita kirmizi +
    # Hat Arizasi acilir. False: yalniz Alarmlar ekraninda gorunur, haritada
    # ariza gostermez. Default True -> geriye donuk uyum.
    produces_fault: bool = True

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
    device_model_filter: str | None = None
    is_active: bool | None = None
    notify_email: bool | None = None
    notify_sms: bool | None = None
    notify_telegram: bool | None = None
    notify_whatsapp_web: bool | None = None
    produces_fault: bool | None = None


class AlarmRuleRead(AlarmRuleBase):
    id: int

    class Config:
        from_attributes = True
