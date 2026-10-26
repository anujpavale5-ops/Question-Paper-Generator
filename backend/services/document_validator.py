"""
Document validator — validates a DocumentResult before returning to the frontend.

Checks:
- Schema completeness (required fields present)
- No duplicate block IDs
- Table HTML validity (basic check)
- Asset references exist on disk
- Blocks have valid block types
"""

from __future__ import annotations

import logging
import os

from backend.schema import (
    BlockType, DocumentResult, ValidationStatus,
)

logger = logging.getLogger(__name__)


class DocumentValidator:
    """Validate a DocumentResult and annotate blocks with validation status."""

    def __init__(self, assets_folder: str):
        self.assets_folder = assets_folder

    def validate(self, result: DocumentResult) -> list[str]:
        """
        Run all validation checks. Returns a list of warning strings.
        Also updates individual block validation_status where issues are found.
        """
        warnings: list[str] = []
        seen_ids: set[str] = set()

        for block in result.blocks:
            block_warnings: list[str] = []

            # ── Duplicate ID check ───────────────────────────────────
            if block.block_id in seen_ids:
                block_warnings.append(f"Duplicate block_id detected: {block.block_id}")
            seen_ids.add(block.block_id)

            # ── Required fields ──────────────────────────────────────
            if block.block_type not in BlockType.__members__.values():
                block_warnings.append(f"Unknown block_type: {block.block_type}")

            # ── Table blocks ─────────────────────────────────────────
            if block.block_type == BlockType.TABLE:
                if block.table_data is None:
                    block_warnings.append("TABLE block has no table_data.")
                    block.validation_status = ValidationStatus.FAILED
                elif not block.table_data.html or "<table" not in block.table_data.html:
                    block_warnings.append("TABLE block has empty or invalid HTML.")
                    if block.validation_status == ValidationStatus.VALID:
                        block.validation_status = ValidationStatus.SUSPECT

            # ── Image / figure blocks ─────────────────────────────────
            if block.block_type in (BlockType.IMAGE, BlockType.FIGURE):
                if not block.asset_id:
                    block_warnings.append(
                        f"{block.block_type.value} block has no asset_id."
                    )
                    block.validation_status = ValidationStatus.FAILED
                else:
                    asset_path = os.path.join(self.assets_folder, block.asset_id)
                    if not os.path.exists(asset_path):
                        block_warnings.append(
                            f"Asset file not found on disk: {block.asset_id}"
                        )
                        block.validation_status = ValidationStatus.FAILED

            # ── Text blocks ──────────────────────────────────────────
            if block.block_type == BlockType.TEXT and not block.text.strip():
                block_warnings.append("TEXT block has empty text.")
                if block.validation_status == ValidationStatus.VALID:
                    block.validation_status = ValidationStatus.SUSPECT

            # ── Propagate notes ──────────────────────────────────────
            for w in block_warnings:
                block.add_note(w)
                warnings.append(f"Block {block.block_id[:8]}: {w}")

        # ── Document-level checks ─────────────────────────────────
        if not result.blocks:
            warnings.append("No content blocks were extracted from the document.")

        if result.summary.total_blocks == 0:
            warnings.append("Document summary reports zero blocks.")

        return warnings
