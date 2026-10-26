"""
Question segmenter — Intelligent Layout-Aware Filtering, Classification, and Segmentation.

Handles:
- Stage A-B integration with DocumentProfiler and BlockClassifier
- Stage C: Tabular question bank row extraction and multi-page continuation stitching
- Stage D: Automatic unit assignment, normalization (Unit 1..Unit 10), and single-unit fallback
- Stage E: Metadata extraction (CO, Bloom, Difficulty, Marks, Topic)
- Stage F: Preserving and binding genuine content tables, figures, images, and math
- Stage G: Confidence scoring and Review Mode A auto-acceptance
"""

from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from backend.schema import BlockType, ContentBlock, Geometry
from backend.services.document_profiler import DocumentProfile, DocumentProfiler
from backend.services.table_classifier import (
    is_question_bank_table,
    is_reference_table,
    extract_qb_table_rows,
    split_cell_questions,
    is_independent_questions,
    QUESTION_SPLIT_REGEX,
    QUESTION_VERBS,
)

logger = logging.getLogger(__name__)

QUESTION_PATTERNS = [
    re.compile(r"^\s*(?:(?:Q(?:uestion)?\.?\s*\d+[.)\]:]*)|(?:\d+[.)\]:]+))\s+\S", re.IGNORECASE),
    re.compile(r"^\s*(\d{1,4})\s*\n\s*[A-Z]", re.IGNORECASE),
    re.compile(r"^\s*Q(?:uestion)?\s*(\d{1,4})\s+[A-Z]", re.IGNORECASE),
    re.compile(r"^\s*(\d{1,4})\s+(?:" + "|".join(sorted(QUESTION_VERBS, key=len, reverse=True)) + r")\b", re.IGNORECASE),
    re.compile(r"^\s*\(([a-z])\)\s+\S"),
    re.compile(r"^\s*([a-z])[.)]\s+\S"),
    re.compile(r"^\s*\(([ivxlcdm]+)\)\s+\S", re.IGNORECASE),
]

GROUP_INSTRUCTION_PATTERNS = [
    re.compile(r"(?i)\bfor\s+questions?\s*(?:no\.?)?\s*(\d+)\s*(?:to|-)\s*(\d+)", re.IGNORECASE),
    re.compile(r"(?i)\bquestions?\s*(?:no\.?)?\s*(\d+)\s*(?:to|-)\s*(\d+)", re.IGNORECASE),
    re.compile(r"(?i)^(?:for\s+questions?\s+\d+.*|read\s+the\s+following.*and\s+answer.*|answer\s+the\s+following\s+questions?\s+based\s+on.*)", re.IGNORECASE),
]

CONTINUATION_BULLET_PATTERN = re.compile(r"^\s*[●•\-*▪▫–—]\s*")
CONTINUATION_ROMAN_PATTERN = re.compile(r"^\s*(?:[ivxlcdm]+|[IVXLCDM]+)[.)\]]\s*")
CONTINUATION_SUBPART_PATTERN = re.compile(r"^\s*(?:\([a-z]\)|[a-z][.)])\s*")
CONTINUATION_CODE_PATTERN = re.compile(
    r"(?i)\b(?:data\.frame|data-frame|ggplot|read\.csv|def\s+|class\s+|SELECT\s+|WHERE\s+|plot\(|summary\(|library\(|str\(|head\(|"
    r"residuals|coefficients|estimate\s+std|r-squared|f-statistic|p-value|signif\.\s+codes)\b|<-|="
)

DIFFICULTY_HEADING_PATTERNS = [
    (re.compile(r"^\s*(?:(?:section|part|level|difficulty)\s*[-:]?\s*)?(?:low\s+level\s+of\s+difficulty|easy(?:\s+level)?)\s*(?:\(\d+[%]?\)|\s*[-:]\s*\d+[%]?)?\s*$", re.IGNORECASE), "Easy", None),
    (re.compile(r"^\s*(?:(?:section|part|level|difficulty)\s*[-:]?\s*)?(?:moderate\s+level\s+of\s+difficulty|medium(?:\s+level)?|moderate)\s*(?:\(\d+[%]?\)|\s*[-:]\s*\d+[%]?)?\s*$", re.IGNORECASE), "Moderate", None),
    (re.compile(r"^\s*(?:(?:section|part|level|difficulty)\s*[-:]?\s*)?(?:higher\s+order\s+thinking\s+skills(?:\s*\(hots\))?|hots|high\s+level\s+of\s+difficulty|difficult(?:\s+level)?|hard)\s*(?:\(\d+[%]?\)|\s*[-:]\s*\d+[%]?)?\s*$", re.IGNORECASE), "Difficult", "HOTS"),
]


def _repair_ocr_text(t: str) -> str:
    t = re.sub(r'(?i)\bfoleasying\b', 'following', t)
    t = re.sub(r'(?i)\bbeeasy\b', 'below', t)
    return t

METADATA_PATTERNS = [
    re.compile(r"(?i)\bexam\s+duration\b"),
    re.compile(r"(?i)\btotal\s+marks\b"),
    re.compile(r"(?i)\bcourse\s+code\b"),
    re.compile(r"(?i)\bacademic\s+year\b"),
    re.compile(r"(?i)\bsemester\s*[-:\s]*\b"),
]

SECTION_HEADING_PATTERNS = [
    re.compile(r"(?i)^(?:part|section|module)\s+[a-z0-9]+$"),
]

SECTION_INSTRUCTION_PATTERNS = [
    re.compile(r"(?i)^(?:answer|attempt)\s+(?:any|all)\b"),
    re.compile(r"(?i)^note\s*[:\-]"),
]

UNIT_PATTERN = re.compile(
    r"(?i)\b(?:unit|module)\s*(?:no\.?)?\s*[-:]?\s*(?:(10|[1-9])(?!\d)|([IVXLCDMivxlcdm]+)\b)"
)


@dataclass
class QuestionCandidate:
    question_id: str
    block_id: str
    text: str
    page_number: int
    reading_order: int
    parent_id: str | None = None
    level: int = 1
    associated_blocks: list[str] = field(default_factory=list)
    uncertain: bool = False
    unit: str | None = None
    classification_source: str | None = None
    difficulty: str | None = None
    estimated_marks: float | None = None
    topic: str | None = None
    course_outcome: str | None = None
    bloom_level: str | None = None
    source_question_number: str | None = None
    confidence: float = 1.0
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)
    geometry: Geometry | None = None
    original_text: str | None = None
    is_repaired: bool = False
    repair_metadata: dict[str, Any] | None = None
    repaired_confidence: float | None = None
    extraction_method: str = "local"
    options: list[str] = field(default_factory=list)
    subquestions: list[str] = field(default_factory=list)


@dataclass
class SegmentationResult:
    questions: list[QuestionCandidate]
    unclassified_block_ids: list[str]
    warnings: list[str]
    unit_distribution: dict[str, int] = field(default_factory=dict)
    auto_accepted_count: int = 0
    review_needed_count: int = 0


def _roman_to_int(s: str) -> int:
    rom_val = {'i': 1, 'v': 5, 'x': 10, 'l': 50, 'c': 100, 'd': 500, 'm': 1000}
    int_val = 0
    s = s.lower().strip()
    for i in range(len(s)):
        if i > 0 and rom_val.get(s[i], 0) > rom_val.get(s[i - 1], 0):
            int_val += rom_val.get(s[i], 0) - 2 * rom_val.get(s[i - 1], 0)
        else:
            int_val += rom_val.get(s[i], 0)
    return int_val or 1


def normalize_unit_label(raw: str) -> str:
    """Normalize unit string (e.g. 'I', 'IV', '1', '10') into 'Unit 1'..'Unit 10'."""
    clean = raw.strip()
    m = re.search(r'\b([1-9]|10|[IVXLCDMivxlcdm]+)\b', clean)
    val = m.group(1) if m else clean
    if val.isdigit():
        return f"Unit {val}"
    else:
        return f"Unit {_roman_to_int(val)}"


def _clean_question_text(text: str) -> str:
    """Return text stripped of leading header noise if question pattern starts on subsequent lines."""
    clean = text.strip()
    for p in QUESTION_PATTERNS:
        if p.match(clean):
            return clean
    lines = [ln.strip() for ln in clean.split('\n') if ln.strip()]
    if len(lines) > 1:
        for idx in range(1, min(4, len(lines))):
            sub_text = '\n'.join(lines[idx:])
            for p in QUESTION_PATTERNS:
                if p.match(sub_text):
                    return sub_text
    return clean


def _is_question(text: str) -> bool:
    cleaned = _clean_question_text(text)
    for p in QUESTION_PATTERNS:
        if p.match(cleaned):
            return True
    return False


def _estimate_level(text: str) -> int:
    t = _clean_question_text(text).strip()
    if re.match(r"^\s*(?:Q\.?\s*)?\d+[.)\]]?\s+", t, re.IGNORECASE):
        return 1
    if re.match(r"^\s*\([ivxlcdm]+\)", t, re.IGNORECASE):
        return 3
    if re.match(r"^\s*\([a-z]\)", t) or re.match(r"^\s*[a-z][.)]\s+", t):
        return 2
    return 1


class QuestionSegmenter:
    """General Layout-Aware Filtering and Question Segmentation."""

    def segment(
        self,
        blocks: list[ContentBlock],
        profile: DocumentProfile | None = None
    ) -> SegmentationResult:
        if profile is None:
            profiler = DocumentProfiler()
            profile = profiler.profile(blocks)

        # ── Step 1: Detect Headers & Footers across pages ─────────────
        page_texts_top: dict[str, set[int]] = {}
        page_texts_bottom: dict[str, set[int]] = {}
        for b in blocks:
            if b.block_type == BlockType.TEXT and b.geometry and b.geometry.page_height > 0:
                y0_pct = b.geometry.y0 / b.geometry.page_height
                txt = b.text.strip().lower()
                if len(txt) > 3:
                    if y0_pct < 0.15:
                        page_texts_top.setdefault(txt, set()).add(b.page_number)
                    elif y0_pct > 0.85:
                        page_texts_bottom.setdefault(txt, set()).add(b.page_number)

        # ── Step 2: Classify blocks ────────────────────────────────────
        for b in blocks:
            if b.block_type == BlockType.TEXT:
                txt = b.text.strip()
                txt_lower = txt.lower()
                y0_pct = (
                    (b.geometry.y0 / b.geometry.page_height)
                    if (b.geometry and b.geometry.page_height > 0)
                    else 0.5
                )

                # Page number check
                if re.match(r"^(?:page\s*)?\d+(?:\s*/\s*\d+)?$", txt_lower) and (y0_pct < 0.15 or y0_pct > 0.85):
                    b.classification = "page_number"
                    b.classification_reason = "Matches page number format at top/bottom."
                    continue

                # Repeating header/footer
                if (y0_pct < 0.15 and len(page_texts_top.get(txt_lower, [])) > 1) or \
                   (y0_pct > 0.85 and len(page_texts_bottom.get(txt_lower, [])) > 1):
                    b.classification = "header_footer"
                    b.classification_reason = "Repeated text at top/bottom of multiple pages."
                    continue

                # Document metadata
                if any(p.search(txt_lower) for p in METADATA_PATTERNS):
                    b.classification = "metadata"
                    b.classification_reason = "Contains document metadata keywords."
                    continue

                # Question Group Instruction (parent instructions for a range of questions)
                if any(p.search(txt) for p in GROUP_INSTRUCTION_PATTERNS):
                    b.classification = "question_group_instruction"
                    b.classification_reason = "Parent instruction for upcoming group of questions."
                    continue

                # Unit Heading
                m_unit = UNIT_PATTERN.search(txt)
                if m_unit and len(txt) < 120 and ("unit" in txt_lower or "module" in txt_lower) and not re.search(r"\bquestion\s+bank\b|\bunits?\s*\d+\s*(?:,|and|-)\s*\d+", txt_lower):
                    b.classification = "unit_heading"
                    b.classification_reason = "Matches unit heading format."
                    b.notes = [m_unit.group(1) or m_unit.group(2)]
                    continue

                # Section Heading
                if any(p.match(txt_lower) for p in SECTION_HEADING_PATTERNS):
                    b.classification = "section_heading"
                    b.classification_reason = "Matches section heading format."
                    continue

                # Section Instruction
                if any(p.search(txt_lower) for p in SECTION_INSTRUCTION_PATTERNS):
                    b.classification = "section_instruction"
                    b.classification_reason = "Matches section instruction phrasing."
                    continue

                # Section Difficulty / Taxonomy Heading
                matched_diff = False
                for p, d_val, b_val in DIFFICULTY_HEADING_PATTERNS:
                    if p.search(txt_lower) and len(txt) < 80 and not _is_question(txt):
                        b.classification = "difficulty_heading"
                        b.classification_reason = f"Section difficulty heading: {d_val}"
                        b.notes = [d_val, b_val or ""]
                        matched_diff = True
                        break
                if matched_diff:
                    continue

                # Multi-question block detection
                m_splits = list(QUESTION_SPLIT_REGEX.finditer(txt))
                if is_independent_questions(m_splits, txt):
                    b.classification = "question"
                    b.classification_reason = "Block contains multiple independent candidate questions."
                    continue

                # Question / Subquestion
                if _is_question(txt):
                    b.classification = "question" if _estimate_level(txt) == 1 else "subquestion"
                    b.classification_reason = "Matches question numbering pattern."
                    continue

                # Question Continuation (bullet points, Roman numerals, code snippets, etc.)
                if (
                    CONTINUATION_BULLET_PATTERN.match(txt)
                    or CONTINUATION_ROMAN_PATTERN.match(txt)
                    or CONTINUATION_SUBPART_PATTERN.match(txt)
                    or CONTINUATION_CODE_PATTERN.search(txt)
                ):
                    b.classification = "question_continuation"
                    b.classification_reason = "Bulleted subpart, clause, or code snippet."
                    continue

                b.classification = "uncertain"
                b.classification_reason = "Could not definitively classify."

            elif b.block_type in (BlockType.IMAGE, BlockType.FIGURE):
                y0_pct = (
                    (b.geometry.y0 / b.geometry.page_height)
                    if (b.geometry and b.geometry.page_height > 0)
                    else 0.5
                )
                has_caption = bool(b.metadata.get("caption") if isinstance(b.metadata, dict) else False)
                is_cluster = bool(b.metadata.get("figure_cluster") if isinstance(b.metadata, dict) else False)
                has_preceding_question = any(
                    prev.page_number == 1 and prev.reading_order < b.reading_order and _is_question(prev.text)
                    for prev in blocks if prev.block_type == BlockType.TEXT and prev.text
                )
                if b.page_number == 1 and y0_pct < 0.15 and not has_preceding_question and not has_caption and not is_cluster:
                    b.classification = "decorative_image"
                    b.classification_reason = "Image at the top of the first page (likely logo)."
                else:
                    b.classification = "associated_table"
                    b.classification_reason = "Image/figure assumed to be part of question."

            elif b.block_type == BlockType.TABLE:
                if is_reference_table(b.table_data):
                    b.classification = "reference_table"
                    b.classification_reason = "Reference / Course Outcome mapping table."
                elif is_question_bank_table(b.table_data):
                    b.classification = "question_bank_table"
                    b.classification_reason = "Table is a question bank index containing candidate question rows."
                else:
                    b.classification = "associated_table"
                    b.classification_reason = "Table assumed to be part of question content."

            else:
                b.classification = "uncertain"
                b.classification_reason = "Unknown block type."

        # ── Step 3: Extract Questions & Associate Content ───────────────
        questions: list[QuestionCandidate] = []
        unclassified: list[str] = []
        warnings: list[str] = []
        parent_stack: list[QuestionCandidate] = []
        last_question_idx = -1
        current_unit: str | None = None
        current_difficulty: str | None = None
        current_bloom: str | None = None
        unit_distribution: dict[str, int] = {}
        active_group_instruction: dict | None = None
        content_tables: list[ContentBlock] = []
        visual_blocks: list[ContentBlock] = []

        for b in blocks:
            # 1. Unit Headings
            if b.classification == "unit_heading":
                raw_val = b.notes[0] if b.notes else ""
                if raw_val:
                    current_unit = normalize_unit_label(raw_val)
                    unit_distribution.setdefault(current_unit, 0)
                current_difficulty = None
                current_bloom = None
                continue

            # 2. Section Difficulty Headings (Low, Moderate, HOTS)
            if b.classification == "difficulty_heading":
                if b.notes:
                    current_difficulty = b.notes[0]
                    current_bloom = b.notes[1] if len(b.notes) > 1 and b.notes[1] else None
                continue

            # 2. Question Group Instructions
            if b.classification == "question_group_instruction":
                m_inst = re.search(
                    r'(?i)(?:for\s+questions?\s*(?:no\.?)?\s*(\d+)\s*(?:to|-)\s*(\d+)|use\s+.*?for\s+questions?\s*(?:no\.?)?\s*(\d+)\s*(?:to|-)\s*(\d+))',
                    b.text
                )
                start_q, end_q = None, None
                if m_inst:
                    g = [x for x in m_inst.groups() if x is not None]
                    if len(g) >= 2:
                        start_q, end_q = int(g[0]), int(g[1])
                active_group_instruction = {
                    "text": b.text.strip(),
                    "start": start_q,
                    "end": end_q
                }
                continue

            # 3. Text questions
            if b.classification in ["question", "subquestion"]:
                raw_text = _repair_ocr_text(b.text.strip())
                items = split_cell_questions(raw_text)

                # Handle leading prefix (e.g. continuation of previous question across block boundary)
                if items and items[0].get('prefix') and last_question_idx >= 0:
                    prefix_clean = items[0]['prefix'].strip()
                    if prefix_clean:
                        questions[last_question_idx].text = questions[last_question_idx].text.rstrip() + " " + prefix_clean

                for sub_idx, it in enumerate(items):
                    sub_text = it['text'].strip()
                    if not sub_text:
                        continue

                    # If single question with no prefix, preserve exact cleaned text
                    if len(items) == 1 and not it.get('prefix'):
                        cleaned_text = _clean_question_text(raw_text)
                    else:
                        cleaned_text = sub_text

                    # Extract serial number prefix if present
                    m_sr = re.match(r"^(?:(?:Q(?:uestion)?\.?\s*)(\d+)[.)\]:-]?\s+|(\d+)[.)\]:-]\s+)", cleaned_text, re.IGNORECASE)
                    sr_no = it.get('serial_no') or ((m_sr.group(1) or m_sr.group(2)) if m_sr else None)

                    # Ensure cleaned_text has serial prefix if sr_no was extracted from regex outside the text
                    if sr_no and not re.match(r"^(?:(?:Q(?:uestion)?\.?\s*)?\d+[.)\]:-]\s+)", cleaned_text, re.IGNORECASE):
                        if str(sr_no).isalpha():
                            cleaned_text = f"({sr_no}) {cleaned_text}"
                        else:
                            cleaned_text = f"{sr_no}. {cleaned_text}"

                    level = _estimate_level(cleaned_text)

                    # Prepend active group instruction if within range
                    if active_group_instruction and sr_no and str(sr_no).isdigit():
                        q_num = int(sr_no)
                        if active_group_instruction["start"] and active_group_instruction["end"]:
                            if active_group_instruction["start"] <= q_num <= active_group_instruction["end"]:
                                inst_t = active_group_instruction["text"]
                                if inst_t not in cleaned_text:
                                    cleaned_text = f"[{inst_t}]\n{cleaned_text}"

                    # Extract bracketed metadata if present: e.g. [3 Marks] [Easy] [CO1] [BL2]
                    est_marks = None
                    m_marks = re.search(r"(?:\[|\()(\d+(?:\.\d+)?)\s*(?:m(?:arks)?|mk|pts?)(?:\]|\))", cleaned_text, re.IGNORECASE)
                    if m_marks:
                        try:
                            est_marks = float(m_marks.group(1))
                        except ValueError:
                            est_marks = None

                    diff_val = None
                    for d in ["Easy", "Moderate", "Difficult", "Medium", "Hard"]:
                        if re.search(rf"\b{d}\b", cleaned_text, re.IGNORECASE):
                            diff_val = d.capitalize()
                            break
                    if not diff_val and current_difficulty:
                        diff_val = current_difficulty

                    bloom_val = None
                    m_bl = re.search(r"\[?(?:BL\s*([1-6])|Bloom(?:\s*Taxonomy)?[:\s]+(\w+))\]?", cleaned_text, re.IGNORECASE)
                    if m_bl:
                        bloom_val = f"BL{m_bl.group(1)}" if m_bl.group(1) else m_bl.group(2)
                    if not bloom_val and current_bloom:
                        bloom_val = current_bloom

                    co_val = None
                    m_co = re.search(r"\[?\bCO[-_]?\s*([1-6])\b\]?", cleaned_text, re.IGNORECASE)
                    if m_co:
                        co_val = f"CO{m_co.group(1)}"

                    q_id_suffix = f"_{sr_no}" if sr_no else (f"_{sub_idx}" if len(items) > 1 else "")
                    q = QuestionCandidate(
                        question_id=f"q_{b.block_id[:8]}{q_id_suffix}",
                        block_id=b.block_id,
                        text=cleaned_text,
                        page_number=b.page_number,
                        reading_order=b.reading_order,
                        level=level,
                        unit=current_unit,
                        classification_source="heading_boundary" if current_unit else None,
                        difficulty=diff_val,
                        estimated_marks=est_marks,
                        bloom_level=bloom_val,
                        course_outcome=co_val,
                        source_question_number=str(sr_no) if sr_no else None,
                        geometry=b.geometry,
                    )

                    # Confidence calculation
                    boundary_conf = 1.0 if level == 1 else 0.85
                    unit_conf = 1.0 if current_unit else 0.0
                    meta_conf = 1.0 if (diff_val or bloom_val or est_marks) else 0.8
                    q.confidence = round(boundary_conf * 0.5 + unit_conf * 0.4 + meta_conf * 0.1, 2)

                    if current_unit:
                        unit_distribution[current_unit] = unit_distribution.get(current_unit, 0) + 1
                    else:
                        q.uncertain = True
                        q.needs_review = True
                        q.review_reasons.append("No unit heading found before this question.")
                        if "No unit heading found before this question." not in warnings:
                            warnings.append("No unit heading found before this question.")

                    if len(cleaned_text.split()) < 3:
                        q.uncertain = True
                        q.needs_review = True
                        q.review_reasons.append("Question statement is unusually short.")

                    if level > 1 and parent_stack:
                        for ancestor in reversed(parent_stack):
                            if ancestor.level < level:
                                q.parent_id = ancestor.question_id
                                break
                        else:
                            q.uncertain = True

                    parent_stack = [p for p in parent_stack if p.level < level]
                    parent_stack.append(q)
                    questions.append(q)
                    last_question_idx = len(questions) - 1

            # 4. Continuation Text Blocks (Problem 1)
            elif b.block_type == BlockType.TEXT and last_question_idx >= 0 and b.classification in ("question_continuation", "uncertain"):
                prev_q = questions[last_question_idx]
                can_merge = False
                if b.page_number in (prev_q.page_number, prev_q.page_number + 1):
                    txt = b.text.strip()
                    if (
                        CONTINUATION_BULLET_PATTERN.match(txt)
                        or CONTINUATION_ROMAN_PATTERN.match(txt)
                        or CONTINUATION_SUBPART_PATTERN.match(txt)
                        or CONTINUATION_CODE_PATTERN.search(txt)
                        or prev_q.text.rstrip().endswith((":", ",", "(", "OR", "and", "the", "-"))
                        or (b.geometry and prev_q.geometry and prev_q.page_number == b.page_number and b.geometry.y0 - prev_q.geometry.y1 < 40)
                    ):
                        can_merge = True

                if can_merge:
                    prev_q.text = prev_q.text.rstrip() + "\n" + _repair_ocr_text(b.text.strip())
                    prev_q.associated_blocks.append(b.block_id)
                    b.classification = "question_continuation"
                    if b.geometry:
                        if prev_q.geometry and b.page_number == prev_q.page_number:
                            prev_q.geometry.x0 = min(prev_q.geometry.x0, b.geometry.x0)
                            prev_q.geometry.y0 = min(prev_q.geometry.y0, b.geometry.y0)
                            prev_q.geometry.x1 = max(prev_q.geometry.x1, b.geometry.x1)
                            prev_q.geometry.y1 = max(prev_q.geometry.y1, b.geometry.y1)
                        elif not prev_q.geometry:
                            prev_q.geometry = b.geometry
                else:
                    unclassified.append(b.block_id)

            # 5. Question Bank Tables
            elif b.classification == "question_bank_table":
                table_rows = extract_qb_table_rows(b.table_data, context_unit=current_unit)
                if isinstance(b.table_data, dict) and b.table_data.get('_detected_unit'):
                    current_unit = b.table_data['_detected_unit']
                    unit_distribution.setdefault(current_unit, 0)
                for r_idx, r in enumerate(table_rows):
                    q_text = _repair_ocr_text(r['question_text'])
                    sr_no = r.get('serial_no')
                    row_unit = r.get('unit') or current_unit
                    if r.get('unit'):
                        current_unit = r['unit']
                        unit_distribution.setdefault(current_unit, 0)

                    display_text = f"{sr_no}. {q_text}" if sr_no else q_text

                    # Check multi-page table continuation for unnumbered first row
                    if r_idx == 0 and not sr_no and last_question_idx >= 0:
                        prev_q = questions[last_question_idx]
                        if prev_q.unit == row_unit and b.page_number in (prev_q.page_number, prev_q.page_number + 1):
                            next_sr = table_rows[1].get('serial_no') if len(table_rows) > 1 else None
                            m_prev = re.match(r"^(\d+)", prev_q.source_question_number or "")
                            m_next = re.match(r"^(\d+)", next_sr or "")
                            is_continuation = True
                            if m_prev and m_next:
                                p_num = int(m_prev.group(1))
                                n_num = int(m_next.group(1))
                                if n_num == p_num + 2:
                                    sr_no = str(p_num + 1)
                                    display_text = f"{sr_no}. {q_text}"
                                    is_continuation = False
                            if is_continuation:
                                prev_q.text = prev_q.text.rstrip() + "\n" + q_text
                                prev_q.associated_blocks.append(b.block_id)
                                if not prev_q.difficulty and r.get('difficulty'):
                                    prev_q.difficulty = r.get('difficulty')
                                continue

                    # Deduplication check: only deduplicate if from same block OR text is genuinely similar
                    already_present = False
                    for existing_q in questions:
                        if existing_q.block_id == b.block_id and existing_q.source_question_number == sr_no:
                            already_present = True
                            break
                        if sr_no and existing_q.unit == row_unit and existing_q.source_question_number == sr_no:
                            row_diff = r.get('difficulty') or current_difficulty
                            if existing_q.difficulty and row_diff and existing_q.difficulty.lower() != row_diff.lower():
                                continue
                            if abs(existing_q.page_number - b.page_number) > 1:
                                continue
                            w1 = set(re.findall(r"\b[a-z]{3,}\b", existing_q.text.lower()))
                            w2 = set(re.findall(r"\b[a-z]{3,}\b", q_text.lower()))
                            if w1 and w2 and ((len(w1 & w2) / len(w1 | w2) > 0.6) or (len(w1 & w2) / min(len(w1), len(w2)) > 0.8 and min(len(w1), len(w2)) >= 5)):
                                already_present = True
                                if len(q_text) > len(existing_q.text):
                                    existing_q.text = display_text
                                    existing_q.block_id = b.block_id
                                    existing_q.confidence = max(existing_q.confidence, 0.95)
                                if not existing_q.difficulty and r.get('difficulty'):
                                    existing_q.difficulty = r.get('difficulty')
                                if not existing_q.estimated_marks and r.get('marks'):
                                    existing_q.estimated_marks = r.get('marks')
                                if not existing_q.topic and r.get('topic'):
                                    existing_q.topic = r.get('topic')
                                if not existing_q.course_outcome and r.get('course_outcome'):
                                    existing_q.course_outcome = r.get('course_outcome')
                                if not existing_q.bloom_level and r.get('bloom_level'):
                                    existing_q.bloom_level = r.get('bloom_level')
                                break

                    if not already_present and len(q_text) >= 5:
                        boundary_conf = 1.0 if sr_no else 0.85
                        unit_conf = 1.0 if row_unit else 0.0
                        meta_conf = 1.0 if (r.get('difficulty') or r.get('bloom_level') or r.get('marks')) else 0.8
                        confidence = round(boundary_conf * 0.5 + unit_conf * 0.4 + meta_conf * 0.1, 2)

                        row_geom = None
                        r_bbox = r.get('bbox')
                        if r_bbox:
                            row_geom = Geometry(
                                x0=r_bbox[0],
                                y0=r_bbox[1],
                                x1=r_bbox[2],
                                y1=r_bbox[3],
                                page_width=b.geometry.page_width if b.geometry else 595.0,
                                page_height=b.geometry.page_height if b.geometry else 842.0,
                            )
                        elif b.geometry and len(table_rows) > 0:
                            frac_top = r_idx / len(table_rows)
                            frac_bot = (r_idx + 1) / len(table_rows)
                            h = b.geometry.y1 - b.geometry.y0
                            row_geom = Geometry(
                                x0=b.geometry.x0,
                                y0=b.geometry.y0 + frac_top * h,
                                x1=b.geometry.x1,
                                y1=b.geometry.y0 + frac_bot * h,
                                page_width=b.geometry.page_width,
                                page_height=b.geometry.page_height,
                            )
                        else:
                            row_geom = b.geometry

                        q_cand = QuestionCandidate(
                            question_id=f"q_{b.block_id[:6]}_{r_idx}_{sr_no or 'sub'}",
                            block_id=b.block_id,
                            text=display_text,
                            page_number=b.page_number,
                            reading_order=b.reading_order + (r_idx * 0.01),
                            level=1,
                            unit=row_unit,
                            classification_source="table_row",
                            difficulty=r.get('difficulty') or current_difficulty,
                            estimated_marks=r.get('marks'),
                            topic=r.get('topic'),
                            course_outcome=r.get('course_outcome'),
                            bloom_level=r.get('bloom_level'),
                            source_question_number=sr_no,
                            confidence=confidence,
                            geometry=row_geom,
                        )

                        if row_unit:
                            unit_distribution[row_unit] = unit_distribution.get(row_unit, 0) + 1
                        else:
                            q_cand.uncertain = True
                            q_cand.needs_review = True
                            q_cand.review_reasons.append("No unit assigned to question.")

                        questions.append(q_cand)
                        last_question_idx = len(questions) - 1

            # 6. Visuals (Images/Figures) - collect for context & caption-aware association
            elif b.block_type in (BlockType.IMAGE, BlockType.FIGURE):
                if b.classification != "decorative_image":
                    visual_blocks.append(b)

            # 7. Collect Content Tables to associate in Step 4
            elif b.block_type == BlockType.TABLE and b.classification in ("associated_table", "question_content_table"):
                content_tables.append(b)

            elif b.classification == "uncertain":
                unclassified.append(b.block_id)

        # ── Helper: compute question vertical layout zones per page ──────
        def _get_page_question_zones(qs: list[QuestionCandidate], target_page: int) -> list[tuple[QuestionCandidate, float, float]]:
            """
            Returns a list of (question, zone_top, zone_bottom) for all questions on target_page,
            sorted from top to bottom.
            """
            page_qs = [q for q in qs if q.page_number == target_page and q.geometry and q.geometry.y1 > q.geometry.y0]
            if not page_qs:
                return []
            page_qs.sort(key=lambda q: (q.geometry.y0, q.geometry.x0 if q.geometry else 0.0))
            zones = []
            for idx, q in enumerate(page_qs):
                z_top = q.geometry.y0
                if idx + 1 < len(page_qs):
                    next_q = page_qs[idx + 1]
                    z_bot = next_q.geometry.y0
                    if z_bot <= z_top:
                        z_bot = max(z_top + 10.0, q.geometry.y1)
                else:
                    z_bot = max(q.geometry.y1, q.geometry.page_height or 99999.0)
                zones.append((q, z_top, z_bot))
            return zones

        # ── Step 4: Associate Genuine Content Tables ─────────────────────
        table_cues = [
            'following table', 'as below', 'table below', 'refer to the table',
            'refer to table', 'given below', 'matrix', 'data frame', 'data beeasy',
            'outputs', 'residuals', 'dispersion', 'regression model', 'from the table',
            'table shown', 'given in table'
        ]

        for ct in content_tables:
            headers = []
            sample_cells = []
            if ct.table_data and ct.table_data.rows:
                headers = [
                    re.sub(r'\s+', ' ', c.text.strip().lower())
                    for c in ct.table_data.rows[0].cells
                    if c.text and len(c.text.strip()) > 1
                ]
                if len(ct.table_data.rows) > 1:
                    sample_cells = [
                        re.sub(r'\s+', ' ', c.text.strip().lower())
                        for c in ct.table_data.rows[1].cells
                        if c.text and len(c.text.strip()) > 2
                    ]

            matched_q = None
            best_score = -1.0

            same_page_qs = [q for q in questions if q.page_number == ct.page_number]
            page_zones = _get_page_question_zones(questions, ct.page_number)
            zone_map = {q.question_id: (z_top, z_bot) for q, z_top, z_bot in page_zones}
            preceding_qs = [q for q in same_page_qs if q.reading_order < ct.reading_order]

            for q in same_page_qs:
                score = 0.0
                q_low = q.text.lower()

                # Text cues bonus
                for cue in table_cues:
                    if cue in q_low:
                        score += 3.0
                        break
                for h in headers:
                    if len(h) >= 3 and h in q_low:
                        score += 2.5
                for sc in sample_cells[:3]:
                    if len(sc) >= 3 and sc in q_low:
                        score += 1.0

                # Spatial geometry (if available)
                if ct.geometry and q.question_id in zone_map:
                    z_top, z_bot = zone_map[q.question_id]
                    ct_y0 = ct.geometry.y0
                    ct_y1 = ct.geometry.y1
                    if q in preceding_qs:
                        if z_top - 12 <= ct_y0 < z_bot:
                            score += 6.0
                            if ct_y1 <= z_bot + 15:
                                score += 3.0
                    if ct_y1 < z_top - 15:
                        score -= 15.0
                    elif ct_y0 >= z_bot:
                        score -= 15.0

                # Reading order relationship
                if q in preceding_qs:
                    if q == preceding_qs[-1]:
                        score += 2.5 if (not zone_map or not q.geometry) else 1.5
                    else:
                        score += 0.5
                else:
                    score -= 3.0

                if score > best_score:
                    best_score = score
                    matched_q = q

            # Multi-page transition: question at bottom of page P, table at top of page P+1
            if (not matched_q or best_score < 1.0) and ct.page_number > 1 and ct.geometry and ct.geometry.y0 < 180:
                prev_page_qs = [q for q in questions if q.page_number == ct.page_number - 1]
                if prev_page_qs:
                    candidate_prev_q = prev_page_qs[-1]
                    if any(cue in candidate_prev_q.text.lower() for cue in table_cues) or candidate_prev_q.text.rstrip().endswith((":", "table")):
                        matched_q = candidate_prev_q
                        best_score = 4.0

            if matched_q and best_score >= 2.0:
                if ct.block_id not in matched_q.associated_blocks:
                    matched_q.associated_blocks.append(ct.block_id)

                # Clean question prompt text if it duplicates raw table dump text
                if headers:
                    lines = matched_q.text.split('\n')
                    clean_lines = []
                    for line in lines:
                        line_norm = re.sub(r'\s+', ' ', line.strip().lower())
                        if clean_lines and (
                            sum(1 for h in headers if h in line_norm) >= 2 or
                            sum(1 for w in ['high-income', 'easy-income', 'standard deviation', 'revenue', 'profits', 'prn no.', 'fees paid', 'bank account', 'percentage', 'class', 'residuals:'] if w in line_norm) >= 2
                        ):
                            break
                        clean_lines.append(line)
                    clean_prompt = '\n'.join(clean_lines).strip()
                    if len(clean_prompt) >= 15:
                        matched_q.text = clean_prompt
            else:
                unclassified.append(ct.block_id)

        # ── Step 5: Associate Visuals (Images/Figures) ────────────────────
        visual_cues = [
            'shown in figure', 'shown in fig', 'following figure', 'following diagram',
            'refer to figure', 'refer to the figure', 'refer figure', 'as shown in',
            'following neural network', 'in the figure', 'given figure', 'following ann',
            'from the diagram', 'from the figure', 'diagram below', 'figure below',
            'circuit shown', 'graph shown', 'plot shown', 'weights shown', 'network shown'
        ]

        for vb in visual_blocks:
            caption = (vb.metadata.get("caption") or "").strip() if vb.metadata else ""
            caption_matched = False

            # 1. Explicit Caption Matching (e.g. "Fig 1.1", "Figure 2", "Diagram 3")
            if caption:
                m_cap = re.match(r"^(?:Fig(?:ure)?\.?\s*([\d.]+)|Diagram\s*([\d.]+)|Graph\s*([\d.]+)|Model\s*([\d.]+)|Plot\s*([\d.]+))", caption, re.I)
                if m_cap:
                    cap_num = next(g for g in m_cap.groups() if g is not None)
                    cap_pat = re.compile(rf"(?:Fig(?:ure)?|Diagram|Graph|Model|Plot)\.?\s*{re.escape(cap_num)}\b", re.IGNORECASE)
                    for q in questions:
                        if cap_pat.search(q.text):
                            if vb.block_id not in q.associated_blocks:
                                q.associated_blocks.append(vb.block_id)
                            caption_matched = True

            if caption_matched:
                continue

            # Check if questions explicitly group-refer to a visual: e.g. "For questions 3 and 4 refer to figure"
            group_matched = False
            for q in questions:
                if q.page_number == vb.page_number:
                    m_grp = re.search(r"questions?\s*(\d+)\s*(?:and|to|-|,)\s*(\d+)", q.text, re.I)
                    if m_grp and any(cue in q.text.lower() for cue in visual_cues):
                        q_start, q_end = int(m_grp.group(1)), int(m_grp.group(2))
                        for target_q in questions:
                            try:
                                q_num = int(re.sub(r"\D", "", target_q.source_question_number or ""))
                                if q_start <= q_num <= q_end:
                                    if vb.block_id not in target_q.associated_blocks:
                                        target_q.associated_blocks.append(vb.block_id)
                                    group_matched = True
                            except (ValueError, TypeError):
                                pass

            if group_matched:
                continue

            # 2. Contextual & Spatial Layout Association
            same_page_qs = [q for q in questions if q.page_number == vb.page_number]
            page_zones = _get_page_question_zones(questions, vb.page_number)
            zone_map = {q.question_id: (z_top, z_bot) for q, z_top, z_bot in page_zones}
            preceding_qs = [q for q in same_page_qs if q.reading_order < vb.reading_order]

            best_q = None
            best_score = -1.0

            for q in same_page_qs:
                score = 0.0
                q_low = q.text.lower()

                # Visual cues bonus
                for cue in visual_cues:
                    if cue in q_low:
                        score += 3.5
                        break

                # Spatial geometry (if available)
                if vb.geometry and q.question_id in zone_map:
                    z_top, z_bot = zone_map[q.question_id]
                    vb_y0 = vb.geometry.y0
                    vb_y1 = vb.geometry.y1
                    if q in preceding_qs:
                        if z_top - 12 <= vb_y0 < z_bot:
                            score += 6.0
                            if vb_y1 <= z_bot + 15:
                                score += 3.0
                    if vb_y1 < z_top - 15:
                        score -= 15.0
                    elif vb_y0 >= z_bot:
                        score -= 15.0

                # Reading order relationship
                if q in preceding_qs:
                    if q == preceding_qs[-1]:
                        score += 2.5 if (not zone_map or not q.geometry) else 1.5
                    else:
                        score += 0.5
                else:
                    score -= 3.0

                if score > best_score:
                    best_score = score
                    best_q = q

            # 3. Multi-page question transition (question at bottom of page P, visual at top of page P+1)
            if (not best_q or best_score < 1.0) and vb.page_number > 1 and vb.geometry and vb.geometry.y0 < 180:
                prev_page_qs = [q for q in questions if q.page_number == vb.page_number - 1]
                if prev_page_qs:
                    candidate_prev_q = prev_page_qs[-1]
                    prev_low = candidate_prev_q.text.lower()
                    if any(cue in prev_low for cue in visual_cues) or candidate_prev_q.text.rstrip().endswith((":", "figure", "diagram")):
                        best_q = candidate_prev_q
                        best_score = 4.5

            # Associate if confident
            if best_q and best_score >= 2.0:
                if vb.block_id not in best_q.associated_blocks:
                    best_q.associated_blocks.append(vb.block_id)
                # If marginal score, flag question for review
                if best_score < 3.5:
                    if not any("visual asset" in r.lower() for r in best_q.review_reasons):
                        best_q.review_reasons.append("Associated visual asset should be verified against source page.")
                        best_q.needs_review = True
            else:
                # Do NOT arbitrarily force onto questions[-1]!
                unclassified.append(vb.block_id)

        # ── Step 4b: Single-Unit Document Fallback ───────────────────────
        assigned_units = {q.unit for q in questions if q.unit}
        if len(assigned_units) == 0 and profile and profile.detected_units and len(profile.detected_units) == 1:
            fallback_unit = profile.detected_units[0]
            for q in questions:
                q.unit = fallback_unit
                q.uncertain = False
                q.needs_review = False
                q.confidence = 0.95
                q.review_reasons = []
            unit_distribution[fallback_unit] = len(questions)

        # ── Step 6: Self-Validation & Quality Layer ───────────────────────
        # Note: Unassociated assets remain unclassified and are not arbitrarily dumped onto questions[-1]
        all_associated_blocks = set()
        for q in questions:
            all_associated_blocks.update(q.associated_blocks)

        # 2. Hanging / Truncated Question Detection & Quality Flags
        DANGLING_ENDINGS = (':', ',', '(', 'or', 'and', 'the', 'with', 'for', 'in', 'to', 'of', '-', '/', 'between', 'among', 'using', 'by', 'from')
        seen_texts = set()
        for q in questions:
            clean_q = q.text.strip()
            # Unusually short statement (< 3 words)
            if len(clean_q.split()) < 3 and not any("short" in r.lower() for r in q.review_reasons):
                q.uncertain = True
                q.needs_review = True
                q.review_reasons.append("Question statement is unusually short (< 3 words).")

            # Ends with dangling conjunction/preposition without content table or visual
            last_word = clean_q.split()[-1].lower().strip(".,:;\"'?") if clean_q.split() else ""
            is_dangling = clean_q.endswith(('-', ':', ',', '(', '/')) or (last_word in DANGLING_ENDINGS)
            if is_dangling and not q.associated_blocks:
                if not any("truncated" in r.lower() or "dangling" in r.lower() for r in q.review_reasons):
                    q.needs_review = True
                    q.uncertain = True
                    q.review_reasons.append("Question appears truncated or ends with dangling conjunction/preposition.")

            # Duplicate question in same unit check
            text_norm = re.sub(r"^(?:(?:Q(?:uestion)?\.?\s*)?\d+[.)\]:-]?\s+)", "", clean_q, flags=re.IGNORECASE).strip().lower()
            key = (q.unit, text_norm)
            if len(text_norm) > 10:
                if key in seen_texts:
                    if not any("duplicate" in r.lower() for r in q.review_reasons):
                        q.needs_review = True
                        q.review_reasons.append("Potential duplicate question detected in the same unit.")
                else:
                    seen_texts.add(key)

        # ── Step 7: Summary Metrics ─────────────────────────────────────
        auto_accepted = sum(1 for q in questions if not q.needs_review)
        review_needed = sum(1 for q in questions if q.needs_review)

        return SegmentationResult(
            questions=questions,
            unclassified_block_ids=unclassified,
            warnings=warnings,
            unit_distribution=unit_distribution,
            auto_accepted_count=auto_accepted,
            review_needed_count=review_needed,
        )

