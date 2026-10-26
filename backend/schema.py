"""
Document schema — common internal representation.

Every extractor (PDF, DOCX) produces a DocumentResult containing
an ordered list of ContentBlock objects. This schema is the single
source of truth shared by all services.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ──────────────────────────────────────────────────────────────
# Enumerations
# ──────────────────────────────────────────────────────────────

class BlockType(str, Enum):
    TEXT    = "text"
    TABLE   = "table"
    IMAGE   = "image"
    FIGURE  = "figure"
    UNKNOWN = "unknown"


class ExtractionMethod(str, Enum):
    NATIVE_TEXT   = "native_text"     # selectable text from PDF/DOCX
    NATIVE_TABLE  = "native_table"    # structured table from PDF/DOCX
    GEOMETRIC_TABLE = "geometric_table" # table reconstructed from geometry
    NATIVE_IMAGE  = "native_image"    # embedded image blob
    OCR_TEXT      = "ocr_text"        # text recovered via OCR
    OCR_TABLE     = "ocr_table"       # table recovered via OCR
    RASTERIZED    = "rasterized"      # page or region rasterized for inspection
    RENDERED_REGION = "rendered_region" # page region rendered at high DPI for composite figure/diagram
    UNKNOWN       = "unknown"


class ValidationStatus(str, Enum):
    VALID     = "valid"
    SUSPECT   = "suspect"      # passed but with concerns
    FAILED    = "failed"       # failed validation
    SKIPPED   = "skipped"      # validation not attempted
    FALLBACK  = "fallback"     # fell back to image/raw content


class SourceFormat(str, Enum):
    PDF  = "pdf"
    DOCX = "docx"


# ──────────────────────────────────────────────────────────────
# Geometry
# ──────────────────────────────────────────────────────────────

@dataclass
class Geometry:
    """Bounding rectangle in page coordinates (points for PDF, EMU/px for DOCX)."""
    x0: float
    y0: float
    x1: float
    y1: float
    page_width: float  = 0.0
    page_height: float = 0.0

    def to_dict(self) -> dict:
        return {
            "x0": self.x0, "y0": self.y0,
            "x1": self.x1, "y1": self.y1,
            "page_width": self.page_width,
            "page_height": self.page_height,
        }


# ──────────────────────────────────────────────────────────────
# Table structures
# ──────────────────────────────────────────────────────────────

@dataclass
class TableCell:
    text:       str
    row_index:  int
    col_index:  int
    rowspan:    int = 1
    colspan:    int = 1
    is_header:  bool = False

    def to_dict(self) -> dict:
        return {
            "text":      self.text,
            "row_index": self.row_index,
            "col_index": self.col_index,
            "rowspan":   self.rowspan,
            "colspan":   self.colspan,
            "is_header": self.is_header,
        }


@dataclass
class TableRow:
    cells:      list[TableCell]
    row_index:  int
    is_header:  bool = False
    bbox:       tuple[float, float, float, float] | None = None

    def to_dict(self) -> dict:
        d = {
            "row_index": self.row_index,
            "is_header": self.is_header,
            "cells":     [c.to_dict() for c in self.cells],
        }
        if self.bbox is not None:
            d["bbox"] = list(self.bbox)
        return d


@dataclass
class TableData:
    rows:           list[TableRow]
    column_count:   int
    row_count:      int
    has_header:     bool = False
    html:           str  = ""           # reconstructed semantic HTML

    def to_dict(self) -> dict:
        return {
            "rows":         [r.to_dict() for r in self.rows],
            "column_count": self.column_count,
            "row_count":    self.row_count,
            "has_header":   self.has_header,
            "html":         self.html,
        }


# ──────────────────────────────────────────────────────────────
# Content block
# ──────────────────────────────────────────────────────────────

@dataclass
class ContentBlock:
    """
    A single extractable unit of content from a document.

    Every block has:
    - block_id          — stable UUID
    - block_type        — text / table / image / figure / unknown
    - source_format     — pdf / docx
    - page_number       — 1-based page (PDF) or section index (DOCX)
    - reading_order     — 0-based ordering within the document
    - extraction_method — how the content was obtained
    - validation_status — result of schema / content validation
    - text              — raw text (for text blocks; flattened for others)
    - table_data        — structured rows/cells (for table blocks)
    - asset_id          — reference to a stored image file (for image/figure)
    - geometry          — bounding box when available
    - metadata          — extra key/value pairs
    - notes             — human-readable notes or warnings
    """

    block_type:         BlockType
    source_format:      SourceFormat
    page_number:        int
    reading_order:      int
    extraction_method:  ExtractionMethod = ExtractionMethod.UNKNOWN
    validation_status:  ValidationStatus = ValidationStatus.SKIPPED

    text:               str               = ""
    table_data:         TableData | None  = None
    asset_id:           str | None        = None       # filename in assets folder
    geometry:           Geometry | None   = None
    metadata:           dict[str, Any]    = field(default_factory=dict)
    notes:              list[str]         = field(default_factory=list)
    classification:     str               = "unknown"
    classification_reason: str            = ""
    block_id:           str               = field(default_factory=lambda: str(uuid.uuid4()))

    # ── Convenience ──────────────────────────────
    def add_note(self, note: str) -> None:
        self.notes.append(note)

    def fingerprint(self) -> str:
        """Content-based hash for deduplication."""
        content = (self.text or "") + (self.asset_id or "")
        if self.table_data:
            for row in self.table_data.rows:
                for cell in row.cells:
                    content += cell.text
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "block_id":         self.block_id,
            "block_type":       self.block_type.value,
            "source_format":    self.source_format.value,
            "page_number":      self.page_number,
            "reading_order":    self.reading_order,
            "extraction_method":self.extraction_method.value,
            "validation_status":self.validation_status.value,
            "text":             self.text,
            "table_data":       self.table_data.to_dict() if self.table_data else None,
            "asset_id":         self.asset_id,
            "geometry":         self.geometry.to_dict() if self.geometry else None,
            "metadata":         self.metadata,
            "notes":            self.notes,
            "classification":   self.classification,
            "classification_reason": self.classification_reason,
        }


# ──────────────────────────────────────────────────────────────
# Document result
# ──────────────────────────────────────────────────────────────

@dataclass
class ProcessingSummary:
    total_pages:      int   = 0
    total_blocks:     int   = 0
    text_blocks:      int   = 0
    table_blocks:     int   = 0
    image_blocks:     int   = 0
    figure_blocks:    int   = 0
    unknown_blocks:   int   = 0
    scanned_pages:    int   = 0
    warnings:         list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_pages":   self.total_pages,
            "total_blocks":  self.total_blocks,
            "text_blocks":   self.text_blocks,
            "table_blocks":  self.table_blocks,
            "image_blocks":  self.image_blocks,
            "figure_blocks": self.figure_blocks,
            "unknown_blocks":self.unknown_blocks,
            "scanned_pages": self.scanned_pages,
            "warnings":      self.warnings,
        }


@dataclass
class DocumentResult:
    """Top-level result returned by every extractor."""
    document_id:    str
    filename:       str
    source_format:  SourceFormat
    blocks:         list[ContentBlock]  = field(default_factory=list)
    summary:        ProcessingSummary   = field(default_factory=ProcessingSummary)
    metadata:       dict[str, Any]      = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "document_id":   self.document_id,
            "filename":      self.filename,
            "source_format": self.source_format.value,
            "blocks":        [b.to_dict() for b in self.blocks],
            "summary":       self.summary.to_dict(),
            "metadata":      self.metadata,
        }

    def compute_summary(self) -> None:
        """Recompute summary statistics from the blocks list."""
        s = self.summary
        s.total_blocks  = len(self.blocks)
        s.text_blocks   = sum(1 for b in self.blocks if b.block_type == BlockType.TEXT)
        s.table_blocks  = sum(1 for b in self.blocks if b.block_type == BlockType.TABLE)
        s.image_blocks  = sum(1 for b in self.blocks if b.block_type == BlockType.IMAGE)
        s.figure_blocks = sum(1 for b in self.blocks if b.block_type == BlockType.FIGURE)
        s.unknown_blocks= sum(1 for b in self.blocks if b.block_type == BlockType.UNKNOWN)
