"""
Table Classifier and Processing Service — Stage C.

Distinguishes between:
- Case A: Question-bank index tables (lists of independent questions with metadata).
- Case B: Question content tables (data frames, matrices, tables belonging to a single question).
- Case C: Reference tables (Course Outcome statements, Bloom weighting, Marks distribution).

Handles:
- Dynamic column mapping (Sr No, Unit, CO, Question, Bloom, Difficulty, Marks, Topic)
- Multi-page continuation tables and wrapped cell stitching
- Carry-forward unit and CO assignments
- Exclusion of group headings (e.g., 'Q.1 Answer the following:') from standalone question records
"""

from __future__ import annotations
import re
from typing import Any

DIFFICULTY_KEYWORDS = {'easy', 'moderate', 'difficult', 'medium', 'hard', 'low', 'high', 'beginner', 'intermediate', 'advanced', 'complexity'}
INDEX_COL_KEYWORDS = {'sr. no', 'sr.no', 'sr no', 'q. no', 'q.no', 'sl. no', 'sl.no', 's.no', 'no.', 'q no', 'sr', 'sl no', 'sl', 'item no', 'sn'}
QUESTION_COL_KEYWORDS = {'question', 'questions', 'particulars', 'problem', 'problems', 'description', 'question statement', 'question text', 'statement', 'item', 'items', 'exercise', 'exercises'}
METADATA_COL_KEYWORDS = {'difficulty', 'level', 'difficulty level', 'marks', 'cognitive', 'bloom', 'co', 'unit', 'module', 'topic', 'k-level', 'rbt'}

QUESTION_SPLIT_REGEX = re.compile(
    r'(?:^|\n)\s*(?:(?:Q(?:uestion)?\.?\s*)?(\d+)[.)\]:-]|(?:\(([a-z]|\d+|[ivxlcdm]+)\)|([a-z]|\d+|[ivxlcdm]+)[.)]))(?:\s*\n|\s+)',
    re.IGNORECASE
)

QUESTION_VERBS = {
    'what', 'explain', 'define', 'describe', 'discuss', 'differentiate',
    'distinguish', 'compare', 'calculate', 'illustrate', 'state', 'write',
    'show', 'prove', 'evaluate', 'analyze', 'derive', 'design', 'demonstrate',
    'identify', 'apply', 'formulate', 'critique', 'classify', 'give', 'list',
    'examine', 'assess', 'propose', 'recommend', 'develop', 'encrypt', 'decrypt',
    'how', 'why', 'which', 'when', 'where', 'who', 'find', 'solve', 'compute',
    'construct', 'draw', 'determine'
}


def is_independent_questions(matches: list[re.Match], text: str) -> bool:
    if len(matches) <= 1:
        return False

    # If all matches are Roman numerals (I., II., III., etc.), they represent
    # ordered steps, sub-items, or clauses within a single question, not separate top-level questions.
    all_roman = all(
        bool(
            (m.group(2) and re.match(r"^[ivxlcdm]+$", m.group(2), re.IGNORECASE)) or
            (m.group(3) and re.match(r"^[ivxlcdm]+$", m.group(3), re.IGNORECASE))
        )
        for m in matches
    )
    if all_roman:
        return False

    # If the first match is a main question number (e.g. 23.) and all subsequent matches
    # are Roman numerals (I., II., III., etc.) or clauses following a colon/steps:
    if len(matches) > 1 and matches[0].group(1):
        remaining_matches = matches[1:]
        all_roman_remaining = all(
            bool(
                (m.group(2) and re.match(r"^[ivxlcdm]+$", m.group(2), re.IGNORECASE)) or
                (m.group(3) and re.match(r"^[ivxlcdm]+$", m.group(3), re.IGNORECASE))
            )
            for m in remaining_matches
        )
        if all_roman_remaining:
            preamble = text[matches[0].end():remaining_matches[0].start()].strip()
            if preamble.endswith(':') or any(k in preamble.lower() for k in ['steps', 'following', 'options', 'statements', 'algorithm', 'syntax']):
                return False

    first_part = text[:matches[0].start()].strip()
    if first_part and (
        first_part.endswith(':') or
        first_part.endswith('?') or
        any(k in first_part.lower() for k in ['steps', 'options', 'following options', 'choose the', 'select the', 'following statements', 'do the following'])
    ):
        all_short_and_non_verbs = True
        for i, m in enumerate(matches):
            start = m.end()
            end = matches[i+1].start() if i + 1 < len(matches) else len(text)
            item_text = text[start:end].strip()
            first_word = item_text.split()[0].lower().strip(".,:;\"'?") if item_text.split() else ''
            if len(item_text.split()) > 15 or first_word in QUESTION_VERBS:
                all_short_and_non_verbs = False
                break
        if all_short_and_non_verbs:
            return False

    verb_count = 0
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i+1].start() if i + 1 < len(matches) else len(text)
        item_text = text[start:end].strip()
        words = item_text.split()
        first_word = words[0].lower().strip(".,:;\"'?") if words else ''
        if first_word in QUESTION_VERBS or '?' in item_text or len(words) >= 6:
            verb_count += 1
            
    return verb_count >= 2


def split_cell_questions(text: str, default_sr: str | None = None) -> list[dict]:
    text = text.strip()
    matches = list(QUESTION_SPLIT_REGEX.finditer(text))
    if not is_independent_questions(matches, text):
        sr = default_sr
        cleaned = text
        prefix = ""
        if matches:
            sr_match = matches[0]
            first_part = text[:sr_match.start()].strip() if sr_match.start() > 0 else ""
            if first_part and (
                first_part.endswith(':') or
                first_part.endswith('?') or
                any(k in first_part.lower() for k in ['steps', 'options', 'following options', 'choose the', 'select the', 'following statements', 'do the following'])
            ):
                m_lead = re.match(r"^(?:(?:Q(?:uestion)?\.?\s*)?(\d+)[.)\]:-]?\s+)", first_part, re.IGNORECASE)
                lead_sr = m_lead.group(1) if m_lead else default_sr
                return [{'serial_no': lead_sr, 'text': text, 'prefix': ''}]

            if sr_match.start() > 0:
                prefix = text[:sr_match.start()].strip()
            found_sr = sr_match.group(1) or sr_match.group(2) or sr_match.group(3)
            if found_sr:
                sr = found_sr
            cleaned = text[sr_match.end():].strip()
        return [{'serial_no': sr, 'text': cleaned, 'prefix': prefix}]

    prefix = text[:matches[0].start()].strip()
    results = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i+1].start() if i + 1 < len(matches) else len(text)
        sr = m.group(1) or m.group(2) or m.group(3)
        q_body = text[start:end].strip()
        if len(q_body) >= 3:
            results.append({'serial_no': sr or default_sr, 'text': q_body, 'prefix': prefix if i == 0 else ''})
    return results


def _extract_raw_rows_and_bboxes(table_input: Any) -> tuple[list[list[str]], list[tuple[float, float, float, float] | None]]:
    """Helper to convert various table formats to (list[list[str]], list[bbox])."""
    if not table_input:
        return [], []

    raw_rows: list[list[str]] = []
    raw_bboxes: list[tuple[float, float, float, float] | None] = []

    # Check if TableData instance
    if hasattr(table_input, 'rows'):
        for r in table_input.rows:
            bbox = getattr(r, 'bbox', None)
            raw_bboxes.append(bbox)
            if hasattr(r, 'cells'):
                raw_rows.append([c.text.strip() for c in r.cells])
            elif isinstance(r, (list, tuple)):
                raw_rows.append([str(c).strip() for c in r])
    # Check if dict
    elif isinstance(table_input, dict):
        if 'rows' in table_input:
            for r in table_input['rows']:
                bbox = r.get('bbox') if isinstance(r, dict) else None
                if bbox and isinstance(bbox, list):
                    bbox = tuple(bbox)
                raw_bboxes.append(bbox)
                if isinstance(r, dict) and 'cells' in r:
                    raw_rows.append([c.get('text', '').strip() if isinstance(c, dict) else str(c).strip() for c in r['cells']])
                elif isinstance(r, (list, tuple)):
                    raw_rows.append([str(c).strip() for c in r])
    # Check if list of lists
    elif isinstance(table_input, (list, tuple)):
        for r in table_input:
            raw_bboxes.append(None)
            if isinstance(r, (list, tuple)):
                raw_rows.append([str(c).strip() if c is not None else "" for c in r])

    return raw_rows, raw_bboxes


def _extract_raw_rows(table_input: Any) -> list[list[str]]:
    """Helper to convert various table formats to list[list[str]]."""
    rows, _ = _extract_raw_rows_and_bboxes(table_input)
    return rows


def is_reference_table(table_input: Any) -> bool:
    """
    Returns True if the table is a reference document table:
    - Course Outcome (CO) statements
    - Bloom taxonomy mapping
    - Marks distribution / weightage table
    - Course specification metadata table
    """
    raw_rows = _extract_raw_rows(table_input)
    if not raw_rows or len(raw_rows) < 2:
        return False

    h_str = " ".join(c.lower() for c in raw_rows[0])

    if ("co statement" in h_str) or ("co1" in h_str and "total" in h_str) or \
       ("bloom" in h_str and "co" in h_str and len(raw_rows) <= 6) or \
       ("marks distribution" in h_str):
        return True

    # Course parameter specification (e.g. Credits, SA Marks, Duration) with <= 3 rows
    if len(raw_rows) <= 3 and ("course code" in h_str or "academic year" in h_str or "name of the course" in h_str):
        return True

    return False


def is_question_bank_table(table_input: Any) -> bool:
    """
    Returns True if the table is a question-bank index table (list of candidate questions).
    Returns False if the table is a genuine question content table or reference table.
    """
    if not table_input:
        return False

    if isinstance(table_input, dict) and 'html' in table_input and 'rows' not in table_input:
        return is_question_bank_table_html(table_input['html'])

    raw_rows = _extract_raw_rows(table_input)
    if not raw_rows or len(raw_rows) < 1:
        return False

    # Exclude reference tables first
    if is_reference_table(table_input):
        return False

    # Check single-row question table (common when a question spans an entire page)
    if len(raw_rows) == 1:
        cells = [c.strip() for c in raw_rows[0] if c]
        has_serial = any(re.match(r"^\s*(?:Q\.?\s*)?\d+[.)\]]?\s*$", c) for c in cells[:2])
        has_q_text = any(len(c) > 20 for c in cells)
        has_meta_val = any(
            (c.lower() in DIFFICULTY_KEYWORDS) or
            bool(re.match(r"^co\d+$", c.lower())) or
            bool(re.match(r"^\s*\d+\s*m(?:arks)?\s*$", c, re.IGNORECASE))
            for c in cells
        )
        return (has_serial and has_q_text) or (has_q_text and has_meta_val)

    # 1. Inspect header row
    header = [c.lower() for c in raw_rows[0]]
    header_str = ' '.join(header)

    has_index = any(k in header_str for k in INDEX_COL_KEYWORDS)
    has_q_col = any(k in header_str for k in QUESTION_COL_KEYWORDS)
    has_meta  = any(k in header_str for k in METADATA_COL_KEYWORDS)

    if (has_index and has_q_col) or (has_q_col and has_meta):
        return True

    # 2. Inspect individual row patterns across any columns
    # Handles continuation tables without explicit headers or with diverse column orders
    qb_row_count = 0
    for row in raw_rows:
        if not row or len(row) < 2:
            continue
        cells = [c.strip() for c in row if c]
        if not cells:
            continue

        has_serial = any(re.match(r"^\s*(?:Q\.?\s*)?\d+[.)\]]?\s*$", c) for c in cells[:2])
        has_q_text = any(len(c) > 20 for c in cells)
        has_meta_val = any(
            (c.lower() in DIFFICULTY_KEYWORDS) or
            bool(re.match(r"^co\d+$", c.lower())) or
            bool(re.match(r"^\s*\d+\s*m(?:arks)?\s*$", c, re.IGNORECASE))
            for c in cells
        )

        if (has_serial and has_q_text) or (has_q_text and has_meta_val):
            qb_row_count += 1

    if qb_row_count >= 1:
        return True

    # 3. Check for cells containing multiple independent questions
    for row in raw_rows:
        for c in row:
            if len(c) > 50:
                matches = list(QUESTION_SPLIT_REGEX.finditer(c))
                if is_independent_questions(matches, c):
                    return True

    return False


def is_question_bank_table_html(html: str) -> bool:
    """Check HTML string of an extracted table."""
    if not html:
        return False
    h_lower = html.lower()

    # Exclude reference tables
    if "co statement" in h_lower or ("co1" in h_lower and "total" in h_lower):
        return False

    # Check headers
    if ('sr.' in h_lower or 'sr ' in h_lower or 'sr.no' in h_lower or 'q. no' in h_lower or 'sl. no' in h_lower) and 'question' in h_lower:
        return True
    if 'question' in h_lower and ('difficulty' in h_lower or 'level' in h_lower or 'marks' in h_lower or 'unit' in h_lower or 'co' in h_lower):
        return True

    # Check for question rows (td number + td long text + td difficulty)
    if re.search(r'<t[dh]>\s*\d+\.?\s*</t[dh]>', html) and any(d in h_lower for d in DIFFICULTY_KEYWORDS):
        return True

    return False


def extract_qb_table_rows(
    table_input: Any, context_unit: str | None = None
) -> tuple[list[dict], str | None]:
    """
    Extract individual candidate question records from a question bank table.
    
    Features:
    - Identifies column mapping dynamically.
    - Stitches continuation rows that wrap across page breaks.
    - Carries forward unit and course outcome assignments.
    - Excludes group headings (e.g. 'Answer the following:') from standalone question records.
    - Returns:
        (questions_list, updated_unit_context)
    """
    raw_rows, raw_bboxes = _extract_raw_rows_and_bboxes(table_input)
    if not raw_rows or len(raw_rows) < 1:
        return []

    # 1. Identify column mapping
    h_row = [re.sub(r'\s+', ' ', c.lower().strip()) for c in raw_rows[0]]
    first_cell_is_sr = bool(re.match(r"^\s*(?:Q\.?\s*)?\d+[.)\]]?\s*$", h_row[0])) if h_row else False
    has_header = False
    if not first_cell_is_sr and h_row:
        has_index = any(any(k == c or k in c.split() for k in INDEX_COL_KEYWORDS) for c in h_row[:2])
        has_q = any(any(k == c or k in c.split() for k in QUESTION_COL_KEYWORDS) for c in h_row if len(c) < 35)
        has_header = (has_index and has_q) or (has_q and any(k in ' '.join(h_row) for k in METADATA_COL_KEYWORDS))

    idx_col = None
    q_col = None
    diff_col = None
    marks_col = None
    unit_col = None
    co_col = None
    bloom_col = None
    topic_col = None

    if has_header:
        for idx, col in enumerate(h_row):
            if any(k in col for k in INDEX_COL_KEYWORDS) and idx_col is None:
                idx_col = idx
            elif any(k in col for k in QUESTION_COL_KEYWORDS) and q_col is None:
                q_col = idx
            elif any(k in col for k in {'difficulty', 'level', 'diff', 'levels'}) and 'bloom' not in col and diff_col is None:
                diff_col = idx
            elif any(k in col for k in {'bloom'}) and bloom_col is None:
                bloom_col = idx
            elif any(k in col for k in {'marks', 'mark'}) and marks_col is None:
                marks_col = idx
            elif any(k in col for k in {'unit', 'module'}) and unit_col is None:
                unit_col = idx
            elif col == 'co' or 'course outcome' in col:
                co_col = idx
            elif any(k in col for k in {'topic', 'taxonomy'}) and topic_col is None:
                topic_col = idx
        data_rows = raw_rows[1:]
        data_bboxes = raw_bboxes[1:]
    else:
        # Continuation table without explicit header:
        # Analyze columns across rows to find roles
        data_bboxes = raw_bboxes
        col_count = len(raw_rows[0])
        col_avg_len = [0.0] * col_count
        col_serial_count = [0] * col_count
        col_diff_count = [0] * col_count
        col_co_count = [0] * col_count
        col_unit_count = [0] * col_count

        for r in raw_rows:
            for c_idx, val in enumerate(r[:col_count]):
                clean = re.sub(r'\s+', ' ', val).strip()
                col_avg_len[c_idx] += len(clean)
                if re.match(r"^\s*(?:Q\.?\s*)?\d+[.)\]]?\s*$", clean):
                    col_serial_count[c_idx] += 1
                clean_no_ws = clean.lower().replace(' ', '').replace('\n', '')
                if any(clean_no_ws == d or clean_no_ws.startswith(d) for d in DIFFICULTY_KEYWORDS):
                    col_diff_count[c_idx] += 1
                if re.match(r"^co\d+$", clean.lower()):
                    col_co_count[c_idx] += 1
                if re.match(r"^(?:unit|module)\s*[-:]?\s*([1-9]|10|[IVXLCDMivxlcdm]+)$", clean, re.IGNORECASE):
                    col_unit_count[c_idx] += 1

        for c_idx in range(col_count):
            col_avg_len[c_idx] /= max(1, len(raw_rows))

        # Best serial column
        if max(col_serial_count, default=0) > 0:
            idx_col = col_serial_count.index(max(col_serial_count))

        # Best question column (longest text, excluding serial col)
        best_len = -1
        for c_idx in range(col_count):
            if c_idx != idx_col and col_avg_len[c_idx] > best_len:
                best_len = col_avg_len[c_idx]
                q_col = c_idx

        if max(col_diff_count, default=0) > 0:
            diff_col = col_diff_count.index(max(col_diff_count))
        if max(col_co_count, default=0) > 0:
            co_col = col_co_count.index(max(col_co_count))
        if max(col_unit_count, default=0) > 0:
            unit_col = col_unit_count.index(max(col_unit_count))

        data_rows = raw_rows

    if q_col is None:
        return []

    # 2. Pre-distribute stacked multi-line serials and recover row 0 serial if shifted
    if idx_col is not None and len(data_rows) > 0:
        data_rows = [list(r) for r in data_rows]
        pending_srs: list[str] = []
        pending_diffs: list[str] = []
        for i, r in enumerate(data_rows):
            c0 = r[idx_col].strip() if len(r) > idx_col else ""
            srs = [s.strip() for s in c0.split('\n') if s.strip()] if c0 else []
            diff_str = r[diff_col].strip() if (diff_col is not None and len(r) > diff_col) else ""
            diffs = [d.strip() for d in diff_str.split('\n') if d.strip()] if diff_str else []

            if len(srs) > 1:
                r[idx_col] = srs[0]
                pending_srs.extend(srs[1:])
            elif len(srs) == 1:
                if pending_srs:
                    cur = srs[0]
                    r[idx_col] = pending_srs.pop(0)
                    pending_srs.append(cur)
            elif not srs and pending_srs:
                r[idx_col] = pending_srs.pop(0)

            if len(diffs) > 1:
                r[diff_col] = diffs[0]
                pending_diffs.extend(diffs[1:])
            elif len(diffs) == 1:
                if pending_diffs:
                    cur_d = diffs[0]
                    r[diff_col] = pending_diffs.pop(0)
                    pending_diffs.append(cur_d)
            elif not diffs and pending_diffs:
                r[diff_col] = pending_diffs.pop(0)

            data_rows[i] = r

    # 3. Stitch continuation rows
    stitched: list[list[str]] = []
    stitched_bboxes: list[tuple[float, float, float, float] | None] = []
    for r_idx, r in enumerate(data_rows):
        if not r or len(r) <= q_col:
            continue
        r_bbox = data_bboxes[r_idx] if r_idx < len(data_bboxes) else None
        c0 = r[idx_col].strip() if (idx_col is not None and len(r) > idx_col) else ""
        c_q = r[q_col].strip() if len(r) > q_col else ""
        # Check if previous row had multiple serial numbers (e.g. '2.\n3.')
        # and current row has empty serial column but non-empty question text
        if stitched and idx_col is not None and len(stitched[-1]) > idx_col:
            prev_srs = [s.strip() for s in stitched[-1][idx_col].split('\n') if s.strip()]
            if len(prev_srs) >= 2 and not c0 and c_q:
                stitched[-1][idx_col] = prev_srs[0]
                c0 = prev_srs[1]
                r = list(r)
                r[idx_col] = c0
                if diff_col is not None and len(stitched[-1]) > diff_col:
                    prev_diffs = [d.strip() for d in stitched[-1][diff_col].split('\n') if d.strip()]
                    if len(prev_diffs) >= 2:
                        stitched[-1][diff_col] = prev_diffs[0]
                        if len(r) > diff_col and not r[diff_col]:
                            r[diff_col] = prev_diffs[1]

        if not c_q:
            excluded = {idx_col, diff_col, marks_col, unit_col, co_col}
            candidates = [c.strip() for i, c in enumerate(r) if c and i not in excluded and len(c.strip()) >= 2]
            if not candidates:
                candidates = [c.strip() for i, c in enumerate(r) if c and i != idx_col and len(c.strip()) >= 2]
            if candidates:
                c_q = " ".join(candidates)
                r = list(r)
                r[q_col] = c_q
            elif idx_col is not None and len(r) > idx_col and len(r[idx_col].strip()) >= 15:
                txt_idx = r[idx_col].strip()
                m_lead = re.match(r"^\s*(?:Q\.?\s*)?(\d{1,4})[.)\]:-]?\s+(.+)$", txt_idx, re.DOTALL | re.IGNORECASE)
                if m_lead:
                    r = list(r)
                    r[idx_col] = m_lead.group(1)
                    r[q_col] = m_lead.group(2).strip()
                    c0 = r[idx_col]
                    c_q = r[q_col]

        # Check if question row has an embedded group instruction starting with "Let ... then solve"
        m_split_inst = re.search(r'(?is)^(.+?)\s*\n\s*(Let\b.*?(?:then\s+)?solve\s+\d+.*)$', c_q)
        if m_split_inst:
            r = list(r)
            r[q_col] = m_split_inst.group(1).strip()
            inst_row = ["" for _ in range(len(r))]
            inst_row[q_col] = m_split_inst.group(2).strip()
            stitched.append(r)
            stitched_bboxes.append(r_bbox)
            stitched.append(inst_row)
            stitched_bboxes.append(r_bbox)
            continue

        # Check if row is solely a unit heading or instruction
        row_str_check = " ".join(c for c in r if c).strip()
        is_unit_row = bool(
            re.search(r"(?i)\b(?:unit|module)\s*[-:]?\s*(?:(10|[1-9])(?!\d)|([ivxlcdm]+)\b)(?:[\s\-_:]*(.*))?$", row_str_check)
            and not (set(re.findall(r"\b[a-z]+\b", row_str_check.lower())) & QUESTION_VERBS)
            and not any(k in row_str_check.lower() for k in ['question bank', 'syllabus', 'course outcome'])
        )
        is_heading_or_inst = is_unit_row or any(k in c_q.lower() for k in [
            'answer the following', 'attempt any', 'coding based questions',
            'part a', 'part b', 'section a', 'section b'
        ])

        # Check if continuation fragment:
        # If no serial number, not a heading/instruction, and previous row exists
        if not c0 and c_q and stitched and not is_heading_or_inst:
            has_explicit_sr = bool(re.match(r"^\s*(?:Q(?:uestion)?\.?\s*)?(\d{1,4})[.)\]:-]\s+\S", c_q, re.IGNORECASE))
            if not has_explicit_sr:
                stitched[-1][q_col] = (stitched[-1][q_col] + "\n" + c_q).strip()
                if stitched_bboxes and stitched_bboxes[-1] and r_bbox:
                    b1 = stitched_bboxes[-1]
                    b2 = r_bbox
                    stitched_bboxes[-1] = (min(b1[0], b2[0]), min(b1[1], b2[1]), max(b1[2], b2[2]), max(b1[3], b2[3]))
                elif r_bbox and stitched_bboxes:
                    stitched_bboxes[-1] = r_bbox
                if diff_col is not None and len(r) > diff_col and r[diff_col]:
                    if len(stitched[-1]) > diff_col and not stitched[-1][diff_col]:
                        stitched[-1][diff_col] = r[diff_col]
                if marks_col is not None and len(r) > marks_col and r[marks_col]:
                    if len(stitched[-1]) > marks_col and not stitched[-1][marks_col]:
                        stitched[-1][marks_col] = r[marks_col]
                continue

        stitched.append(list(r))
        stitched_bboxes.append(r_bbox)

    # 3. Process each row
    results: list[dict] = []
    current_unit = context_unit
    current_co: str | None = None
    current_instruction: dict | None = None

    for r_idx_st, r in enumerate(stitched):
        row_bbox = stitched_bboxes[r_idx_st] if r_idx_st < len(stitched_bboxes) else None
        row_full_text = " ".join(c for c in r if c).strip()

        # Check if row is a group instruction or dataset reference for a range of questions
        m_inst = re.search(
            r'(?i)(?:(?:solve|for|use|refer|answer)\s+.*?questions?\s*(?:no\.?)?\s*(\d+)\s*(?:to|-|and)\s*(\d+)|(?:then\s+)?solve\s+(\d+)\s*(?:to|-|and)\s*(\d+))',
            row_full_text
        )
        if m_inst:
            g = [x for x in m_inst.groups() if x is not None]
            if len(g) >= 2:
                current_instruction = {
                    "text": row_full_text,
                    "start": int(g[0]),
                    "end": int(g[1])
                }
                continue

        # Check if row is a unit heading (e.g., 'UNIT 1', 'UNIT 2', 'Unit-4Fuzzy Logic Control', 'Unit 3Fundamental of Fuzzy Logic', 'Unit 4: Fuzzy')
        u_match = re.search(
            r"(?i)\b(?:unit|module)\s*[-:]?\s*(?:(10|[1-9])(?!\d)|([ivxlcdm]+)\b)(?:[\s\-_:]*(.*))?$",
            row_full_text
        )
        is_unit_row = False
        if u_match:
            words = set(re.findall(r"\b[a-z]+\b", row_full_text.lower()))
            if not (words & QUESTION_VERBS) and not any(k in row_full_text.lower() for k in ['question bank', 'syllabus', 'course outcome']):
                raw_u_val = u_match.group(1) or u_match.group(2)
                if raw_u_val.isdigit():
                    current_unit = f"Unit {raw_u_val}"
                else:
                    rom_val = {'i': 1, 'v': 5, 'x': 10, 'l': 50, 'c': 100}
                    s_r = raw_u_val.lower().strip()
                    int_u = 0
                    for i_r in range(len(s_r)):
                        if i_r > 0 and rom_val.get(s_r[i_r], 0) > rom_val.get(s_r[i_r - 1], 0):
                            int_u += rom_val.get(s_r[i_r], 0) - 2 * rom_val.get(s_r[i_r - 1], 0)
                        else:
                            int_u += rom_val.get(s_r[i_r], 0)
                    current_unit = f"Unit {int_u or 1}"
                is_unit_row = True

        if is_unit_row:
            continue

        q_text = r[q_col].strip() if len(r) > q_col else ""
        q_text = re.sub(r'[ \t]+', ' ', q_text).strip()
        if not q_text or len(q_text) < 4:
            # Check other cells in row (excluding index and metadata columns)
            excluded_cols = {idx_col, diff_col, marks_col, unit_col, co_col}
            candidates = [c.strip() for i, c in enumerate(r) if c and i not in excluded_cols and len(c.strip()) >= 4]
            if not candidates:
                candidates = [c.strip() for i, c in enumerate(r) if c and i != idx_col and len(c.strip()) >= 4]
            if candidates:
                q_text = candidates[0]
            else:
                continue

        # Skip repeated header row
        if any(k in q_text.lower() for k in QUESTION_COL_KEYWORDS) and (
            idx_col is not None and any(k in r[idx_col].lower() for k in INDEX_COL_KEYWORDS)
        ):
            continue

        # Check Unit updates from unit_col or embedded in row
        raw_u = r[unit_col].strip() if (unit_col is not None and len(r) > unit_col) else ""
        if raw_u:
            u_m = re.search(r'\b(?:unit|module)\s*[-:]?\s*(\d+|[ivxlcdm]+)\b', raw_u, re.IGNORECASE)
            if u_m:
                current_unit = f"Unit {u_m.group(1)}"
            elif re.match(r"^\d+$", raw_u) and has_header and unit_col is not None and 'unit' in h_row[unit_col]:
                current_unit = f"Unit {raw_u}"

        # Check Course Outcome
        raw_co = r[co_col].strip() if (co_col is not None and len(r) > co_col) else ""
        if raw_co and re.match(r"^co\d+$", raw_co, re.IGNORECASE):
            current_co = raw_co.upper()

        # Serial number: prioritize clean serial number column
        raw_sr = r[idx_col].strip() if (idx_col is not None and len(r) > idx_col) else ""
        m_sr_exact = re.match(r"^\s*(?:Q\.?\s*)?(\d{1,4}|[a-z]|[ivxlcdm]+)[.)\]:-]?\s*$", raw_sr, re.IGNORECASE)
        if m_sr_exact:
            sr_no = m_sr_exact.group(1)
        else:
            m_lead = re.match(r"^\s*(?:Q\.?\s*)?(\d{1,4})[.)\]:-]?(?:\s+|$)", raw_sr, re.IGNORECASE)
            sr_no = m_lead.group(1) if m_lead else ""

        # Clean leading serial number from question text if present
        q_cleaned = re.sub(r"^(?:(?:Q(?:uestion)?\.?\s*)\d+[.)\]:-]?\s+|\d+[.)\]:-]\s+)", "", q_text, flags=re.IGNORECASE).strip()

        if not sr_no:
            prefix_match = re.match(r"^(?:(?:Q(?:uestion)?\.?\s*)(\d+)[.)\]:-]?\s+|(\d+)[.)\]:-]\s+)", q_text, re.IGNORECASE)
            if prefix_match:
                sr_no = prefix_match.group(1) or prefix_match.group(2)

        # Skip section group instructions like "Answer the following:" if it has no serial number
        if not sr_no and any(inst in q_text.lower() for inst in ["answer the following", "attempt any", "coding based questions"]):
            continue

        # Repair OCR text artifacts
        q_cleaned = re.sub(r'(?i)\bfoleasying\b', 'following', q_cleaned)
        q_cleaned = re.sub(r'(?i)\bbeeasy\b', 'below', q_cleaned)
        q_text = re.sub(r'(?i)\bfoleasying\b', 'following', q_text)
        q_text = re.sub(r'(?i)\bbeeasy\b', 'below', q_text)

        # Prepend group instruction if this question falls within the instruction's range
        if current_instruction and sr_no and sr_no.isdigit():
            q_num = int(sr_no)
            if current_instruction["start"] <= q_num <= current_instruction["end"]:
                inst_text = current_instruction["text"]
                if inst_text not in q_cleaned and inst_text not in q_text:
                    q_cleaned = f"[{inst_text}]\n{q_cleaned}"
                    q_text = f"[{inst_text}]\n{q_text}"

        # Difficulty
        diff_val = None
        if diff_col is not None and len(r) > diff_col:
            raw_diff = re.sub(r'\s+', '', r[diff_col]).lower()
            for dk in DIFFICULTY_KEYWORDS:
                if dk in raw_diff:
                    diff_val = dk.capitalize()
                    break

        # Bloom's level
        bloom_val = None
        if bloom_col is not None and len(r) > bloom_col:
            b_str = r[bloom_col].strip()
            if b_str:
                bloom_val = b_str

        # Marks
        marks_val = None
        if marks_col is not None and len(r) > marks_col:
            m_match = re.search(r"(\d+(?:\.\d+)?)", r[marks_col])
            if m_match:
                try:
                    marks_val = float(m_match.group(1))
                except ValueError:
                    pass

        topic_val = r[topic_col].strip() if (topic_col is not None and len(r) > topic_col) else None

        # Split cell if it contains multiple independent questions
        target_text = q_cleaned if q_cleaned else q_text
        cell_questions = split_cell_questions(target_text, default_sr=sr_no)

        for cq in cell_questions:
            q_str = cq.get('text', '').strip()
            if not q_str or len(q_str) < 4:
                continue
            item_sr = cq.get('serial_no') or sr_no
            results.append({
                'serial_no': item_sr,
                'question_text': q_str,
                'raw_text': q_str,
                'difficulty': diff_val,
                'marks': marks_val,
                'unit': current_unit,
                'course_outcome': current_co,
                'bloom_level': bloom_val,
                'topic': topic_val,
                'bbox': row_bbox,
            })

    if isinstance(table_input, dict) and current_unit:
        table_input['_detected_unit'] = current_unit

    return results
