"""The single filter model shared by every analysis, table, comparison and export.

Semantics (case-level; matching cases keep their complete event histories):

* ``start_from`` / ``start_to``: the case's first event falls on a calendar date
  in the dataset display timezone with ``start_from <= date <= start_to``
  (both inclusive).
* ``activity``: case contains *every* listed activity (at least once).
* ``exclude_activity``: case contains *none* of the listed activities.
* ``resource`` / ``department``: at least one event of the case has any of the
  listed values ("any event, any value").
* ``variant``: case follows any of the listed variant IDs.
* ``min_duration_h`` / ``max_duration_h``: observed duration in hours,
  inclusive bounds ``min <= duration <= max``.
* ``rework``: true = cases with at least one repeated activity; false = none.
* ``status``: completion status in the listed values.
* ``conformance_rule`` + ``conformance_result``: the case's result for that rule
  is one of the listed results.
* ``transition_from`` + ``transition_to``: case contains that directly-follows
  pair at least once; ``__start__`` / ``__end__`` denote the synthetic markers.

All criteria are combined with AND.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

START_MARKER = "__start__"
END_MARKER = "__end__"


class FilterSpec(BaseModel):
    start_from: date | None = None
    start_to: date | None = None
    activity: list[str] = Field(default_factory=list)
    exclude_activity: list[str] = Field(default_factory=list)
    resource: list[str] = Field(default_factory=list)
    department: list[str] = Field(default_factory=list)
    variant: list[str] = Field(default_factory=list)
    min_duration_h: float | None = Field(None, ge=0)
    max_duration_h: float | None = Field(None, ge=0)
    rework: bool | None = None
    status: list[Literal["completed", "incomplete", "unknown"]] = Field(default_factory=list)
    conformance_rule: int | None = None
    conformance_result: list[Literal["compliant", "violating", "not_evaluable"]] = Field(default_factory=list)
    transition_from: str | None = None
    transition_to: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.start_from and self.start_to and self.start_from > self.start_to:
            raise ValueError("start_from must not be after start_to")
        if (self.min_duration_h is not None and self.max_duration_h is not None
                and self.min_duration_h > self.max_duration_h):
            raise ValueError("min_duration_h must not exceed max_duration_h")
        if self.conformance_result and self.conformance_rule is None:
            raise ValueError("conformance_result requires conformance_rule")
        if (self.transition_from is None) != (self.transition_to is None):
            raise ValueError("transition_from and transition_to must be given together")
        return self

    def normalized(self) -> dict:
        data = self.model_dump(mode="json", exclude_none=True)
        out = {}
        for k, v in sorted(data.items()):
            if isinstance(v, list):
                v = sorted({str(x) for x in v if str(x).strip()})
                if not v:
                    continue
            out[k] = v
        if "conformance_result" in out and "conformance_rule" not in out:
            out.pop("conformance_result")
        if "conformance_rule" in out and "conformance_result" not in out:
            out.pop("conformance_rule")  # a rule without results does not filter
        return out

    def cache_key(self) -> str:
        return json.dumps(self.normalized(), sort_keys=True, separators=(",", ":"))

    def is_empty(self) -> bool:
        return not self.normalized()
