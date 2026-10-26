"""
Document Profiler Service — Stage A.

Examines the whole document before final question extraction to produce a comprehensive
structural and metadata profile. Identifies:
- Document title, type, and layout strategy
- Institute / College name
- Course name, course code, academic year, semester, credits, exam duration
- Repeated headers/footers, page numbers, and logos
- Unit/module structure (multi-unit vs single-unit)
- Question numbering conventions
- Table types (question-bank index vs reference vs content tables)
- Metadata column availability (CO, Bloom, Difficulty, Marks, Unit, Topic)
- Scanned pages requiring OCR
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from backend.schema import BlockType, ContentBlock, SourceFormat
from backend.services.table_classifier import _extract_raw_rows


INSTITUTION_KEYWORDS = [
    r"(?i)\bcollege\s+of\s+engineering\b",
    r"(?i)\buniversity\b",
    r"(?i)\binstitute\s+of\s+technology\b",
    r"(?i)\bautonomous\s+institute\b",
    r"(?i)\beducation\s+trust\b",
    r"(?i)\bpolytechnic\b",
    r"(?i)\bdepartment\s+of\b",
]

COURSE_CODE_PATTERNS = [
    re.compile(r"(?i)(?:course|subject)\s*code\s*[:\s-]*([A-Z0-9]{5,10})"),
    re.compile(r"\b([A-Z]{2,4}\d{2}[A-Z]{2}\d{2})\b"),  # e.g., BSH23EM01, BSH23OE04
]

ACADEMIC_YEAR_PATTERNS = [
    re.compile(r"(?i)academic\s+year\s*[:\s-]*(\d{4}\s*[-–/]\s*\d{2,4})"),
    re.compile(r"\b(20\d{2}\s*[-–/]\s*20?\d{2})\b"),
]

SEMESTER_PATTERNS = [
    re.compile(r"(?i)semester\s*[:\s-]*([IVXLCDMivxlcdm]+|\d+)"),
    re.compile(r"(?i)\bsem(?:ester)?\s*[-:\s]*([IVXLCDMivxlcdm]+|\d+)"),
]

UNIT_HEADING_PATTERN = re.compile(
    r"(?i)\b(?:unit|module)\s*(?:no\.?)?\s*[-:]?\s*([IVXLCDMivxlcdm]+|\d+)\b"
)


@dataclass
class DocumentProfile:
    document_title: str = ""
    doc_type: str = "unknown"  # "question_bank_tabular", "question_bank_list", "question_paper", "reference"
    institution_name: str | None = None
    course_name: str | None = None
    course_code: str | None = None
    academic_year: str | None = None
    semester: str | None = None
    programme: str | None = None
    credits: str | None = None
    exam_duration: str | None = None
    total_marks: float | None = None
    has_multiple_units: bool = False
    detected_units: list[str] = field(default_factory=list)
    question_numbering_style: str = "unknown"  # "numeric", "q_prefix", "alpha", "table_sr_no"
    table_stats: dict[str, int] = field(default_factory=dict)
    metadata_columns: list[str] = field(default_factory=list)
    repeated_headers: list[str] = field(default_factory=list)
    repeated_footers: list[str] = field(default_factory=list)
    is_scanned: bool = False
    total_pages: int = 1
    summary_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_title": self.document_title,
            "doc_type": self.doc_type,
            "institution_name": self.institution_name,
            "course_name": self.course_name,
            "course_code": self.course_code,
            "academic_year": self.academic_year,
            "semester": self.semester,
            "programme": self.programme,
            "credits": self.credits,
            "exam_duration": self.exam_duration,
            "total_marks": self.total_marks,
            "has_multiple_units": self.has_multiple_units,
            "detected_units": self.detected_units,
            "question_numbering_style": self.question_numbering_style,
            "table_stats": self.table_stats,
            "metadata_columns": self.metadata_columns,
            "repeated_headers": self.repeated_headers,
            "repeated_footers": self.repeated_footers,
            "is_scanned": self.is_scanned,
            "total_pages": self.total_pages,
            "summary_notes": self.summary_notes,
        }


class DocumentProfiler:
    """Analyzes all extracted blocks of a document to establish global context."""

    def profile(
        self,
        blocks: list[ContentBlock],
        source_format: SourceFormat = SourceFormat.PDF,
        filename: str = "",
    ) -> DocumentProfile:
        profile = DocumentProfile()
        if not blocks:
            profile.summary_notes.append("Empty document.")
            return profile

        pages = {b.page_number for b in blocks if b.page_number}
        profile.total_pages = max(pages) if pages else 1

        # ── 1. Repeated Header / Footer Analysis ──────────────────────
        self._analyze_headers_and_footers(blocks, profile)

        # ── 2. Institution, Course, Academic Metadata ─────────────────
        self._extract_institutional_metadata(blocks, profile, filename)

        # ── 3. Table Structure & Columns ──────────────────────────────
        self._analyze_tables(blocks, profile)

        # ── 4. Unit / Module Detection ────────────────────────────────
        self._detect_units(blocks, profile, filename)

        # ── 5. Question Numbering & Document Type ─────────────────────
        self._determine_document_type(blocks, profile)

        return profile

    def _analyze_headers_and_footers(
        self, blocks: list[ContentBlock], profile: DocumentProfile
    ) -> None:
        """Find repeated text strings across pages in top (y0 < 0.15) or bottom (y0 > 0.85)."""
        top_map: dict[str, set[int]] = {}
        bottom_map: dict[str, set[int]] = {}

        for b in blocks:
            if b.block_type == BlockType.TEXT and b.geometry and b.geometry.page_height > 0:
                y0_pct = b.geometry.y0 / b.geometry.page_height
                txt = b.text.strip().lower()
                if len(txt) > 5 and "\n" not in txt:
                    if y0_pct < 0.15:
                        top_map.setdefault(txt, set()).add(b.page_number)
                    elif y0_pct > 0.85:
                        bottom_map.setdefault(txt, set()).add(b.page_number)

        profile.repeated_headers = [
            txt for txt, pages in top_map.items() if len(pages) >= 2
        ]
        profile.repeated_footers = [
            txt for txt, pages in bottom_map.items() if len(pages) >= 2
        ]

    def _extract_institutional_metadata(
        self, blocks: list[ContentBlock], profile: DocumentProfile, filename: str
    ) -> None:
        """Search text and table metadata cells for course, college, year, semester."""
        all_text_p1_p2 = []
        for b in blocks:
            if b.page_number in (1, 2):
                if b.block_type == BlockType.TEXT and b.text:
                    all_text_p1_p2.append(b.text)
                elif b.block_type == BlockType.TABLE and b.table_data:
                    rows = b.table_data.rows if hasattr(b.table_data, 'rows') else (b.table_data.get('rows', []) if isinstance(b.table_data, dict) else [])
                    for row in rows:
                        cells = row.cells if hasattr(row, 'cells') else (row.get('cells', []) if isinstance(row, dict) else row)
                        row_texts = []
                        for c in cells:
                            txt = c.text.strip() if hasattr(c, 'text') else (c.get('text', '').strip() if isinstance(c, dict) else str(c).strip())
                            if txt:
                                row_texts.append(txt)
                        if row_texts:
                            all_text_p1_p2.append(" | ".join(row_texts))

        combined_early = "\n".join(all_text_p1_p2)

        # Institution Name
        for pattern in INSTITUTION_KEYWORDS:
            m = re.search(pattern, combined_early)
            if m:
                # Capture the line
                for line in combined_early.split("\n"):
                    if re.search(pattern, line):
                        profile.institution_name = line.strip(" |:-")[:120]
                        break
                if profile.institution_name:
                    break

        # Check structured table cells on page 1-2
        for b in blocks:
            if b.page_number in (1, 2) and b.block_type == BlockType.TABLE and b.table_data:
                rows = b.table_data.rows if hasattr(b.table_data, 'rows') else (b.table_data.get('rows', []) if isinstance(b.table_data, dict) else [])
                if len(rows) >= 2:
                    first_row_cells = rows[0].cells if hasattr(rows[0], 'cells') else (rows[0].get('cells', []) if isinstance(rows[0], dict) else rows[0])
                    h_cells = [
                        (c.text.strip().lower() if hasattr(c, 'text') else (c.get('text', '').strip().lower() if isinstance(c, dict) else str(c).strip().lower()))
                        for c in first_row_cells
                    ]
                    # Only proceed if table is a course specification table
                    if not (any("course" in h for h in h_cells) and any("code" in h or "academic" in h for h in h_cells)):
                        continue
                    second_row_cells = rows[1].cells if hasattr(rows[1], 'cells') else (rows[1].get('cells', []) if isinstance(rows[1], dict) else rows[1])
                    v_cells = [
                        (c.text.strip() if hasattr(c, 'text') else (c.get('text', '').strip() if isinstance(c, dict) else str(c).strip()))
                        for c in second_row_cells
                    ]
                    for idx, h in enumerate(h_cells):
                        if idx < len(v_cells) and v_cells[idx]:
                            val = v_cells[idx].replace("\n", " ").strip()
                            if ("course" in h and ("name" in h or "subject" in h)) or h == "name of the course":
                                profile.course_name = val
                            elif "code" in h and "course" in h:
                                profile.course_code = val
                            elif "academic" in h and "year" in h:
                                profile.academic_year = val
                            elif "semester" in h:
                                profile.semester = val
                            elif "credit" in h:
                                profile.credits = val
                            elif "duration" in h:
                                profile.exam_duration = val
                            elif "marks" in h and ("sa" in h or "total" in h or "max" in h):
                                try:
                                    m_num = re.search(r"\d+", val)
                                    if m_num:
                                        profile.total_marks = float(m_num.group(0))
                                except Exception:
                                    pass

        # Course Code fallback
        if not profile.course_code:
            for pat in COURSE_CODE_PATTERNS:
                m = pat.search(combined_early)
                if m:
                    profile.course_code = m.group(1).strip()
                    break

        # Course Name fallback
        if not profile.course_name:
            course_name_m = re.search(
                r"(?i)(?:name\s+of\s+the\s+course|subject|course(?:\s+name)?)\s*[:\s-]*([^\n|]+)",
                combined_early,
            )
            if course_name_m:
                cname = course_name_m.group(1).strip(" |:-")
                if len(cname) > 3 and not any(kw in cname.lower() for kw in ["code", "credits", "marks"]):
                    profile.course_name = cname[:100]

        # Academic Year
        for pat in ACADEMIC_YEAR_PATTERNS:
            m = pat.search(combined_early)
            if m:
                profile.academic_year = m.group(1).strip()
                break

        # Semester
        for pat in SEMESTER_PATTERNS:
            m = pat.search(combined_early)
            if m:
                profile.semester = f"Semester {m.group(1).strip()}"
                break

        # Programme
        prog_m = re.search(r"(?i)\b(B\.?\s*Tech|B\.?\s*E\.?|M\.?\s*Tech|BCA|MCA|MBA)\b[^\n|]*", combined_early)
        if prog_m:
            profile.programme = prog_m.group(0).strip(" |:-")[:60]

        # Duration & Marks fallback
        if not profile.exam_duration:
            dur_m = re.search(r"(?i)duration\s*(?:of\s*exam)?\s*[:\s-]*([^\n|]+)", combined_early)
            if dur_m:
                val = dur_m.group(1).strip(" |:-")[:30]
                if not any(k in val.lower() for k in ["marks", "credits", "code"]):
                    profile.exam_duration = val

        if profile.total_marks is None:
            marks_m = re.search(r"(?i)(?:total|max(?:imum)?|sa)\s*marks\s*[:\s-]*(\d+)", combined_early)
            if marks_m:
                try:
                    profile.total_marks = float(marks_m.group(1))
                except ValueError:
                    pass

        # Title from filename or document
        title_m = re.search(r"(?i)(?:question\s+bank|question\s+paper\s+bank|question\s+paper)[^\n|]*", combined_early)
        if title_m:
            profile.document_title = title_m.group(0).strip(" |:-")[:120]
        elif filename:
            profile.document_title = filename.rsplit(".", 1)[0].replace("_", " ")

    def _analyze_tables(
        self, blocks: list[ContentBlock], profile: DocumentProfile
    ) -> None:
        """Categorize tables and identify columns."""
        qb_count = 0
        ref_count = 0
        content_count = 0
        cols_detected = set()

        for b in blocks:
            if b.block_type != BlockType.TABLE or not b.table_data:
                continue

            raw_rows = _extract_raw_rows(b.table_data)
            if not raw_rows:
                continue

            # Header text check
            h_text = " ".join(c.lower() for c in raw_rows[0])

            # Metadata column check
            for keyword in ["unit", "co", "bloom", "difficulty", "level", "marks", "topic", "q. no", "sr. no"]:
                if keyword in h_text:
                    clean_kw = "bloom" if "bloom" in keyword else ("difficulty" if "level" in keyword or "diff" in keyword else keyword)
                    cols_detected.add(clean_kw)

            # Is reference table? (e.g. CO statement, Marks distribution)
            if ("co statement" in h_text) or ("bloom" in h_text and "co" in h_text and len(raw_rows) <= 6) or \
               ("co1" in h_text and "total" in h_text) or ("name of the course" in h_text and len(raw_rows) <= 3):
                ref_count += 1
            elif ("question" in h_text or "q. no" in h_text or "sr. no" in h_text) and \
                 ("difficulty" in h_text or "level" in h_text or "marks" in h_text or "bloom" in h_text or "co" in h_text):
                qb_count += 1
            elif len(raw_rows) >= 2 and any(re.match(r"^\d+[.)]?$", c.strip()) for r in raw_rows for c in r[:1]):
                qb_count += 1
            else:
                content_count += 1

        profile.table_stats = {
            "qb_tables": qb_count,
            "ref_tables": ref_count,
            "content_tables": content_count,
        }
        profile.metadata_columns = sorted(list(cols_detected))

    def _detect_units(
        self, blocks: list[ContentBlock], profile: DocumentProfile, filename: str = ""
    ) -> None:
        """Detect all unique units referenced in text or tables."""
        units_found: list[str] = []

        def normalize_u(match_val: str) -> str:
            val = match_val.strip()
            if val.isdigit():
                return f"Unit {val}"
            # Roman
            rom_val = {'i': 1, 'v': 5, 'x': 10, 'l': 50, 'c': 100}
            int_val = 0
            s = val.lower()
            for idx in range(len(s)):
                if idx > 0 and rom_val.get(s[idx], 0) > rom_val.get(s[idx - 1], 0):
                    int_val += rom_val.get(s[idx], 0) - 2 * rom_val.get(s[idx - 1], 0)
                else:
                    int_val += rom_val.get(s[idx], 0)
            return f"Unit {int_val}" if int_val > 0 else f"Unit {val}"

        for b in blocks:
            if b.block_type == BlockType.TEXT and b.text:
                for line in b.text.split("\n"):
                    m = UNIT_HEADING_PATTERN.search(line)
                    if m:
                        u_norm = normalize_u(m.group(1))
                        if u_norm not in units_found:
                            units_found.append(u_norm)
            elif b.block_type == BlockType.TABLE and b.table_data:
                raw_rows = _extract_raw_rows(b.table_data)
                for row in raw_rows:
                    for txt in row:
                        txt = txt.strip()
                        m = re.match(r"^(?:unit\s*)?([1-9]|10|[IVXLCDMivxlcdm]+)$", txt, re.IGNORECASE)
                        if m and len(txt) <= 8:
                            u_norm = normalize_u(m.group(1))
                            if u_norm not in units_found:
                                units_found.append(u_norm)

        if not units_found and filename:
            m_fn = re.search(r'(?i)\b(?:unit|module)\s*[-_:]?\s*(10|[1-9]|[IVXLCDMivxlcdm]+)\b', filename)
            if m_fn:
                u_norm = normalize_u(m_fn.group(1))
                units_found.append(u_norm)

        profile.detected_units = units_found
        profile.has_multiple_units = len(units_found) > 1

    def _determine_document_type(
        self, blocks: list[ContentBlock], profile: DocumentProfile
    ) -> None:
        """Determine primary layout strategy for extraction."""
        qb_tables = profile.table_stats.get("qb_tables", 0)
        
        # Scanned page check
        scanned_count = sum(1 for b in blocks if "scanned" in " ".join(b.notes).lower())
        if scanned_count > 0:
            profile.is_scanned = True
            profile.summary_notes.append(f"{scanned_count} scanned page(s) flagged.")

        if qb_tables >= 1:
            profile.doc_type = "question_bank_tabular"
            profile.question_numbering_style = "table_sr_no"
            profile.summary_notes.append(
                f"Tabular question bank detected with {qb_tables} question-list table(s)."
            )
        else:
            profile.doc_type = "question_bank_list"
            profile.question_numbering_style = "numeric"
            profile.summary_notes.append("List/paragraph-based question bank detected.")

        if profile.has_multiple_units:
            profile.summary_notes.append(
                f"Multi-unit document ({len(profile.detected_units)} units: {', '.join(profile.detected_units)})."
            )
        elif profile.detected_units:
            profile.summary_notes.append(f"Single-unit document ({profile.detected_units[0]}).")
        else:
            profile.summary_notes.append("No explicit unit headings identified.")
