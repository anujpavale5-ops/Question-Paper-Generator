"""
DOCX Exporter Service.

Generates formal, printable examination papers in Microsoft Word (.docx) format
using python-docx, matching standard university / autonomous college formats.
Ensures full editability of text, tables, images, and layout in Microsoft Word.
"""

import io
import os
import re
import base64
import logging
from typing import Any
from PIL import Image

import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from bs4 import BeautifulSoup

from backend.services.table_classifier import is_question_bank_table, is_question_bank_table_html

logger = logging.getLogger(__name__)


# ── XML Helper Functions ──────────────────────────────────────────────────────

def _set_cell_margins(cell, top=60, bottom=60, left=80, right=80):
    """Set inner cell padding in twips (1 pt = 20 twips)."""
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = OxmlElement('w:tcMar')
    for m, val in [('top', top), ('bottom', bottom), ('left', left), ('right', right)]:
        node = OxmlElement(f'w:{m}')
        node.set(qn('w:w'), str(val))
        node.set(qn('w:type'), 'dxa')
        tcMar.append(node)
    tcPr.append(tcMar)


def _set_cell_border(cell, **kwargs):
    """
    Set individual cell borders.
    kwargs: top, bottom, left, right
    values: dict(sz=4, val='single', color='000000')
    """
    tcPr = cell._tc.get_or_add_tcPr()
    tcBorders = tcPr.first_child_found_in('w:tcBorders')
    if tcBorders is None:
        tcBorders = OxmlElement('w:tcBorders')
        tcPr.append(tcBorders)
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        edge_data = kwargs.get(edge)
        if edge_data:
            tag = f'w:{edge}'
            element = OxmlElement(tag)
            element.set(qn('w:val'), edge_data.get('val', 'single'))
            element.set(qn('w:sz'), str(edge_data.get('sz', 4)))
            element.set(qn('w:space'), '0')
            element.set(qn('w:color'), edge_data.get('color', '000000'))
            tcBorders.append(element)


def _set_cell_shading(cell, color_hex):
    """Apply background shading to a cell."""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color_hex}"/>')
    tcPr.append(shd)


def _set_table_borders(table, color="000000", sz="4", inside_v="single", inside_h="single"):
    """Apply standard clean borders to all cells in a table."""
    tblPr = table._tbl.tblPr
    tblBorders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>\n'
        f'  <w:top w:val="single" w:sz="{sz}" w:space="0" w:color="{color}"/>\n'
        f'  <w:bottom w:val="single" w:sz="{sz}" w:space="0" w:color="{color}"/>\n'
        f'  <w:insideH w:val="{inside_h}" w:sz="{sz}" w:space="0" w:color="{color}"/>\n'
        f'  <w:insideV w:val="{inside_v}" w:sz="{sz}" w:space="0" w:color="{color}"/>\n'
        f'  <w:left w:val="single" w:sz="{sz}" w:space="0" w:color="{color}"/>\n'
        f'  <w:right w:val="single" w:sz="{sz}" w:space="0" w:color="{color}"/>\n'
        f'</w:tblBorders>'
    )
    tblPr.append(tblBorders)


def _set_row_cant_split(row):
    """Prevent table row from splitting awkwardly across pages."""
    trPr = row._tr.get_or_add_trPr()
    trPr.append(OxmlElement('w:cantSplit'))


def _set_table_column_widths(table, widths):
    """Enforce explicit column widths across table columns and cells."""
    table.autofit = False
    for row in table.rows:
        for ci, w in enumerate(widths):
            if ci < len(row.cells):
                row.cells[ci].width = w
    for ci, w in enumerate(widths):
        if ci < len(table.columns):
            table.columns[ci].width = w


def _add_page_number(run):
    """Insert dynamic Word PAGE and NUMPAGES fields."""
    fld1 = parse_xml(r'<w:fldChar %s w:fldCharType="begin"/>' % nsdecls('w'))
    instr1 = parse_xml(r'<w:instrText %s xml:space="preserve"> PAGE </w:instrText>' % nsdecls('w'))
    fld2 = parse_xml(r'<w:fldChar %s w:fldCharType="separate"/>' % nsdecls('w'))
    fld3 = parse_xml(r'<w:fldChar %s w:fldCharType="end"/>' % nsdecls('w'))
    run._r.append(fld1)
    run._r.append(instr1)
    run._r.append(fld2)
    run._r.append(fld3)


def _calculate_image_dimensions(img_path_or_bytes, max_w_in=5.5, max_h_in=3.8, explicit_w_px=None):
    """
    Calculate proportional image dimensions in Inches, respecting aspect ratio
    and bounding within printable margins.
    """
    try:
      with Image.open(img_path_or_bytes) as im:
        w_px, h_px = im.size
        if w_px <= 0 or h_px <= 0:
          return Inches(max_w_in), None

        aspect = h_px / float(w_px)

        if explicit_w_px and explicit_w_px > 0:
          target_w_in = explicit_w_px / 96.0
          if target_w_in > max_w_in:
            target_w_in = max_w_in
        else:
          # Scale based on native resolution, bounded to max_w_in
          target_w_in = min(max_w_in, max(1.5, w_px / 150.0))

        target_h_in = target_w_in * aspect
        if target_h_in > max_h_in:
          target_h_in = max_h_in
          target_w_in = target_h_in / aspect

        return Inches(round(target_w_in, 2)), Inches(round(target_h_in, 2))
    except Exception:
      return Inches(max_w_in), None


def filter_question_image_blocks(blocks: list[dict]) -> list[dict]:
    """
    Filter out redundant raw image/vector slices when a unified rendered figure
    (_fig_) is present for the same page/question.
    """
    fig_blocks = [
        b for b in blocks
        if b.get('block_type') in ('image', 'figure')
        and ('_fig_' in (b.get('asset_id') or b.get('image_path') or '') or b.get('extraction_method') == 'rendered_region')
    ]
    if not fig_blocks:
        return [b for b in blocks if b.get('block_type') in ('image', 'figure')]

    fig_pages = set()
    for fb in fig_blocks:
        aid = fb.get('asset_id') or fb.get('image_path') or ''
        m = re.search(r'_p(\d+)_', aid)
        if m:
            fig_pages.add(m.group(1))

    filtered = []
    for b in blocks:
        if b.get('block_type') not in ('image', 'figure'):
            continue
        aid = b.get('asset_id') or b.get('image_path') or ''
        m = re.search(r'_p(\d+)_', aid)
        page_num = m.group(1) if m else None

        if page_num and page_num in fig_pages and '_fig_' not in aid and b.get('extraction_method') != 'rendered_region':
            continue
        filtered.append(b)
    return filtered


# ── Content Cell Renderers ───────────────────────────────────────────────────

def _insert_image_from_tag(img_tag, paragraph, base_folders=None):
    """Insert image from HTML <img> tag into paragraph."""
    src = img_tag.get('src', '')
    if not src:
        return

    img_bytes = None
    if src.startswith('data:image'):
        try:
            _, data = src.split(',', 1)
            img_bytes = io.BytesIO(base64.b64decode(data))
        except Exception:
            pass
    else:
        fname = os.path.basename(src.split('?')[0].replace('\\', '/'))
        if base_folders:
            for fld in base_folders:
                if not fld:
                    continue
                cand = os.path.join(fld, fname)
                if os.path.isfile(cand):
                    try:
                        with open(cand, 'rb') as f:
                            img_bytes = io.BytesIO(f.read())
                        break
                    except Exception:
                        pass

    if img_bytes:
        try:
            explicit_w = None
            style = img_tag.get('style', '')
            m = re.search(r'width\s*:\s*(\d+)px', style)
            if m:
                explicit_w = float(m.group(1))
            elif img_tag.get('width'):
                try:
                    explicit_w = float(img_tag['width'])
                except ValueError:
                    pass

            w, h = _calculate_image_dimensions(img_bytes, max_w_in=5.5, max_h_in=3.5, explicit_w_px=explicit_w)
            img_bytes.seek(0)
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.paragraph_format.space_before = Pt(4)
            paragraph.paragraph_format.space_after = Pt(4)
            if w and h:
                paragraph.add_run().add_picture(img_bytes, width=w, height=h)
            elif w:
                paragraph.add_run().add_picture(img_bytes, width=w)
        except Exception as e:
            logger.warning(f"Failed to insert image into DOCX: {e}")


def _render_html_table(cell, table_tag):
    """Render HTML <table> tag into a native Word table within a cell."""
    rows = table_tag.find_all('tr')
    if not rows:
        return
    row_count = len(rows)
    max_cols = max(len(r.find_all(['td', 'th'])) for r in rows)
    if max_cols == 0:
        return

    sub_tbl = cell.add_table(rows=row_count, cols=max_cols)
    sub_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(sub_tbl, sz="4")

    is_dense = max_cols > 6
    avail_w = 5.5
    col_width = Inches(round(avail_w / max_cols, 2))
    pad_lr = 30 if is_dense else 50
    pad_tb = 25 if is_dense else 40
    font_sz = Pt(8.5) if is_dense else Pt(10)

    for ri, r in enumerate(rows):
        cells = r.find_all(['td', 'th'])
        for ci in range(min(max_cols, len(cells))):
            c_elem = cells[ci]
            cell_obj = sub_tbl.cell(ri, ci)
            cell_obj.width = col_width
            _set_cell_margins(cell_obj, top=pad_tb, bottom=pad_tb, left=pad_lr, right=pad_lr)
            p = cell_obj.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r_run = p.add_run(c_elem.get_text().strip())
            r_run.font.size = font_sz
            if c_elem.name == 'th' or ri == 0:
                r_run.bold = True

    _set_table_column_widths(sub_tbl, [col_width] * max_cols)


def _render_dict_table(cell, t_data):
    """Render structured table data dict into native Word table within a cell."""
    rows = t_data.get('rows', [])
    if not rows:
        return
    col_count = t_data.get('column_count') or max(len(r.get('cells', [])) for r in rows)
    if col_count == 0:
        return

    sub_tbl = cell.add_table(rows=len(rows), cols=col_count)
    sub_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(sub_tbl, sz="4")

    is_dense = col_count > 6
    avail_w = 5.5
    col_w = Inches(round(avail_w / col_count, 2))
    pad_lr = 30 if is_dense else 50
    pad_tb = 25 if is_dense else 40
    font_sz = Pt(8.5) if is_dense else Pt(10)

    for ri, r_data in enumerate(rows):
        cells = r_data.get('cells', [])
        for ci in range(min(col_count, len(cells))):
            cell_val = str(cells[ci].get('text', '')).strip()
            cell_obj = sub_tbl.cell(ri, ci)
            cell_obj.width = col_w
            _set_cell_margins(cell_obj, top=pad_tb, bottom=pad_tb, left=pad_lr, right=pad_lr)
            p = cell_obj.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run(cell_val)
            run.font.size = font_sz
            if ri == 0:
                run.bold = True

    _set_table_column_widths(sub_tbl, [col_w] * col_count)


def _render_html_into_cell(cell, html_content: str, base_folders: list[str]):
    """Parse HTML string and render native Word elements into a table cell."""
    if not html_content or not html_content.strip():
        return

    soup = BeautifulSoup(html_content, 'html.parser')

    def process_inline_nodes(element, paragraph):
        for node in element.children:
            if isinstance(node, str):
                text = str(node)
                if text:
                    paragraph.add_run(text)
            elif node.name == 'br':
                paragraph.add_run().add_break()
            elif node.name in ('b', 'strong'):
                run = paragraph.add_run(node.get_text())
                run.bold = True
            elif node.name in ('i', 'em'):
                run = paragraph.add_run(node.get_text())
                run.italic = True
            elif node.name == 'u':
                run = paragraph.add_run(node.get_text())
                run.underline = True
            elif node.name in ('code', 'tt'):
                run = paragraph.add_run(node.get_text())
                run.font.name = 'Consolas'
                run.font.size = Pt(9.5)
            elif node.name == 'img':
                _insert_image_from_tag(node, paragraph, base_folders)
            elif node.name in ('span', 'font', 'a'):
                process_inline_nodes(node, paragraph)
            else:
                paragraph.add_run(node.get_text())

    body = soup.body if soup.body else soup
    first_p = cell.paragraphs[0] if cell.paragraphs else cell.add_paragraph()
    first_p_used = False

    for child in body.children:
        if isinstance(child, str):
            txt = str(child).strip()
            if txt:
                p = first_p if not first_p_used else cell.add_paragraph()
                first_p_used = True
                p.paragraph_format.line_spacing = 1.15
                p.paragraph_format.space_after = Pt(2)
                p.add_run(txt)
        elif child.name in ('p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'pre', 'blockquote'):
            p = first_p if not first_p_used else cell.add_paragraph()
            first_p_used = True
            p.paragraph_format.line_spacing = 1.15
            p.paragraph_format.space_after = Pt(2)
            if child.name in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                p.paragraph_format.space_before = Pt(4)
            if child.name == 'pre':
                p.paragraph_format.left_indent = Inches(0.2)
            process_inline_nodes(child, p)
            if child.name in ('h1', 'h2', 'h3', 'h4'):
                for r in p.runs:
                    r.bold = True
            elif child.name == 'pre':
                for r in p.runs:
                    r.font.name = 'Consolas'
                    r.font.size = Pt(9.5)
        elif child.name in ('ul', 'ol'):
            for li_idx, li in enumerate(child.find_all('li', recursive=False)):
                p = first_p if not first_p_used else cell.add_paragraph()
                first_p_used = True
                p.paragraph_format.line_spacing = 1.15
                p.paragraph_format.space_after = Pt(1)
                p.paragraph_format.left_indent = Inches(0.25)
                bullet = f"{li_idx + 1}. " if child.name == 'ol' else "• "
                p.add_run(bullet)
                process_inline_nodes(li, p)
        elif child.name == 'table':
            _render_html_table(cell, child)
        elif child.name == 'img':
            p = first_p if not first_p_used else cell.add_paragraph()
            first_p_used = True
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _insert_image_from_tag(child, p, base_folders)


def _render_blocks_into_cell(cell, q_obj: dict, base_folders: list[str]):
    """Render question content blocks into a table cell."""
    blocks = q_obj.get('blocks', [])
    first_p = cell.paragraphs[0] if cell.paragraphs else cell.add_paragraph()
    first_p_used = False

    # 1. Text blocks
    text_blocks = [b for b in blocks if b.get('block_type') == 'text']
    if text_blocks:
        full_text = "\n".join(b.get('text_content') or '' for b in text_blocks)
        clean_text = re.sub(r"^(?:(?:Q(?:uestion)?\.?\s*)?\d+[.)\]:-]?\s*|\([a-z0-9]+\)\s*)+", "", full_text, flags=re.IGNORECASE).strip()
        if not clean_text:
            clean_text = full_text.strip()

        lines = clean_text.split("\n")
        for li, line in enumerate(lines):
            line_str = line.strip()
            if not line_str and 0 < li < len(lines) - 1:
                p = cell.add_paragraph()
                p.paragraph_format.space_after = Pt(2)
                continue
            if not line_str:
                continue
            p = first_p if not first_p_used else cell.add_paragraph()
            first_p_used = True
            p.paragraph_format.line_spacing = 1.15
            p.paragraph_format.space_after = Pt(2)
            p.add_run(line_str)
    else:
        fb_text = q_obj.get('text') or q_obj.get('question_text') or q_obj.get('raw_text') or ''
        if fb_text:
            clean_text = re.sub(r"^(?:(?:Q(?:uestion)?\.?\s*)?\d+[.)\]:-]?\s*|\([a-z0-9]+\)\s*)+", "", fb_text, flags=re.IGNORECASE).strip()
            p = first_p if not first_p_used else cell.add_paragraph()
            first_p_used = True
            p.paragraph_format.line_spacing = 1.15
            p.paragraph_format.space_after = Pt(2)
            p.add_run(clean_text or fb_text)

    # 2. Table blocks
    for b in blocks:
        if b.get('block_type') == 'table':
            t_data = b.get('table_data')
            if isinstance(t_data, dict):
                if is_question_bank_table(t_data) or is_question_bank_table_html(t_data.get('html', '')):
                    continue
                if t_data.get('rows'):
                    _render_dict_table(cell, t_data)
                elif t_data.get('html'):
                    soup = BeautifulSoup(t_data['html'], 'html.parser')
                    tbl = soup.find('table')
                    if tbl:
                        _render_html_table(cell, tbl)

    # 3. Filtered Figures & Images
    img_blocks = filter_question_image_blocks(blocks)
    seen_images = set()
    for b in img_blocks:
        aid = b.get('asset_id') or b.get('image_path')
        if not aid:
            continue
        fname = os.path.basename(aid.replace('\\', '/'))
        if fname in seen_images:
            continue
        seen_images.add(fname)

        img_path = None
        if base_folders:
            for fld in base_folders:
                if not fld:
                    continue
                cand = os.path.join(fld, fname)
                if os.path.isfile(cand):
                    img_path = cand
                    break

        if img_path:
            try:
                w, h = _calculate_image_dimensions(img_path, max_w_in=5.5, max_h_in=3.5)
                p_img = cell.add_paragraph()
                p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p_img.paragraph_format.space_before = Pt(4)
                p_img.paragraph_format.space_after = Pt(2)
                if w and h:
                    p_img.add_run().add_picture(img_path, width=w, height=h)
                elif w:
                    p_img.add_run().add_picture(img_path, width=w)

                caption = b.get('metadata', {}).get('caption')
                if caption:
                    p_cap = cell.add_paragraph()
                    p_cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    p_cap.paragraph_format.space_after = Pt(4)
                    r_c = p_cap.add_run(caption)
                    r_c.bold = True
                    r_c.italic = True
                    r_c.font.size = Pt(9.5)
            except Exception as e:
                logger.warning(f"Error inserting image block into DOCX: {e}")


# ── Main Export Function ──────────────────────────────────────────────────────

def export_paper_to_docx(
    paper: dict[str, Any],
    assets_folder: str | None = None,
    paper_assets_folder: str | None = None,
    bank_info: dict[str, Any] | None = None,
    header_values: dict[str, Any] | None = None,
) -> io.BytesIO:
    """
    Export a generated examination paper dictionary to a formatted Word document (.docx).
    Uses native Word paragraphs, tables, borders, and styles, preserving 100% editability.
    """
    if header_values is None:
        header_values = {}

    doc = docx.Document()

    # ── Page Setup (A4 with 15 mm / 0.59 in side margins) ─────────────
    section = doc.sections[0]
    section.page_width = Inches(8.27)    # 210 mm
    section.page_height = Inches(11.69)  # 297 mm
    section.top_margin = Inches(0.47)    # 12 mm
    section.bottom_margin = Inches(0.47) # 12 mm
    section.left_margin = Inches(0.59)   # 15 mm
    section.right_margin = Inches(0.59)  # 15 mm

    # Base typography: Times New Roman, 11pt, 1.15 line spacing
    normal_style = doc.styles['Normal']
    normal_style.font.name = 'Times New Roman'
    normal_style.font.size = Pt(11)
    normal_style.font.color.rgb = RGBColor(0, 0, 0)
    normal_style.paragraph_format.line_spacing = 1.15
    normal_style.paragraph_format.space_after = Pt(2)

    # Native Word dynamic page number footer
    footer = section.footer
    f_p = footer.paragraphs[0]
    f_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    f_p.paragraph_format.space_after = Pt(0)
    f_run = f_p.add_run("Page ")
    f_run.font.name = 'Times New Roman'
    f_run.font.size = Pt(9.5)
    _add_page_number(f_p.add_run())
    f_run2 = f_p.add_run(" of ")
    f_run2.font.name = 'Times New Roman'
    f_run2.font.size = Pt(9.5)
    _add_page_number(f_p.add_run())

    # Paths and Asset Folders
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    logo_path = os.path.join(project_root, "frontend", "assets", "PCCOE-Logo-24.png")
    if not assets_folder:
        assets_folder = os.path.join(project_root, "storage", "extracted_assets")
    if not paper_assets_folder:
        paper_assets_folder = os.path.join(project_root, "storage", "paper_assets")

    base_folders = [
        paper_assets_folder,
        assets_folder,
        os.path.join(project_root, "storage", "extracted_assets"),
        os.path.join(project_root, "storage", "assets"),
        os.path.join(project_root, "storage", "uploads"),
        os.path.join(project_root, "frontend", "assets"),
    ]

    # Resolve header metadata with priorities: header_values > bank_info > paper > defaults
    institute_status = header_values.get('header-status') or 'An Autonomous Institute'
    affiliation = header_values.get('header-affiliation') or '(Permanently affiliated to Savitribai Phule Pune University)'
    set_num = header_values.get('header-set') or 'I'
    sem_num = header_values.get('header-sem') or 'III'
    exam_title = header_values.get('header-exam-title') or 'Summative Assessment'
    programme = header_values.get('header-programme') or 'Programme: Second Year B. Tech. (Computer, IT & AIML)'
    
    course_name = (
        header_values.get('header-course-name')
        or (bank_info.get('subject') if bank_info else None)
        or paper.get('course_name')
        or 'Statistical Data Analysis Using R[Open Elective]'
    )
    course_code = header_values.get('header-course-code') or 'Course Code'
    regulation = header_values.get('header-regulation') or '2020'
    term_str = header_values.get('header-term') or 'Term: Odd Semester (Academic Year: 2025-2026)'

    total_marks = int(paper.get('paper_total', 30))
    default_duration = "1 Hr." if total_marks <= 35 else "2 Hrs."
    time_str = header_values.get('header-time') or default_duration
    total_mqs = header_values.get('header-total-questions') or str(len(paper.get('main_questions', [])))
    total_pages = header_values.get('header-total-pages') or ("1" if total_marks <= 35 else "2")

    # ── 1. College Header Box Table (2 rows x 3 cols, Total = 6.75 in) ────
    hdr_table = doc.add_table(rows=2, cols=3)
    hdr_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr_table.autofit = False

    hdr_col_widths = [Inches(1.25), Inches(4.74), Inches(1.10)]
    _set_table_column_widths(hdr_table, hdr_col_widths)

    # Row 0, Col 0: Logo merged with Row 1, Col 0 (Rowspan 2)
    cell_logo = hdr_table.cell(0, 0)
    cell_logo.merge(hdr_table.cell(1, 0))
    cell_logo.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    _set_cell_border(cell_logo, top={'val': 'single', 'sz': 4}, bottom={'val': 'single', 'sz': 4}, left={'val': 'single', 'sz': 4}, right={'val': 'single', 'sz': 4})
    _set_cell_margins(cell_logo, top=60, bottom=60, left=40, right=40)
    p_logo = cell_logo.paragraphs[0]
    p_logo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_logo.paragraph_format.space_after = Pt(0)
    if os.path.isfile(logo_path):
        try:
            p_logo.add_run().add_picture(logo_path, width=Inches(1.05))
        except Exception:
            p_logo.add_run("PCCOE").bold = True
    else:
        p_logo.add_run("PCCOE").bold = True

    # Row 0, Col 1: Institute Title
    cell_inst = hdr_table.cell(0, 1)
    cell_inst.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    _set_cell_border(cell_inst, top={'val': 'single', 'sz': 4}, bottom={'val': 'single', 'sz': 4}, left={'val': 'single', 'sz': 4}, right={'val': 'single', 'sz': 4})
    _set_cell_margins(cell_inst, top=60, bottom=60, left=80, right=80)
    p_inst = cell_inst.paragraphs[0]
    p_inst.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_inst.paragraph_format.space_after = Pt(0)
    r1 = p_inst.add_run("Pimpri Chinchwad Education Trust's\n")
    r1.font.size = Pt(9.5)
    r2 = p_inst.add_run("Pimpri Chinchwad College of Engineering\n")
    r2.font.size = Pt(11.5)
    r2.bold = True
    r3 = p_inst.add_run(f"{institute_status}\n")
    r3.font.size = Pt(9.5)
    r4 = p_inst.add_run(f"{affiliation}")
    r4.font.size = Pt(8.5)
    r4.italic = True

    # Row 0, Col 2: Set
    cell_set = hdr_table.cell(0, 2)
    cell_set.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    _set_cell_border(cell_set, top={'val': 'single', 'sz': 4}, bottom={'val': 'single', 'sz': 4}, left={'val': 'single', 'sz': 4}, right={'val': 'single', 'sz': 4})
    _set_cell_margins(cell_set, top=60, bottom=60, left=40, right=40)
    p_set = cell_set.paragraphs[0]
    p_set.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_set.paragraph_format.space_after = Pt(0)
    r_set = p_set.add_run(f"SET - {set_num}")
    r_set.bold = True
    r_set.font.size = Pt(11)

    # Row 1, Col 1: Examination Title
    cell_exam = hdr_table.cell(1, 1)
    cell_exam.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    _set_cell_border(cell_exam, top={'val': 'single', 'sz': 4}, bottom={'val': 'single', 'sz': 4}, left={'val': 'single', 'sz': 4}, right={'val': 'single', 'sz': 4})
    _set_cell_margins(cell_exam, top=40, bottom=40, left=80, right=80)
    _set_cell_shading(cell_exam, "F0F0F0")
    p_exam = cell_exam.paragraphs[0]
    p_exam.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_exam.paragraph_format.space_after = Pt(0)
    r_exam = p_exam.add_run(exam_title)
    r_exam.bold = True
    r_exam.font.size = Pt(11)

    # Row 1, Col 2: Semester
    cell_sem = hdr_table.cell(1, 2)
    cell_sem.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    _set_cell_border(cell_sem, top={'val': 'single', 'sz': 4}, bottom={'val': 'single', 'sz': 4}, left={'val': 'single', 'sz': 4}, right={'val': 'single', 'sz': 4})
    _set_cell_margins(cell_sem, top=40, bottom=40, left=40, right=40)
    p_sem = cell_sem.paragraphs[0]
    p_sem.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_sem.paragraph_format.space_after = Pt(0)
    r_sem = p_sem.add_run(f"SEM - {sem_num}")
    r_sem.bold = True
    r_sem.font.size = Pt(10)

    for r in hdr_table.rows:
        _set_row_cant_split(r)

    # ── 2. Course & Programme Information ────────────────────────────
    p_course = doc.add_paragraph()
    p_course.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_course.paragraph_format.space_before = Pt(4)
    p_course.paragraph_format.space_after = Pt(4)
    p_course.paragraph_format.line_spacing = 1.15

    r_prog = p_course.add_run(f"{programme}\n")
    r_prog.bold = True
    r_cname = p_course.add_run(f"Course: {course_name}\n")
    r_cname.bold = True
    r_cname.font.size = Pt(11.5)
    r_code = p_course.add_run(f"[{course_code}]  (Regulation: {regulation})\n")
    r_code.bold = True
    r_term = p_course.add_run(term_str)
    r_term.bold = True

    # ── 3. PRN Box Table (1 row x 10 cols, Right-Aligned) ────────────
    prn_table = doc.add_table(rows=1, cols=10)
    prn_table.alignment = WD_TABLE_ALIGNMENT.RIGHT
    prn_table.autofit = False
    _set_row_cant_split(prn_table.rows[0])

    prn_widths = [Inches(0.55)] + [Inches(0.28)] * 9
    _set_table_column_widths(prn_table, prn_widths)

    c_prn_lbl = prn_table.cell(0, 0)
    _set_cell_border(c_prn_lbl, top={'val': 'none'}, bottom={'val': 'none'}, left={'val': 'none'}, right={'val': 'none'})
    p_prnl = c_prn_lbl.paragraphs[0]
    p_prnl.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_prnl.paragraph_format.space_after = Pt(0)
    r_prnl = p_prnl.add_run("PRN: ")
    r_prnl.bold = True
    r_prnl.italic = True

    for b_idx in range(1, 10):
        cell_b = prn_table.cell(0, b_idx)
        _set_cell_border(cell_b, top={'val': 'single', 'sz': 4}, bottom={'val': 'single', 'sz': 4}, left={'val': 'single', 'sz': 4}, right={'val': 'single', 'sz': 4})
        _set_cell_margins(cell_b, top=30, bottom=30, left=20, right=20)
        p_b = cell_b.paragraphs[0]
        p_b.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_b.paragraph_format.space_after = Pt(0)
        p_b.add_run(" ")

    # ── 4. Paper Meta Information (Questions, Pages, Time, Max Marks) ─
    meta_table = doc.add_table(rows=2, cols=2)
    meta_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    meta_table.autofit = False
    _set_table_column_widths(meta_table, [Inches(3.54), Inches(3.55)])

    for r in meta_table.rows:
        _set_row_cant_split(r)
        for c in r.cells:
            _set_cell_border(c, top={'val': 'none'}, bottom={'val': 'none'}, left={'val': 'none'}, right={'val': 'none'})
            _set_cell_margins(c, top=20, bottom=20, left=0, right=0)

    # Row 0: Total Questions (left) & Printed Pages (right)
    p_m00 = meta_table.cell(0, 0).paragraphs[0]
    p_m00.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p_m00.paragraph_format.space_after = Pt(0)
    r_q = p_m00.add_run(f"Total No. of Questions: {total_mqs}")
    r_q.bold = True
    r_q.italic = True

    p_m01 = meta_table.cell(0, 1).paragraphs[0]
    p_m01.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_m01.paragraph_format.space_after = Pt(0)
    r_p = p_m01.add_run(f"Total No. of Printed Pages: {total_pages}")
    r_p.bold = True
    r_p.italic = True

    # Row 1: Time (left) & Max Marks (right)
    p_m10 = meta_table.cell(1, 0).paragraphs[0]
    p_m10.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p_m10.paragraph_format.space_after = Pt(0)
    r_t = p_m10.add_run(f"[Time: {time_str}]")
    r_t.bold = True
    r_t.italic = True

    p_m11 = meta_table.cell(1, 1).paragraphs[0]
    p_m11.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_m11.paragraph_format.space_after = Pt(0)
    r_m = p_m11.add_run(f"[Max. Marks: {total_marks}]")
    r_m.bold = True
    r_m.italic = True

    # ── 5. Candidate Instructions ────────────────────────────────────
    p_inst_hdr = doc.add_paragraph()
    p_inst_hdr.paragraph_format.space_before = Pt(4)
    p_inst_hdr.paragraph_format.space_after = Pt(1)
    r_ih = p_inst_hdr.add_run("Instructions to Candidates:")
    r_ih.bold = True

    p_imp = doc.add_paragraph()
    p_imp.paragraph_format.space_before = Pt(0)
    p_imp.paragraph_format.space_after = Pt(2)
    r_imp = p_imp.add_run("IMP: Verify that you have received a question paper with correct course, code, branch etc.")
    r_imp.bold = True
    r_imp.font.size = Pt(9.5)

    mq_list = paper.get('main_questions', [])
    attempt_pairs = []
    for i in range(0, len(mq_list) - 1, 2):
        attempt_pairs.append(f"{mq_list[i].get('label', f'Q.{i+1}')} or {mq_list[i+1].get('label', f'Q.{i+2}')}")

    instructions = [
        f"1. Answer {', '.join(attempt_pairs)}." if attempt_pairs else "1. All questions are compulsory.",
        "2. Assume suitable data wherever necessary.",
        "3. Neat diagrams must be drawn wherever necessary.",
        "4. Figures to the right side indicate full marks.",
        "5. Use of scientific non-programmable calculator is permitted."
    ]

    for inst in instructions:
        p_in = doc.add_paragraph(inst)
        p_in.paragraph_format.left_indent = Inches(0.2)
        p_in.paragraph_format.space_after = Pt(1)
        p_in.paragraph_format.line_spacing = 1.1
        for r in p_in.runs:
            r.font.size = Pt(9.5)

    p_gap = doc.add_paragraph()
    p_gap.paragraph_format.space_after = Pt(4)

    # ── 6. Main Questions Examination Grid Table ─────────────────────
    # Single unified table across all questions matching university format:
    # Col 0: Question / Subquestion Number (0.55 in)
    # Col 1: Question Statement, Tables & Figures (5.45 in)
    # Col 2: Marks (0.75 in)
    # Total = 6.75 in
    col_widths = [Inches(0.60), Inches(5.75), Inches(0.74)]

    main_table = doc.add_table(rows=0, cols=3)
    main_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    main_table.autofit = False
    _set_table_borders(main_table, sz="4")

    # Header Row
    hdr_row = main_table.add_row()
    _set_row_cant_split(hdr_row)
    trPr = hdr_row._tr.get_or_add_trPr()
    trPr.append(OxmlElement('w:tblHeader'))

    c0, c1, c2 = hdr_row.cells[0], hdr_row.cells[1], hdr_row.cells[2]
    c0.width, c1.width, c2.width = col_widths
    for c in (c0, c1, c2):
        _set_cell_margins(c, top=40, bottom=40, left=40, right=40)
        _set_cell_border(c, top={'val': 'single', 'sz': 4}, bottom={'val': 'single', 'sz': 4}, left={'val': 'single', 'sz': 4}, right={'val': 'single', 'sz': 4})
    
    p_th = c2.paragraphs[0]
    p_th.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_th.paragraph_format.space_after = Pt(0)
    r_th = p_th.add_run("Marks")
    r_th.bold = True
    r_th.font.size = Pt(10)

    for mq_idx, mq in enumerate(mq_list):
        # Insert OR Separator Row between paired alternative questions
        if mq_idx > 0 and mq_idx % 2 == 1:
            or_row = main_table.add_row()
            _set_row_cant_split(or_row)
            for ci, c in enumerate(or_row.cells):
                c.width = col_widths[ci]
                _set_cell_margins(c, top=30, bottom=30, left=40, right=40)
                _set_cell_border(c, top={'val': 'single', 'sz': 4}, bottom={'val': 'single', 'sz': 4}, left={'val': 'single', 'sz': 4}, right={'val': 'single', 'sz': 4})
                _set_cell_shading(c, "F5F5F5")
            p_or = or_row.cells[1].paragraphs[0]
            p_or.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_or.paragraph_format.space_after = Pt(0)
            r_or = p_or.add_run("OR")
            r_or.bold = True
            r_or.font.size = Pt(11.5)

        mq_label = mq.get('label', f"Q.{mq_idx + 1}")
        heading = (mq.get('instruction') or '').strip()
        rule = mq.get('attempt_rule', 'ALL')
        if rule == 'ALL':
            if not heading or heading.lower() in ('attempt the following', 'attempt all'):
                heading = 'Answer the following questions'
        elif rule == 'ANY_N':
            if not heading or heading.lower().startswith('attempt any') or heading.lower() == 'attempt the following':
                count = mq.get('attempt_count') or 2
                heading = f"Answer the following (Any {count})"

        # Main Question Row
        mq_row = main_table.add_row()
        _set_row_cant_split(mq_row)
        for ci, c in enumerate(mq_row.cells):
            c.width = col_widths[ci]
            _set_cell_margins(c, top=40, bottom=40, left=60, right=60)
            _set_cell_border(c, top={'val': 'single', 'sz': 4}, bottom={'val': 'single', 'sz': 4}, left={'val': 'single', 'sz': 4}, right={'val': 'single', 'sz': 4})
            _set_cell_shading(c, "F9F9F9")

        # Col 0: Main Question Label (e.g. Q.1)
        p_mql = mq_row.cells[0].paragraphs[0]
        p_mql.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_mql.paragraph_format.space_after = Pt(0)
        p_mql.add_run(mq_label).bold = True

        # Col 1: Instruction heading
        p_mqh = mq_row.cells[1].paragraphs[0]
        p_mqh.paragraph_format.space_after = Pt(0)
        p_mqh.add_run(heading).bold = True

        # Col 2: Target Marks (for ANY_N questions)
        p_mqm = mq_row.cells[2].paragraphs[0]
        p_mqm.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_mqm.paragraph_format.space_after = Pt(0)
        if rule == 'ANY_N' and mq.get('target_marks'):
            t_m = float(mq['target_marks'])
            m_lbl = f"[{int(t_m) if t_m.is_integer() else t_m}M]"
            p_mqm.add_run(m_lbl).bold = True

        # Subquestion Rows
        subquestions = mq.get('subquestions', [])
        for sq_idx, sq in enumerate(subquestions):
            sq_label = sq.get('label')
            if not sq_label:
                sq_label = f"{sq_idx + 1}." if rule == 'ANY_N' else chr(65 + sq_idx)

            sq_marks = sq.get('marks') or 5
            q_obj = sq.get('question') or {}
            is_filled = bool(sq.get('is_filled', True)) and bool(q_obj)

            sq_row = main_table.add_row()
            _set_row_cant_split(sq_row)
            c_lbl, c_body, c_mrk = sq_row.cells[0], sq_row.cells[1], sq_row.cells[2]
            c_lbl.width, c_body.width, c_mrk.width = col_widths

            for c in (c_lbl, c_body, c_mrk):
                c.vertical_alignment = WD_ALIGN_VERTICAL.TOP
                _set_cell_margins(c, top=50, bottom=50, left=60, right=60)
                _set_cell_border(c, top={'val': 'single', 'sz': 4}, bottom={'val': 'single', 'sz': 4}, left={'val': 'single', 'sz': 4}, right={'val': 'single', 'sz': 4})

            # Column 0: Subquestion letter (A, B, C...) or number (1., 2...)
            p_sl = c_lbl.paragraphs[0]
            p_sl.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_sl.paragraph_format.space_after = Pt(0)
            p_sl.add_run(f"{sq_label}").bold = True

            # Column 1: Statement Text, Tables, and Figures
            if not is_filled:
                p_empty = c_body.paragraphs[0]
                r_emp = p_empty.add_run("(No question assigned)")
                r_emp.italic = True
                r_emp.font.color.rgb = RGBColor(128, 128, 128)
            elif q_obj.get('html'):
                _render_html_into_cell(c_body, q_obj['html'], base_folders)
            else:
                _render_blocks_into_cell(c_body, q_obj, base_folders)

            # Column 2: Marks and CO/BL tags
            p_sm = c_mrk.paragraphs[0]
            p_sm.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_sm.paragraph_format.space_after = Pt(0)
            marks_val = float(sq_marks)
            m_txt = f"[{int(marks_val) if marks_val.is_integer() else marks_val}M]"
            p_sm.add_run(m_txt).bold = True

            co = q_obj.get('course_outcome')
            bl = q_obj.get('bloom_level')
            tags = []
            if co:
                tags.append(str(co))
            if bl:
                tags.append(str(bl))
            if tags:
                p_sm.add_run("\n")
                r_tag = p_sm.add_run(f"[{' / '.join(tags)}]")
                r_tag.font.size = Pt(8.5)
                r_tag.font.color.rgb = RGBColor(100, 100, 100)

    # Enforce explicit column widths across the entire main question table
    _set_table_column_widths(main_table, col_widths)

    # ── 7. End of Paper Marker ───────────────────────────────────────
    p_end = doc.add_paragraph()
    p_end.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_end.paragraph_format.space_before = Pt(14)
    p_end.paragraph_format.space_after = Pt(14)
    r_end = p_end.add_run("**** End of Question Paper ****")
    r_end.bold = True
    r_end.font.size = Pt(11)

    # Save to buffer and return
    output_io = io.BytesIO()
    doc.save(output_io)
    output_io.seek(0)
    return output_io
