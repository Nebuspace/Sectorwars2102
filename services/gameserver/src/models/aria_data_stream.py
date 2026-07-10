"""ARIA Data Index registry -- the canonical catalog of every observation
stream ARIA learns from (WO-P6-aria-data-index-registry).

Canon: DATA_MODELS/aria-data-index.md (ADR-0092 the architecture this index
serves). Per that doc's Registry table rule 1: "Adding a data point ARIA
tracks is a registry row plus a writer hook -- never a hardcoded enum. The
``memory_type`` taxonomy is derived: one type per stream key." So there is no
separate ``memory_type`` column here -- a stream's registry ``key`` (e.g.
``"threat.combat"``) IS the value any ``ARIAPersonalMemory``-backed stream
writes into ``ARIAPersonalMemory.memory_type``.

Schema matches aria-data-index.md's "Registry table" section field-for-field
(the doc, not the dispatching WO's paraphrase -- the WO's own instruction is
"if aria-data-index.md conflicts with the sketch, follow the DOC + report").
The WO sketch named ``memory_type`` / "retrieval scope" / "transparency
surface" as row fields; the doc's actual table has no such columns (memory_
type is derived from ``key`` as above; ``transparency_visible`` is the doc's
literal transparency field; "retrieval scope" has no doc-defined column --
the doc's Consumers section describes read access in prose, not a per-row
field). See this WO's report for the full field reconciliation.
"""

import enum

from sqlalchemy import Boolean, Column, Enum as SQLEnum, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from src.core.database import Base


class ARIADataStreamDomain(str, enum.Enum):
    """aria-data-index.md's six domain sections. Enum VALUES are the exact
    backtick-tagged tokens given in each section header (e.g. "Assets
    (`asset`)" -> "asset"), lowercase per this codebase's enum-serialization
    convention (values_callable pins the PG label to .value, not .name --
    see ShipRegistry.RegistryEventType / ContractIssuerType)."""
    NAV = "nav"
    COMMERCE = "commerce"
    THREAT = "threat"
    ASSET = "asset"
    SOCIAL = "social"
    META = "meta"


class ARIADataStreamRetention(str, enum.Enum):
    """aria-data-index.md's three retention classes (exact doc tokens)."""
    PERMANENT = "permanent"
    ROLLING_90D = "rolling_90d"
    BUDGET_PRUNED = "budget_pruned"


class ARIADataStream(Base):
    """One row per canon ARIA observation stream. String PK ``key`` (e.g.
    ``"commerce.trade"``) doubles as the ``memory_type`` value for any
    stream whose ``storage_table`` is ``ARIAPersonalMemory`` -- see module
    docstring."""

    __tablename__ = "aria_data_streams"

    key = Column(String(64), primary_key=True)
    domain = Column(
        SQLEnum(
            ARIADataStreamDomain,
            name="aria_data_stream_domain",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    display_name = Column(String(100), nullable=False)
    description = Column(Text, nullable=False)
    trigger_event = Column(String(255), nullable=False)
    payload_schema = Column(JSONB, nullable=False)
    storage_table = Column(String(255), nullable=False)
    retention_class = Column(
        SQLEnum(
            ARIADataStreamRetention,
            name="aria_data_stream_retention",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    transparency_visible = Column(Boolean, nullable=False, default=True, server_default="true")
    version = Column(Integer, nullable=False, default=1, server_default="1")
