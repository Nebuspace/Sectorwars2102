"""ARIA Data Index service (WO-P6-aria-data-index-registry).

Thin read layer over the `aria_data_streams` registry table -- the single
source of truth for every observation stream ARIA learns from
(DATA_MODELS/aria-data-index.md). The registry itself is seeded by
src.core.aria_data_stream_seeder.seed_aria_data_streams at startup; this
service only reads it.
"""

import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.aria_data_stream import ARIADataStream

logger = logging.getLogger(__name__)


class ARIADataIndexService:
    """Read-only accessor for the ARIA data-stream registry."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_streams(self) -> List[ARIADataStream]:
        """Every registered stream, ordered by key for a stable, deterministic
        response (a small, rarely-changing catalog -- no pagination needed)."""
        stmt = select(ARIADataStream).order_by(ARIADataStream.key)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_stream(self, key: str) -> Optional[ARIADataStream]:
        """Look up a single stream by its registry key (e.g. "threat.combat")."""
        stmt = select(ARIADataStream).where(ARIADataStream.key == key)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def transparency_visible_streams(self) -> List[ARIADataStream]:
        """Streams the memory-journal transparency browser should surface
        (aria-data-index.md rule 3 -- every stream is visible to its player
        unless its registry row says otherwise)."""
        stmt = (
            select(ARIADataStream)
            .where(ARIADataStream.transparency_visible.is_(True))
            .order_by(ARIADataStream.key)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
