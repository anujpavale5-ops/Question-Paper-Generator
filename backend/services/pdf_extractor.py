"""
PDF extraction service.

Uses PyMuPDF (pymupdf) for text/geometry/image inspection
and pdfplumber for native table extraction.

Design decisions:
- Text extraction uses PyMuPDF's dict output for word-level geometry.
- Tables are extracted by pdfplumber; only accepted if they pass
  a minimum-content validation.
- Embedded images are saved as assets and referenced by asset_id.
- Pages with very little selectable text are flagged as likely scanned.
- Deduplication fingerprinting prevents the same content appearing in
  both the text and table paths.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

import pymupdf as fitz
import pdfplumber
from PIL import Image

from backend.schema import (
    BlockType, ContentBlock, DocumentResult, ExtractionMethod,
    Geometry, ProcessingSummary, SourceFormat, TableCell,
    TableData, TableRow, ValidationStatus,
)
from backend.services.table_reconstructor import TableReconstructor
from backend.services.geometric_table_extractor import GeometricTableExtractor
from backend.services.image_table_recognizer import ImageTableRecognizer

logger = logging.getLogger(__name__)

# Pages with fewer characters than this are treated as scanned / image-only.
SCANNED_TEXT_THRESHOLD = 50
# Minimum rows × columns to accept a pdfplumber table as a real table.
MIN_TABLE_CELLS = 2

reconstructor = TableReconstructor()


class PdfExtractor:
    """Extract a PDF into a DocumentResult."""

    def __init__(self, assets_folder: str):
        self.assets_folder = assets_folder
        os.makedirs(assets_folder, exist_ok=True)
        self.geometric_extractor = GeometricTableExtractor()
        self.table_recognizer = ImageTableRecognizer()

    # ──────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────

    def extract(self, pdf_path: str, document_id: str, filename: str) -> DocumentResult:
        result = DocumentResult(
            document_id=document_id,
            filename=filename,
            source_format=SourceFormat.PDF,
        )
        result.summary.total_pages = 0

        try:
            self._extract_all(pdf_path, document_id, result)
        except Exception as exc:
            logger.exception("PDF extraction failed for %s", pdf_path)
            result.summary.warnings.append(f"Extraction error: {exc}")

        result.compute_summary()
        return result

    # ──────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────

    def _extract_all(self, pdf_path: str, document_id: str, result: DocumentResult) -> None:
        mupdf_doc  = fitz.open(pdf_path)
        reading_order = 0

        # Pre-pass: detect repeating watermark / background drawing clusters across pages
        repeating_watermarks: set[tuple[int, int, int, int]] = set()
        try:
            from collections import Counter
            watermark_counter = Counter()
            for p in mupdf_doc:
                for d in p.get_drawings():
                    r = fitz.Rect(d['rect'])
                    if r.width < 5 or r.height < 5 or r.width > p.rect.width * 0.95:
                        continue
                    k = (round(r.x0, -1), round(r.y0, -1), round(r.x1, -1), round(r.y1, -1))
                    watermark_counter[k] += 1
            repeating_watermarks = {k for k, v in watermark_counter.items() if v >= 3}
        except Exception as e:
            logger.debug("Watermark pre-pass error: %s", e)

        with pdfplumber.open(pdf_path) as plumber_doc:
            result.summary.total_pages = len(mupdf_doc)

            for page_idx in range(len(mupdf_doc)):
                mupdf_page   = mupdf_doc[page_idx]
                plumber_page = plumber_doc.pages[page_idx]
                page_num     = page_idx + 1

                # ── Determine if page is scanned ─────────────────────
                page_text = mupdf_page.get_text("text").strip()
                is_scanned = len(page_text) < SCANNED_TEXT_THRESHOLD
                if is_scanned:
                    result.summary.scanned_pages += 1

                # ── Extract native tables first (pdfplumber) ─────────
                table_rects: list[tuple[float, float, float, float]] = []
                table_blocks_on_page: list[ContentBlock] = []

                if not is_scanned:
                    table_blocks_on_page, table_rects, reading_order = self._extract_tables(
                        plumber_page, mupdf_page, page_num, document_id, reading_order
                    )
                    
                    geom_tables = self.geometric_extractor.extract_tables(mupdf_page, table_rects)
                    for gt in geom_tables:
                        gt.table_data.html = reconstructor.to_html(gt.table_data)
                        validation = self._validate_table(gt.table_data)
                        geom = Geometry(
                            x0=gt.bbox[0], y0=gt.bbox[1], x1=gt.bbox[2], y1=gt.bbox[3],
                            page_width=mupdf_page.rect.width, page_height=mupdf_page.rect.height
                        )
                        block = ContentBlock(
                            block_type=BlockType.TABLE,
                            source_format=SourceFormat.PDF,
                            page_number=page_num,
                            reading_order=reading_order,
                            extraction_method=ExtractionMethod.GEOMETRIC_TABLE,
                            validation_status=validation,
                            table_data=gt.table_data,
                            geometry=geom,
                            notes=["Extracted via geometric fallback."]
                        )
                        table_blocks_on_page.append(block)
                        reading_order += 1
                        table_rects.append(gt.bbox)

                    # ── Discover any remaining tables via PyMuPDF native table finder ──
                    try:
                        mupdf_tabs = mupdf_page.find_tables()
                        for mt in (mupdf_tabs.tables if mupdf_tabs else []):
                            bbox = (mt.bbox[0], mt.bbox[1], mt.bbox[2], mt.bbox[3])
                            if not self._overlaps_any_table(bbox, table_rects):
                                extracted_data = mt.extract()
                                if extracted_data and len(extracted_data) >= 2:
                                    t_rows = []
                                    for ri, r in enumerate(extracted_data):
                                        t_cells = [
                                            TableCell(text=str(c or '').strip(), row_index=ri, col_index=ci, is_header=(ri == 0))
                                            for ci, c in enumerate(r)
                                        ]
                                        r_bbox = None
                                        try:
                                            if hasattr(mt, 'rows') and ri < len(mt.rows):
                                                mb = mt.rows[ri].bbox
                                                r_bbox = (mb[0], mb[1], mb[2], mb[3])
                                        except Exception:
                                            r_bbox = None
                                        t_rows.append(TableRow(cells=t_cells, row_index=ri, is_header=(ri == 0), bbox=r_bbox))
                                    col_cnt = max(len(r.cells) for r in t_rows) if t_rows else 0
                                    t_data = TableData(rows=t_rows, column_count=col_cnt, row_count=len(t_rows), has_header=True)
                                    t_data.html = reconstructor.to_html(t_data)
                                    geom = Geometry(
                                        x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3],
                                        page_width=mupdf_page.rect.width, page_height=mupdf_page.rect.height,
                                    )
                                    blk = ContentBlock(
                                        block_type=BlockType.TABLE,
                                        source_format=SourceFormat.PDF,
                                        page_number=page_num,
                                        reading_order=reading_order,
                                        extraction_method=ExtractionMethod.NATIVE_TABLE,
                                        validation_status=ValidationStatus.VALID,
                                        table_data=t_data,
                                        geometry=geom,
                                        notes=["Extracted via PyMuPDF native table finder."],
                                    )
                                    table_blocks_on_page.append(blk)
                                    reading_order += 1
                                    table_rects.append(bbox)
                    except Exception as exc:
                        logger.debug("PyMuPDF find_tables error on page %d: %s", page_num, exc)

                # ── Extract text blocks (PyMuPDF) ────────────────────
                text_blocks: list[ContentBlock] = []
                if not is_scanned:
                    text_blocks, reading_order = self._extract_text_blocks(
                        mupdf_page, page_num, reading_order, table_rects
                    )

                # ── Extract images ───────────────────────────────────
                image_blocks: list[ContentBlock] = []
                if not is_scanned:
                    image_blocks, reading_order = self._extract_images(
                        mupdf_doc, mupdf_page, page_num, document_id, reading_order, is_scanned
                    )

                # ── Extract vector drawings / diagrams ───────────────
                drawing_blocks: list[ContentBlock] = []
                if not is_scanned:
                    drawing_blocks, reading_order = self._extract_vector_drawings(
                        mupdf_page, page_num, document_id, reading_order, table_rects, image_blocks, repeating_watermarks
                    )

                # ── Check visual blocks for image-based tables ───────
                # Reconstruct image-based tables into structured editable TableData
                # Embedded raster images are checked for table grids
                visual_candidates = image_blocks
                for vb in visual_candidates:
                    if vb.block_type in (BlockType.IMAGE, BlockType.FIGURE) and vb.asset_id:
                        asset_path = os.path.join(self.assets_folder, vb.asset_id)
                        if os.path.isfile(asset_path):
                            ocr_tbl_res = self.table_recognizer.recognize_table(asset_path, vb.asset_id)
                            if ocr_tbl_res.success and ocr_tbl_res.table_data:
                                vb.block_type = BlockType.TABLE
                                vb.extraction_method = ExtractionMethod.OCR_TABLE
                                vb.table_data = ocr_tbl_res.table_data
                                vb.validation_status = ocr_tbl_res.validation_status
                                vb.metadata["is_reconstructed_table"] = True
                                vb.metadata["original_asset_id"] = vb.asset_id
                                vb.metadata["table_type"] = ocr_tbl_res.table_type
                                vb.notes.append("Reconstructed from image-based table via local OCR.")

                # ── Interleave all blocks on the page in geometric reading order ──
                page_all_blocks = text_blocks + table_blocks_on_page + image_blocks + drawing_blocks
                page_all_blocks.sort(
                    key=lambda b: (
                        b.geometry.y0 if b.geometry else 0.0,
                        b.geometry.x0 if b.geometry else 0.0,
                    )
                )
                for b in page_all_blocks:
                    b.reading_order = reading_order
                    reading_order += 1
                    result.blocks.append(b)

                # ── If scanned, flag the page ────────────────────────
                if is_scanned:
                    note_block = ContentBlock(
                        block_type=BlockType.UNKNOWN,
                        source_format=SourceFormat.PDF,
                        page_number=page_num,
                        reading_order=reading_order,
                        extraction_method=ExtractionMethod.UNKNOWN,
                        validation_status=ValidationStatus.SKIPPED,
                        notes=[
                            "Page appears to be scanned (little or no selectable text). "
                            "OCR required for text content. Images extracted where possible."
                        ],
                    )
                    result.blocks.append(note_block)
                    reading_order += 1

        mupdf_doc.close()

    # ── Text extraction ───────────────────────────────────────────

    def _extract_text_blocks(
        self,
        page: fitz.Page,
        page_num: int,
        reading_order: int,
        table_rects: list[tuple[float, float, float, float]],
    ) -> tuple[list[ContentBlock], int]:
        """
        Extract text using PyMuPDF block layout.
        Skip any blocks whose bounding rect overlaps a known table region.
        """
        blocks_out: list[ContentBlock] = []
        page_dict = page.get_text("dict", sort=True)
        page_width  = page.rect.width
        page_height = page.rect.height

        for raw_block in page_dict.get("blocks", []):
            if raw_block.get("type") != 0:   # 0 = text block
                continue

            bbox = raw_block.get("bbox", (0, 0, 0, 0))

            # Skip any blocks whose bounding rect heavily overlaps a known table region
            is_inside_table = False
            for gx0, gy0, gx1, gy1 in table_rects:
                ix0, iy0 = max(bbox[0], gx0), max(bbox[1], gy0)
                ix1, iy1 = min(bbox[2], gx1), min(bbox[3], gy1)
                if ix1 > ix0 and iy1 > iy0:
                    inter_area = (ix1 - ix0) * (iy1 - iy0)
                    tb_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                    if tb_area > 0 and (inter_area / tb_area) >= 0.6:
                        is_inside_table = True
                        break
            if is_inside_table:
                continue

            text = self._extract_text_from_block(raw_block)
            if not text.strip():
                continue

            block = ContentBlock(
                block_type=BlockType.TEXT,
                source_format=SourceFormat.PDF,
                page_number=page_num,
                reading_order=reading_order,
                extraction_method=ExtractionMethod.NATIVE_TEXT,
                validation_status=ValidationStatus.VALID,
                text=text.strip(),
                geometry=Geometry(
                    x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3],
                    page_width=page_width, page_height=page_height,
                ),
            )
            blocks_out.append(block)
            reading_order += 1

        return blocks_out, reading_order

    def _extract_text_from_block(self, raw_block: dict) -> str:
        lines = []
        for line in raw_block.get("lines", []):
            spans = [s.get("text", "") for s in line.get("spans", [])]
            line_text = "".join(spans)
            if line_text.strip():
                lines.append(line_text)
        return "\n".join(lines)

    # ── Table extraction ──────────────────────────────────────────

    def _extract_tables(
        self,
        plumber_page: Any,
        mupdf_page: fitz.Page,
        page_num: int,
        document_id: str,
        reading_order: int,
    ) -> tuple[list[ContentBlock], list[tuple[float, float, float, float]], int]:
        blocks_out: list[ContentBlock] = []
        table_rects: list[tuple[float, float, float, float]] = []

        try:
            tables = plumber_page.extract_tables()
        except Exception as exc:
            logger.warning("pdfplumber table extraction failed on page %d: %s", page_num, exc)
            return blocks_out, table_rects, reading_order

        # Also get table bboxes for overlap detection
        try:
            finder_tables = plumber_page.find_tables()
        except Exception:
            finder_tables = []

        for idx, raw_table in enumerate(tables):
            if not raw_table:
                continue

            # Get bbox from finder if available
            table_bbox = None
            row_bboxes: list[tuple[float, float, float, float] | None] | None = None
            if idx < len(finder_tables):
                try:
                    tb = finder_tables[idx].bbox
                    table_bbox = (tb[0], tb[1], tb[2], tb[3])
                    ft_rows = finder_tables[idx].rows
                    if len(ft_rows) == len(raw_table):
                        row_bboxes = [(r.bbox[0], r.bbox[1], r.bbox[2], r.bbox[3]) for r in ft_rows]
                except Exception:
                    pass

            # Validate minimum content
            flat_cells = [c.strip() for row in raw_table for c in row if c and c.strip()]
            if len(flat_cells) < MIN_TABLE_CELLS or (len(flat_cells) == 2 and not any(len(c) >= 10 for c in flat_cells)):
                continue

            table_data = self._build_table_data(raw_table, row_bboxes=row_bboxes)
            if table_data is None:
                continue

            table_data.html = reconstructor.to_html(table_data)
            validation = self._validate_table(table_data)

            geom: Geometry | None = None
            if table_bbox:
                table_rects.append(table_bbox)
                geom = Geometry(
                    x0=table_bbox[0], y0=table_bbox[1],
                    x1=table_bbox[2], y1=table_bbox[3],
                    page_width=mupdf_page.rect.width,
                    page_height=mupdf_page.rect.height,
                )

            block = ContentBlock(
                block_type=BlockType.TABLE,
                source_format=SourceFormat.PDF,
                page_number=page_num,
                reading_order=reading_order,
                extraction_method=ExtractionMethod.NATIVE_TABLE,
                validation_status=validation,
                table_data=table_data,
                geometry=geom,
                metadata={"table_index_on_page": idx},
            )
            blocks_out.append(block)
            reading_order += 1

        return blocks_out, table_rects, reading_order

    def _build_table_data(self, raw_table: list[list[str | None]], row_bboxes: list[tuple[float, float, float, float] | None] | None = None) -> TableData | None:
        if not raw_table:
            return None
        rows: list[TableRow] = []
        col_count = max(len(r) for r in raw_table)
        for ri, raw_row in enumerate(raw_table):
            is_header = (ri == 0)
            cells: list[TableCell] = []
            for ci, cell_val in enumerate(raw_row):
                text = str(cell_val).strip() if cell_val is not None else ""
                cells.append(TableCell(
                    text=text, row_index=ri, col_index=ci, is_header=is_header
                ))
            # Pad short rows
            while len(cells) < col_count:
                cells.append(TableCell(text="", row_index=ri, col_index=len(cells)))
            r_bbox = row_bboxes[ri] if (row_bboxes and ri < len(row_bboxes)) else None
            rows.append(TableRow(cells=cells, row_index=ri, is_header=is_header, bbox=r_bbox))

        return TableData(
            rows=rows,
            column_count=col_count,
            row_count=len(rows),
            has_header=(len(rows) > 0),
        )

    def _validate_table(self, table_data: TableData) -> ValidationStatus:
        if table_data.row_count == 0 or table_data.column_count == 0:
            return ValidationStatus.FAILED
        non_empty = sum(
            1 for row in table_data.rows for cell in row.cells if cell.text.strip()
        )
        total_cells = table_data.row_count * table_data.column_count
        if total_cells > 0 and non_empty / total_cells < 0.1:
            return ValidationStatus.SUSPECT
        if not table_data.html or "<table" not in table_data.html:
            return ValidationStatus.SUSPECT
        return ValidationStatus.VALID

    # ── Image extraction ──────────────────────────────────────────

    def _extract_images(
        self,
        doc: fitz.Document,
        page: fitz.Page,
        page_num: int,
        document_id: str,
        reading_order: int,
        is_scanned: bool,
    ) -> tuple[list[ContentBlock], int]:
        """
        Extract embedded images with connected-component clustering.
        Abutting or overlapping slices belonging to the same figure/diagram are unified
        and rendered directly from the PDF at 200 DPI to avoid clipped diagrams.
        """
        blocks_out: list[ContentBlock] = []
        page_width  = page.rect.width
        page_height = page.rect.height

        raw_items: list[dict[str, Any]] = []
        seen_xrefs: set[int] = set()
        image_list = page.get_images(full=True)

        for img_info in image_list:
            xref = img_info[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            try:
                rects = page.get_image_rects(xref)
                if not rects:
                    continue
                r = rects[0]
                if r.width < 5 and r.height < 5:
                    continue
                raw_items.append({
                    "xref": xref,
                    "rect": r,
                    "info": img_info,
                })
            except Exception as exc:
                logger.debug("Could not inspect image rect xref=%d: %s", xref, exc)

        if not raw_items:
            return blocks_out, reading_order

        # Connected-component grouping for adjacent/sliced visual fragments
        n = len(raw_items)
        adj: dict[int, set[int]] = {i: set() for i in range(n)}
        for i in range(n):
            for j in range(i + 1, n):
                r1 = raw_items[i]["rect"]
                r2 = raw_items[j]["rect"]
                # 1. Overlapping or intersecting bounds
                if r1.intersects(r2):
                    adj[i].add(j)
                    adj[j].add(i)
                    continue
                # 2. Vertical slices: high horizontal overlap and small vertical gap
                x_overlap = max(0.0, min(r1.x1, r2.x1) - max(r1.x0, r2.x0))
                min_w = min(r1.width, r2.width)
                v_gap = max(0.0, max(r1.y0, r2.y0) - min(r1.y1, r2.y1))
                if min_w > 0 and (x_overlap / min_w) >= 0.70 and v_gap <= 8.0:
                    adj[i].add(j)
                    adj[j].add(i)
                    continue
                # 3. Horizontal slices / adjacent fragments: high vertical overlap and small horizontal gap
                y_overlap = max(0.0, min(r1.y1, r2.y1) - max(r1.y0, r2.y0))
                min_h = min(r1.height, r2.height)
                h_gap = max(0.0, max(r1.x0, r2.x0) - min(r1.x1, r2.x1))
                if min_h > 0 and (y_overlap / min_h) >= 0.70 and h_gap <= 8.0:
                    adj[i].add(j)
                    adj[j].add(i)
                    continue

        visited: set[int] = set()
        groups: list[list[int]] = []
        for i in range(n):
            if i in visited:
                continue
            comp: list[int] = []
            queue = [i]
            visited.add(i)
            while queue:
                curr = queue.pop(0)
                comp.append(curr)
                for neighbor in adj[curr]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            groups.append(comp)

        # Detect nearby figure captions (e.g. "Fig 1.1", "Figure 2:", "Diagram:")
        text_blocks = page.get_text("blocks")
        caption_candidates: list[dict[str, Any]] = []
        for tb in text_blocks:
            txt = tb[4].strip()
            if re.match(r"^(?:Fig(?:ure)?\.?\s*[\d.]+|Diagram\s*[\d.]+|Graph\s*[\d.]+|Model\s*[\d.]+)", txt, re.I):
                caption_candidates.append({
                    "rect": fitz.Rect(tb[0], tb[1], tb[2], tb[3]),
                    "text": txt,
                })

        fig_cluster_counter = 0
        for g in groups:
            g_rect = fitz.Rect()
            group_xrefs: list[int] = []
            for idx in g:
                g_rect |= raw_items[idx]["rect"]
                group_xrefs.append(raw_items[idx]["xref"])

            caption_text = None
            for cap in caption_candidates:
                cr = cap["rect"]
                # Within 15pt vertically and horizontally aligned with the figure
                if (abs(cr.y0 - g_rect.y1) <= 15.0 or abs(g_rect.y0 - cr.y1) <= 15.0) and (
                    max(g_rect.x0, cr.x0) < min(g_rect.x1, cr.x1) + 20.0
                ):
                    g_rect |= cr
                    caption_text = cap["text"].split("\n")[0].strip()
                    break

            # If multi-image cluster OR caption attached: render region at 200 DPI for pristine quality
            if len(g) > 1 or caption_text:
                fig_cluster_counter += 1
                asset_filename = f"{document_id}_p{page_num}_fig_{fig_cluster_counter}.png"
                asset_path = os.path.join(self.assets_folder, asset_filename)

                pad = 4.0
                padded = fitz.Rect(
                    max(0.0, g_rect.x0 - pad),
                    max(0.0, g_rect.y0 - pad),
                    min(page_width, g_rect.x1 + pad),
                    min(page_height, g_rect.y1 + pad),
                )
                try:
                    pix = page.get_pixmap(clip=padded, dpi=200)
                    pix.save(asset_path)

                    geom = Geometry(
                        x0=g_rect.x0, y0=g_rect.y0, x1=g_rect.x1, y1=g_rect.y1,
                        page_width=page_width, page_height=page_height,
                    )
                    block = ContentBlock(
                        block_type=BlockType.FIGURE,
                        source_format=SourceFormat.PDF,
                        page_number=page_num,
                        reading_order=reading_order,
                        extraction_method=ExtractionMethod.RENDERED_REGION,
                        validation_status=ValidationStatus.VALID,
                        asset_id=asset_filename,
                        geometry=geom,
                        metadata={
                            "figure_cluster": True,
                            "slices": len(g),
                            "xrefs": group_xrefs,
                            "caption": caption_text,
                            "width": pix.width,
                            "height": pix.height,
                        },
                    )
                    if is_scanned:
                        block.notes.append(
                            "Extracted from a page flagged as scanned. "
                            "This image may contain text that requires OCR."
                        )
                    blocks_out.append(block)
                    reading_order += 1
                except Exception as exc:
                    logger.debug("Failed rendering figure region on page %d: %s", page_num, exc)

            else:
                # Standalone single image
                xref = group_xrefs[0]
                try:
                    base_image = doc.extract_image(xref)
                    img_bytes  = base_image["image"]
                    img_ext    = base_image["ext"]
                    img_width  = base_image.get("width", 0)
                    img_height = base_image.get("height", 0)

                    # Skip tiny images (bullets, small icons)
                    if img_width < 60 or img_height < 60:
                        continue

                    asset_filename = f"{document_id}_p{page_num}_{xref}.{img_ext}"
                    asset_path     = os.path.join(self.assets_folder, asset_filename)
                    with open(asset_path, "wb") as f:
                        f.write(img_bytes)

                    block_type = BlockType.FIGURE if (img_width > 200 and img_height > 200) else BlockType.IMAGE
                    geom = Geometry(
                        x0=g_rect.x0, y0=g_rect.y0, x1=g_rect.x1, y1=g_rect.y1,
                        page_width=page_width, page_height=page_height,
                    )
                    block = ContentBlock(
                        block_type=block_type,
                        source_format=SourceFormat.PDF,
                        page_number=page_num,
                        reading_order=reading_order,
                        extraction_method=ExtractionMethod.NATIVE_IMAGE,
                        validation_status=ValidationStatus.VALID,
                        asset_id=asset_filename,
                        geometry=geom,
                        metadata={
                            "image_width": img_width,
                            "image_height": img_height,
                            "image_ext": img_ext,
                            "xref": xref,
                        },
                    )
                    if is_scanned:
                        block.notes.append(
                            "Extracted from a page flagged as scanned. "
                            "This image may contain text that requires OCR."
                        )
                    blocks_out.append(block)
                    reading_order += 1
                except Exception as exc:
                    logger.debug("Could not extract image xref=%d on page %d: %s", xref, page_num, exc)

        return blocks_out, reading_order

    def _extract_vector_drawings(
        self,
        page: fitz.Page,
        page_num: int,
        document_id: str,
        reading_order: int,
        table_rects: list[tuple[float, float, float, float]],
        existing_image_blocks: list[ContentBlock],
        repeating_watermarks: set[tuple[int, int, int, int]],
    ) -> tuple[list[ContentBlock], int]:
        blocks_out: list[ContentBlock] = []
        try:
            drawings = page.get_drawings()
            if not drawings:
                return blocks_out, reading_order

            candidates = []
            for d in drawings:
                r = fitz.Rect(d['rect'])
                if r.width < 2 or r.height < 2:
                    continue
                items = d.get('items', [])
                if len(items) == 1 and items[0][0] == 're' and (r.width > 250 or (r.width < 25 and r.height < 25)):
                    continue
                k = (round(r.x0, -1), round(r.y0, -1), round(r.x1, -1), round(r.y1, -1))
                if k in repeating_watermarks:
                    continue
                candidates.append(d)

            if not candidates:
                return blocks_out, reading_order

            # Cluster close drawing elements
            clusters: list[fitz.Rect] = []
            for d in candidates:
                r = fitz.Rect(d['rect'])
                merged = False
                for i, c in enumerate(clusters):
                    if fitz.Rect(c.x0 - 15, c.y0 - 15, c.x1 + 15, c.y1 + 15).intersects(r):
                        clusters[i] = c | r
                        merged = True
                        break
                if not merged:
                    clusters.append(r)

            changed = True
            while changed:
                changed = False
                new_c = []
                while clusters:
                    cur = clusters.pop(0)
                    merged = False
                    for i, other in enumerate(clusters):
                        if fitz.Rect(cur.x0 - 15, cur.y0 - 15, cur.x1 + 15, cur.y1 + 15).intersects(other):
                            clusters[i] = other | cur
                            merged = True
                            changed = True
                            break
                    if not merged:
                        new_c.append(cur)
                clusters = new_c

            existing_image_rects = [
                fitz.Rect(b.geometry.x0, b.geometry.y0, b.geometry.x1, b.geometry.y1)
                for b in existing_image_blocks if b.geometry
            ]
            existing_table_rects = [
                fitz.Rect(tr[0], tr[1], tr[2], tr[3])
                for tr in table_rects
            ]

            diag_idx = 0
            for c in clusters:
                if c.width < 50 or c.height < 40:
                    continue
                if c.width > page.rect.width * 0.92 or c.height > page.rect.height * 0.92:
                    continue
                k = (round(c.x0, -1), round(c.y0, -1), round(c.x1, -1), round(c.y1, -1))
                if k in repeating_watermarks:
                    continue
                # Skip if already covered by an extracted raster image
                if any(c.intersects(ir) for ir in existing_image_rects):
                    continue
                # Skip if already covered by an extracted native table
                if any(
                    c.intersects(tr) and (
                        (abs(c & tr) / max(1.0, c.get_area()) > 0.35) or
                        (abs(c & tr) / max(1.0, tr.get_area()) > 0.35)
                    )
                    for tr in existing_table_rects
                ):
                    continue

                diag_idx += 1
                asset_filename = f"{document_id}_p{page_num}_vec_{diag_idx}.png"
                asset_path = os.path.join(self.assets_folder, asset_filename)

                pad = 4.0
                padded = fitz.Rect(
                    max(0.0, c.x0 - pad),
                    max(0.0, c.y0 - pad),
                    min(page.rect.width, c.x1 + pad),
                    min(page.rect.height, c.y1 + pad)
                )
                pix = page.get_pixmap(clip=padded, dpi=200)
                pix.save(asset_path)

                geom = Geometry(
                    x0=c.x0, y0=c.y0, x1=c.x1, y1=c.y1,
                    page_width=page.rect.width, page_height=page.rect.height
                )
                block = ContentBlock(
                    block_type=BlockType.FIGURE,
                    source_format=SourceFormat.PDF,
                    page_number=page_num,
                    reading_order=reading_order,
                    extraction_method=ExtractionMethod.RENDERED_REGION,
                    validation_status=ValidationStatus.VALID,
                    asset_id=asset_filename,
                    geometry=geom,
                    metadata={"vector_cluster": True, "width": pix.width, "height": pix.height}
                )
                blocks_out.append(block)
                reading_order += 1

        except Exception as exc:
            logger.debug("Vector drawings extraction failed on page %d: %s", page_num, exc)

        return blocks_out, reading_order

    # ── Geometry helpers ──────────────────────────────────────────

    @staticmethod
    def _overlaps_any_table(
        bbox: tuple[float, float, float, float],
        table_rects: list[tuple[float, float, float, float]],
        tolerance: float = 5.0,
    ) -> bool:
        x0, y0, x1, y1 = bbox
        for tx0, ty0, tx1, ty1 in table_rects:
            ox = max(0.0, min(x1, tx1) - max(x0, tx0) - tolerance)
            oy = max(0.0, min(y1, ty1) - max(y0, ty0) - tolerance)
            if ox > 0 and oy > 0:
                return True
        return False
