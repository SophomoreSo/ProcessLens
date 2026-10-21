"""Mining runs (discovery, conformance, comparison, ...), their artifacts and reference models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.dataset import utcnow


class MiningRun(Base):
    """One execution of an expensive mining operation, with full provenance.

    status: queued | running | succeeded | failed | cancelled | timed_out | interrupted
    """

    __tablename__ = "mining_runs"
    __table_args__ = (
        Index("ix_mining_runs_dataset", "dataset_id", "kind"),
        Index("ix_mining_runs_cache", "cache_key"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # discovery | conformance | comparison | model_import | ocpn
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), nullable=True)
    ocel_log_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dataset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    settings_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filter_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    split: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    algorithm_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    implementation: Mapped[dict] = mapped_column(JSON, default=dict)
    request: Mapped[dict] = mapped_column(JSON, default=dict)  # other inputs (methods, limits, model source ...)
    cache_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    stage_key: Mapped[str] = mapped_column(String(80), default="run.stage.queued")
    stage_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timeout_s: Mapped[float] = mapped_column(Float, default=300)
    runtime_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reference_model_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MiningArtifact(Base):
    """Text artifact of a run: native model files (PNML/PTML/BPMN), JSON representations, diagnostics."""

    __tablename__ = "mining_artifacts"
    __table_args__ = (UniqueConstraint("run_id", "name", name="uq_mining_artifact_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("mining_runs.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    media_type: Mapped[str] = mapped_column(String(80))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReferenceModel(Base):
    """A model the user explicitly approved as the reference for conformance checking."""

    __tablename__ = "reference_models"
    __table_args__ = (Index("ix_reference_models_dataset", "dataset_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    model_type: Mapped[str] = mapped_column(String(40))  # petri_net | process_tree | declare | log_skeleton | temporal_profile
    representation: Mapped[dict] = mapped_column(JSON)
    pnml: Mapped[str | None] = mapped_column(Text, nullable=True)  # Petri net used for replay/alignments
    source: Mapped[dict] = mapped_column(JSON)  # {"type": "run", "run_id", "algorithm_id"} | {"type": "upload", "format", "filename"}
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
