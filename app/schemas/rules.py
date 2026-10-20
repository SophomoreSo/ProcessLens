from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import ApiModel

RuleType = Literal["required_activity", "forbidden_activity", "precedence", "max_duration",
                   "max_occurrences", "start_activity", "end_activity"]
AlertType = Literal["duration_threshold", "rework", "conformance_violation"]
AlertStatus = Literal["open", "acknowledged", "resolved"]
Severity = Literal["low", "medium", "high"]


class _Params(BaseModel):
    model_config = {"extra": "forbid"}


class _OpenCasesParam(_Params):
    assume_complete: bool = Field(
        False, description="Treat incomplete/unknown cases as complete. Off by default: results that could still "
                           "change are reported as not evaluable.")


class RequiredActivityParams(_OpenCasesParam):
    activity: str = Field(min_length=1)


class ForbiddenActivityParams(_OpenCasesParam):
    activity: str = Field(min_length=1)


class PrecedenceParams(_OpenCasesParam):
    activity_a: str = Field(min_length=1, description="Must occur before every occurrence of activity_b")
    activity_b: str = Field(min_length=1)

    @model_validator(mode="after")
    def _distinct(self):
        if self.activity_a == self.activity_b:
            raise ValueError("activity_a and activity_b must differ")
        return self


class MaxDurationParams(_Params):
    max_hours: float = Field(gt=0)
    metric: Literal["observed_duration", "completed_throughput"] = "completed_throughput"


class MaxOccurrencesParams(_OpenCasesParam):
    activity: str = Field(min_length=1)
    max_count: int = Field(ge=0)


class StartActivityParams(_Params):
    activities: list[str] = Field(min_length=1)


class EndActivityParams(_OpenCasesParam):
    activities: list[str] = Field(min_length=1)


RULE_PARAM_MODELS: dict[str, type[_Params]] = {
    "required_activity": RequiredActivityParams,
    "forbidden_activity": ForbiddenActivityParams,
    "precedence": PrecedenceParams,
    "max_duration": MaxDurationParams,
    "max_occurrences": MaxOccurrencesParams,
    "start_activity": StartActivityParams,
    "end_activity": EndActivityParams,
}


class ConformanceRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    rule_type: RuleType
    params: dict
    enabled: bool = True

    @model_validator(mode="after")
    def _params(self):
        self.params = RULE_PARAM_MODELS[self.rule_type].model_validate(self.params).model_dump()
        return self


class ConformanceRuleOut(ApiModel):
    id: int
    dataset_id: int
    name: str
    rule_type: str
    params: dict
    enabled: bool
    revision: int
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class ConformanceSummary(BaseModel):
    rule: ConformanceRuleOut
    population_case_count: int
    compliant: int
    violating: int
    not_evaluable: int


class DurationAlertParams(_Params):
    threshold_hours: float = Field(gt=0)
    metric: Literal["observed_duration", "completed_throughput", "open_case_age"] = "observed_duration"


class ReworkAlertParams(_Params):
    activity: str | None = Field(None, description="Only alert when this activity is repeated (any activity if empty)")

    @field_validator("activity", mode="before")
    @classmethod
    def _blank(cls, v):
        return None if isinstance(v, str) and not v.strip() else v


class ConformanceAlertParams(_Params):
    pass


ALERT_PARAM_MODELS: dict[str, type[_Params]] = {
    "duration_threshold": DurationAlertParams,
    "rework": ReworkAlertParams,
    "conformance_violation": ConformanceAlertParams,
}


class AlertRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    alert_type: AlertType
    params: dict = Field(default_factory=dict)
    severity: Severity = "medium"
    enabled: bool = True
    conformance_rule_id: int | None = None

    @model_validator(mode="after")
    def _params(self):
        self.params = ALERT_PARAM_MODELS[self.alert_type].model_validate(self.params).model_dump()
        if self.alert_type == "conformance_violation" and self.conformance_rule_id is None:
            raise ValueError("conformance_violation alerts require conformance_rule_id")
        if self.alert_type != "conformance_violation":
            self.conformance_rule_id = None
        return self


class AlertRuleOut(ApiModel):
    id: int
    dataset_id: int
    name: str
    alert_type: str
    params: dict
    severity: str
    enabled: bool
    conformance_rule_id: int | None
    last_evaluated_at: datetime | None
    created_at: datetime


class AlertOut(ApiModel):
    id: int
    alert_rule_id: int
    dataset_id: int
    case_id: str
    status: str
    severity: str
    message: str
    condition_active: bool
    dataset_version: int
    first_detected_at: datetime
    last_evaluated_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    note: str | None
    rule_name: str | None = None


class AlertStatusUpdate(BaseModel):
    status: AlertStatus
    note: str | None = Field(None, max_length=2000)


class AlertEvaluationResult(BaseModel):
    rules_evaluated: int
    created: int
    updated: int
    no_longer_matching: int
    evaluated_at: datetime
