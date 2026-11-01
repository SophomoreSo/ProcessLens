"""Request bodies and the package exchange format."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

KEY_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
FORMATS = Literal["auto", "number", "integer", "percent", "days", "hours", "currency", "text"]
COMPONENT_TYPES = Literal["kpi", "bar", "line", "pie", "table", "process", "variants", "text"]
EXCHANGE_FORMAT = "processlens-miner-package"
EXCHANGE_VERSION = 1


class PackageIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=4000)
    dataset_id: int
    object_type: str | None = Field(None, max_length=255)
    starter_content: bool = True


class PackagePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    dataset_id: int | None = None
    object_type: str | None = Field(None, max_length=255)
    base_filter: str | None = Field(None, max_length=4000)


class KpiIn(BaseModel):
    key: str = Field(pattern=KEY_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    formula: str = Field(min_length=1, max_length=4000)
    format: FORMATS = "auto"
    target: float | None = None
    higher_is_better: bool = True
    description: str = Field("", max_length=4000)


class FilterIn(BaseModel):
    key: str = Field(pattern=KEY_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    formula: str = Field(min_length=1, max_length=4000)
    description: str = Field("", max_length=4000)


class Item(BaseModel):
    """A dimension or measure: a PLQL formula with an optional display label (and format, for measures)."""

    formula: str = Field(min_length=1, max_length=4000)
    label: str = Field("", max_length=200)
    format: FORMATS = "auto"


class Component(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    type: COMPONENT_TYPES
    title: str = Field("", max_length=200)
    width: Literal[3, 4, 6, 8, 12] = 6
    filter: str = Field("", max_length=4000)  # extra PLQL condition for this component only
    config: dict = Field(default_factory=dict)


class ViewIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=4000)
    filter: str = Field("", max_length=4000)
    components: list[Component] = Field(default_factory=list, max_length=60)


class QuerySpec(BaseModel):
    dimensions: list[Item] = Field(default_factory=list, max_length=8)
    measures: list[Item] = Field(default_factory=list, max_length=20)
    filters: list[str] = Field(default_factory=list, max_length=20)
    order_by: list[tuple[int, bool]] | None = None
    limit: int = Field(500, ge=1, le=10_000)


class SavedQueryIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    spec: QuerySpec


class EvaluateIn(BaseModel):
    formula: str = Field(min_length=1, max_length=4000)
    kind: Literal["measure", "condition"] = "measure"
    # When testing an edited KPI/filter, its new formula replaces the stored one for references.
    key: str | None = None


class ComponentDataIn(BaseModel):
    component: Component
    filters: list[str] = Field(default_factory=list, max_length=40)


class ImportIn(BaseModel):
    document: dict
    dataset_id: int | None = None  # default: the dataset id recorded in the document
    object_type: str | None = None
