"""
Block Classifier Service — Stage B.

Assigns semantic classification and reasoning to every ContentBlock using
the global DocumentProfile and local geometric/textual signals.

Classification types:
- document_title
- institution_metadata
- course_metadata
- unit_heading
- section_heading
- section_instruction
- question_group_heading
- question
- question_bank_table
- question_content_table
- reference_table
- figure
- image
- header_footer
- page_number
- decorative_logo
- unknown
"""

from __future__ import annotations
import re
from backend.schema import BlockType, ContentBlock
from backend.services.document_profiler import DocumentProfile, UNIT_HEADING_PATTERN
from backend.services.table_classifier import (
    is_question_bank_table,
    is_question_bank_table_html,
)

INSTITUTION_REGEX = re.compile(
    r"(?i)\b(?:college\s+of\s+engineering|university|institute\s+of\s+technology|education\s+trust|autonomous\s+institute|polytechnic)\b"
)
COURSE_META_REGEX = re.compile(
    r"(?i)(?:course\s*code|academic\s*year|exam\s*duration|total\s*marks|semester\s*[-:\s]|programme|credits\s*[:\s])"
)
SECTION_HEADING_REGEX = re.compile(r"(?i)^(?:part|section)\s+[a-z0-9]+$")
SECTION_INSTRUCTION_REGEX = re.compile(r"(?i)^(?:answer\s+(?:any|all)|attempt\s+(?:any|all)|note\s*[:-])")
QUESTION_GROUP_REGEX = re.compile(r"(?i)^Q\.?\s*\d+.*(?:answer\s+the\s+following|attempt\s+(?:any|all))")
PAGE_NUM_REGEX = re.compile(r"^(?:page\s*)?\d+(?:\s*/\s*\d+)?$", re.IGNORECASE)

QUESTION_PATTERNS = [
    re.compile(r"^\s*(?:(?:Q(?:uestion)?\.?\s*\d+[.)\]:]*)|(?:\d+[.)\]:]+))\s+\S", re.IGNORECASE),
    re.compile(r"^\s*\(([a-z])\)\s+\S"),
    re.compile(r"^\s*([a-z])[.)]\s+\S"),
    re.compile(r"^\s*\(([ivxlcdm]+)\)\s+\S", re.IGNORECASE),
]


class BlockClassifier:
    """Classifies content blocks into semantic roles."""

    def classify_blocks(
        self, blocks: list[ContentBlock], profile: DocumentProfile
    ) -> list[ContentBlock]:
        for b in blocks:
            self._classify_single_block(b, profile)
        return blocks

    def _classify_single_block(
        self, b: ContentBlock, profile: DocumentProfile
    ) -> None:
        y0_pct = (
            (b.geometry.y0 / b.geometry.page_height)
            if (b.geometry and b.geometry.page_height > 0)
            else 0.5
        )

        # ── 1. Images / Figures ────────────────────────────────────────
        if b.block_type in (BlockType.IMAGE, BlockType.FIGURE):
            if b.page_number == 1 and y0_pct < 0.2:
                b.classification = "decorative_logo"
                b.classification_reason = "Image at top of first page (logo)."
            else:
                b.classification = "figure" if b.block_type == BlockType.FIGURE else "image"
                b.classification_reason = "Embedded visual asset."
            return

        # ── 2. Tables ──────────────────────────────────────────────────
        if b.block_type == BlockType.TABLE:
            self._classify_table_block(b, profile)
            return

        # ── 3. Text Blocks ─────────────────────────────────────────────
        if b.block_type == BlockType.TEXT:
            self._classify_text_block(b, profile, y0_pct)
            return

        b.classification = "unknown"
        b.classification_reason = "Unrecognized block type."

    def _classify_table_block(
        self, b: ContentBlock, profile: DocumentProfile
    ) -> None:
        if not b.table_data or not b.table_data.rows:
            b.classification = "unknown"
            b.classification_reason = "Empty table."
            return

        rows = b.table_data.rows
        h_str = " ".join(c.text.lower() for c in rows[0].cells)

        # Check course/institution specification table
        if (
            len(rows) <= 3
            and ("course code" in h_str or "academic year" in h_str or "name of the course" in h_str)
        ):
            b.classification = "course_metadata"
            b.classification_reason = "Course and exam specification metadata table."
            return

        # Check reference tables (CO mapping, Bloom level mapping, weightage tables)
        if (
            ("co statement" in h_str)
            or ("co1" in h_str and "total" in h_str)
            or ("bloom" in h_str and "co" in h_str and len(rows) <= 6)
            or ("marks distribution" in h_str)
        ):
            b.classification = "reference_table"
            b.classification_reason = "Reference / Course Outcome mapping table."
            return

        # Check Question Bank Table (independent questions)
        if is_question_bank_table(b.table_data) or is_question_bank_table_html(b.table_data.html):
            b.classification = "question_bank_table"
            b.classification_reason = "Question-bank index table with candidate question rows."
            return

        # Genuine Question Content Table (dataframe, matrix, vectors)
        b.classification = "question_content_table"
        b.classification_reason = "Data table associated with question content."

    def _classify_text_block(
        self, b: ContentBlock, profile: DocumentProfile, y0_pct: float
    ) -> None:
        txt = b.text.strip()
        txt_lower = txt.lower()

        # Page Number check
        if PAGE_NUM_REGEX.match(txt) and (y0_pct < 0.12 or y0_pct > 0.88):
            b.classification = "page_number"
            b.classification_reason = "Page number format at page margin."
            return

        # Repeated Header/Footer check
        if (y0_pct < 0.15 and txt_lower in profile.repeated_headers) or (
            y0_pct > 0.85 and txt_lower in profile.repeated_footers
        ):
            b.classification = "header_footer"
            b.classification_reason = "Repeated text across pages."
            return

        # Question Group Heading (e.g. "Q.1 Answer the following:")
        if QUESTION_GROUP_REGEX.search(txt):
            b.classification = "question_group_heading"
            b.classification_reason = "Question group heading / overarching instruction."
            return

        # Unit Heading check
        m_unit = UNIT_HEADING_PATTERN.search(txt)
        if m_unit and len(txt) < 120 and ("unit" in txt_lower or "module" in txt_lower):
            b.classification = "unit_heading"
            b.classification_reason = "Matches unit / module heading format."
            b.notes = [m_unit.group(1)]
            return

        # Section Heading
        if SECTION_HEADING_REGEX.match(txt_lower):
            b.classification = "section_heading"
            b.classification_reason = "Section / Part heading."
            return

        # Section Instruction
        if SECTION_INSTRUCTION_REGEX.search(txt_lower):
            b.classification = "section_instruction"
            b.classification_reason = "Section-level instructions."
            return

        # Institution Metadata
        if b.page_number == 1 and y0_pct < 0.35 and INSTITUTION_REGEX.search(txt):
            b.classification = "institution_metadata"
            b.classification_reason = "College or university identification."
            return

        # Course Metadata
        if b.page_number in (1, 2) and COURSE_META_REGEX.search(txt) and len(txt) < 200:
            b.classification = "course_metadata"
            b.classification_reason = "Course code, academic year, or exam parameters."
            return

        # Document Title
        if b.page_number == 1 and ("question bank" in txt_lower or "question paper bank" in txt_lower) and len(txt) < 150:
            b.classification = "document_title"
            b.classification_reason = "Document title banner."
            return

        # Question Check
        if self._is_question(txt):
            b.classification = "question"
            b.classification_reason = "Matches question numbering format."
            return

        b.classification = "unknown"
        b.classification_reason = "Uncertain text block."

    def _is_question(self, text: str) -> bool:
        clean = text.strip()
        for p in QUESTION_PATTERNS:
            if p.match(clean):
                return True
        lines = [ln.strip() for ln in clean.split("\n") if ln.strip()]
        if len(lines) > 1:
            for idx in range(1, min(4, len(lines))):
                sub_text = "\n".join(lines[idx:])
                for p in QUESTION_PATTERNS:
                    if p.match(sub_text):
                        return True
        return False
