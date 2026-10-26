"""
Image Table Recognizer — Local OCR and Computer Vision Table Detection and Reconstruction.

Capabilities:
1. Local Grid Detection: Uses OpenCV morphological analysis to identify bordered table grids,
   cell boundaries, row lines, and column dividers without external cloud APIs.
2. Borderless Table Detection: Uses RapidOCR spatial text layout analysis to detect aligned
   tabular columns across multiple rows.
3. Diagram & Non-Table Preservation: Distinguishes genuine tables from neural network diagrams,
   flowcharts, circuit diagrams, graphs, and illustrations.
4. Cell-Level Text Association: Spatially maps recognized OCR text into discrete table cells.
5. Structure Preservation: Preserves empty cells, headers, and column/row counts.
6. Full Fallback & Auditability: Retains original asset ID and source crop for comparison and recovery.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from backend.schema import TableCell, TableData, TableRow, ValidationStatus
from backend.services.table_reconstructor import TableReconstructor

logger = logging.getLogger(__name__)

# Try importing local OCR and CV dependencies
try:
    import cv2
    _CV2_AVAILABLE = True
except ImportError:
    cv2 = None
    _CV2_AVAILABLE = False

try:
    from rapidocr_onnxruntime import RapidOCR
    _RAPIDOCR_AVAILABLE = True
except ImportError:
    RapidOCR = None
    _RAPIDOCR_AVAILABLE = False


@dataclass
class OcrTableResult:
    success: bool
    table_data: TableData | None
    fallback_asset_id: str | None          # original image path/filename
    error: str = ""
    table_type: str = "none"              # "bordered" | "borderless" | "none"
    confidence: float = 1.0
    validation_status: ValidationStatus = ValidationStatus.SKIPPED
    uncertain_cells: list[dict[str, Any]] = field(default_factory=list)


class ImageTableRecognizer:
    """
    Local Computer Vision and OCR Table Recognizer.
    Identifies and reconstructs image-based tables into structured, editable TableData.
    """

    def __init__(self):
        self._ocr = None
        self._reconstructor = TableReconstructor()

    @property
    def available(self) -> bool:
        """Returns True if local OCR engine (RapidOCR) and OpenCV are available."""
        return _RAPIDOCR_AVAILABLE and _CV2_AVAILABLE

    def _get_ocr(self):
        """Lazy-initialize RapidOCR instance."""
        if not _RAPIDOCR_AVAILABLE:
            return None
        if self._ocr is None:
            try:
                self._ocr = RapidOCR()
            except Exception as exc:
                logger.warning("Failed to initialize RapidOCR: %s", exc)
                return None
        return self._ocr

    # ──────────────────────────────────────────────────────────────────
    # Detection: Table vs. Diagram / Figure
    # ──────────────────────────────────────────────────────────────────

    def is_likely_table(self, img_input: str | np.ndarray) -> tuple[bool, str, dict[str, Any]]:
        """
        Determine whether an image contains a table or is a diagram/figure.

        Returns:
            (is_table: bool, table_type: str, details: dict)
            table_type is one of: "bordered", "borderless", "not_a_table"
        """
        if not _CV2_AVAILABLE:
            return False, "not_a_table", {"reason": "OpenCV unavailable"}

        # Load image as grayscale numpy array
        if isinstance(img_input, str):
            if not os.path.isfile(img_input):
                return False, "not_a_table", {"reason": "File not found"}
            img = cv2.imread(img_input)
            if img is None:
                return False, "not_a_table", {"reason": "Could not read image"}
        else:
            img = img_input

        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img

        h, w = gray.shape[:2]
        if h < 45 or w < 65:
            return False, "not_a_table", {"reason": "Image dimensions too small for a question table"}

        # ── Test 1: Bordered Grid Detection via OpenCV Morphology ─────
        # Adaptive thresholding to isolate lines
        bw = cv2.adaptiveThreshold(~gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2)

        # Horizontal lines kernel (must span at least ~1/18th of image width)
        h_len = max(14, int(w / 18))
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_len, 1))
        h_lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, h_kernel)

        # Vertical lines kernel (must span at least ~1/16th of image height)
        v_len = max(12, int(h / 16))
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_len))
        v_lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, v_kernel)

        # Intersection corners
        intersections = cv2.bitwise_and(h_lines, v_lines)
        corner_count = np.sum(intersections > 0)

        # Combined grid mask
        grid = cv2.add(h_lines, v_lines)
        contours, _ = cv2.findContours(grid, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

        cells = []
        for c in contours:
            x, y, cw, ch = cv2.boundingRect(c)
            # Filter valid cell dimensions (not tiny dots and not whole image border)
            if cw > 18 and ch > 12 and (cw < w * 0.94 or ch < h * 0.94):
                # Ensure rectangularity: bounding box area vs contour area
                area = cv2.contourArea(c)
                if area > 100 and (area / (cw * ch)) > 0.65:
                    cells.append((x, y, cw, ch))

        # Check if detected cells form a structured grid
        if len(cells) >= 4 and corner_count >= 4:
            y_centers = [y + ch / 2 for x, y, cw, ch in cells]
            x_centers = [x + cw / 2 for x, y, cw, ch in cells]
            row_clusters = len(set(round(yc / 18) for yc in y_centers))
            col_clusters = len(set(round(xc / 25) for xc in x_centers))

            if row_clusters >= 2 and col_clusters >= 2:
                # Check for circular/node dominance (characteristic of neural networks or flowcharts)
                circles = cv2.HoughCircles(
                    gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=25,
                    param1=50, param2=45, minRadius=16, maxRadius=int(min(h, w) / 4)
                )
                circle_count = len(circles[0]) if circles is not None else 0

                # If lots of circular nodes exist, it is likely a network/graph diagram, not a table
                if circle_count >= 5:
                    return False, "not_a_table", {"reason": "Detected network diagram with circular nodes"}

                return True, "bordered", {
                    "cells": cells,
                    "grid_mask": grid,
                    "rows": row_clusters,
                    "cols": col_clusters,
                }

        # ── Test 2: Borderless Table Detection via Text Alignment ─────
        # Fast heuristic checks before expensive OCR:
        # A borderless table must have horizontal text-line structure and lack diagram elements
        circles = cv2.HoughCircles(
            gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=20,
            param1=50, param2=40, minRadius=10, maxRadius=int(min(h, w) / 4)
        )
        if circles is not None and len(circles[0]) >= 3:
            return False, "not_a_table", {"reason": "Detected diagram with circular nodes/elements"}

        # Check horizontal text-band projection
        row_density = np.sum(gray < 200, axis=1)
        text_lines = np.sum(row_density > (w * 0.05))
        if text_lines < 3 or text_lines > h * 0.90:
            return False, "not_a_table", {"reason": "Visual features do not match tabular text"}

        # If text-line structure detected, verify alignment via OCR
        ocr = self._get_ocr()
        if ocr is not None:
            try:
                ocr_result, _ = ocr(img)
                if ocr_result and len(ocr_result) >= 4:
                    parsed_boxes = []
                    for box, text, conf in ocr_result:
                        if not text.strip():
                            continue
                        x0 = min(pt[0] for pt in box)
                        x1 = max(pt[0] for pt in box)
                        y0 = min(pt[1] for pt in box)
                        y1 = max(pt[1] for pt in box)
                        parsed_boxes.append({
                            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                            "text": text.strip(), "conf": float(conf)
                        })

                    if len(parsed_boxes) >= 4:
                        # Cluster into rows by vertical position
                        parsed_boxes.sort(key=lambda b: b["y0"])
                        row_groups: list[list[dict]] = []
                        for b in parsed_boxes:
                            yc = (b["y0"] + b["y1"]) / 2
                            placed = False
                            for rg in row_groups:
                                rg_yc = sum((x["y0"] + x["y1"]) / 2 for x in rg) / len(rg)
                                if abs(yc - rg_yc) < 16:
                                    rg.append(b)
                                    placed = True
                                    break
                            if not placed:
                                row_groups.append([b])

                        # Count rows with multiple items
                        multi_col_rows = sum(1 for rg in row_groups if len(rg) >= 2)
                        if len(row_groups) >= 2 and multi_col_rows >= 2:
                            # Verify column alignment consistency
                            x_starts = [b["x0"] for rg in row_groups for b in rg]
                            distinct_cols = len(set(round(x / 40) for x in x_starts))
                            if distinct_cols >= 2:
                                return True, "borderless", {
                                    "row_groups": row_groups,
                                    "ocr_boxes": parsed_boxes,
                                    "ocr_result": ocr_result,
                                }
            except Exception as exc:
                logger.debug("Borderless table detection error: %s", exc)

        return False, "not_a_table", {"reason": "Neither grid lines nor aligned tabular text detected"}

    # ──────────────────────────────────────────────────────────────────
    # Reconstruction: Reconstructing TableData with Rows and Cells
    # ──────────────────────────────────────────────────────────────────

    def recognize_table(self, image_path: str, asset_id: str | None = None) -> OcrTableResult:
        """
        Detect and reconstruct an image-based table into structured TableData.
        Preserves original image in fallback_asset_id.
        """
        asset_ref = asset_id or os.path.basename(image_path)

        if not self.available:
            return OcrTableResult(
                success=False,
                table_data=None,
                fallback_asset_id=asset_ref,
                error="Local OCR engine (RapidOCR / OpenCV) is not available.",
                table_type="none",
                validation_status=ValidationStatus.SKIPPED,
            )

        if not os.path.isfile(image_path):
            return OcrTableResult(
                success=False,
                table_data=None,
                fallback_asset_id=asset_ref,
                error=f"Image file not found: {image_path}",
                table_type="none",
                validation_status=ValidationStatus.FAILED,
            )

        is_table, table_type, details = self.is_likely_table(image_path)
        if not is_table:
            return OcrTableResult(
                success=False,
                table_data=None,
                fallback_asset_id=asset_ref,
                error=f"Asset is not a table ({details.get('reason', 'diagram/figure')}).",
                table_type="none",
                validation_status=ValidationStatus.SKIPPED,
            )

        # Run OCR
        ocr = self._get_ocr()
        if ocr is None:
            return OcrTableResult(
                success=False,
                table_data=None,
                fallback_asset_id=asset_ref,
                error="RapidOCR engine initialization failed.",
                table_type=table_type,
                validation_status=ValidationStatus.FAILED,
            )

        try:
            ocr_result = details.get("ocr_result")
            if ocr_result is None:
                img = cv2.imread(image_path)
                ocr_result, _ = ocr(img)
            ocr_boxes: list[dict[str, Any]] = []
            if ocr_result:
                for box, text, conf in ocr_result:
                    t_str = text.strip()
                    if not t_str:
                        continue
                    ocr_boxes.append({
                        "x0": min(pt[0] for pt in box),
                        "x1": max(pt[0] for pt in box),
                        "y0": min(pt[1] for pt in box),
                        "y1": max(pt[1] for pt in box),
                        "text": t_str,
                        "conf": float(conf),
                    })

            if table_type == "bordered":
                table_data, uncertain = self._reconstruct_bordered_table(details["cells"], ocr_boxes)
            else:
                table_data, uncertain = self._reconstruct_borderless_table(ocr_boxes)

            if not table_data or table_data.row_count == 0 or table_data.column_count == 0:
                return OcrTableResult(
                    success=False,
                    table_data=None,
                    fallback_asset_id=asset_ref,
                    error="Failed to reconstruct rows and columns from table image.",
                    table_type=table_type,
                    validation_status=ValidationStatus.FAILED,
                )

            # Generate semantic HTML
            table_data.html = self._reconstructor.to_html(table_data)

            val_status = ValidationStatus.SUSPECT if uncertain else ValidationStatus.VALID
            avg_conf = (
                sum(b["conf"] for b in ocr_boxes) / len(ocr_boxes)
                if ocr_boxes else 0.85
            )

            return OcrTableResult(
                success=True,
                table_data=table_data,
                fallback_asset_id=asset_ref,
                table_type=table_type,
                confidence=avg_conf,
                validation_status=val_status,
                uncertain_cells=uncertain,
            )

        except Exception as exc:
            logger.exception("Table reconstruction failed for %s: %s", image_path, exc)
            return OcrTableResult(
                success=False,
                table_data=None,
                fallback_asset_id=asset_ref,
                error=f"Table reconstruction error: {exc}",
                table_type=table_type,
                validation_status=ValidationStatus.FAILED,
            )

    # ──────────────────────────────────────────────────────────────────
    # Internal Grid Builders
    # ──────────────────────────────────────────────────────────────────

    def _reconstruct_bordered_table(
        self,
        raw_cells: list[tuple[int, int, int, int]],
        ocr_boxes: list[dict[str, Any]],
    ) -> tuple[TableData, list[dict[str, Any]]]:
        """
        Reconstruct bordered table from cell rectangles and OCR boxes.
        """
        # 1. Deduplicate cells
        unique_cells = []
        for x, y, cw, ch in raw_cells:
            if not any(
                abs(x - ex) < 8 and abs(y - ey) < 8 and abs(cw - ecw) < 10 and abs(ch - ech) < 10
                for ex, ey, ecw, ech in unique_cells
            ):
                unique_cells.append((x, y, cw, ch))

        # 2. Filter out composite cells (boxes that contain smaller detected cells)
        cells_clean = []
        for i, (x, y, w, h) in enumerate(unique_cells):
            is_composite = False
            for j, (ox, oy, ow, oh) in enumerate(unique_cells):
                if i != j and x <= ox + 2 and y <= oy + 2 and x + w >= ox + ow - 2 and y + h >= oy + oh - 2:
                    if (ow * oh) < (w * h) * 0.90:
                        is_composite = True
                        break
            if not is_composite:
                cells_clean.append((x, y, w, h))

        # Cluster cells by y into rows
        cells_clean.sort(key=lambda c: (c[1], c[0]))
        rows_of_cells: list[list[tuple[int, int, int, int]]] = []
        for c in cells_clean:
            cy = c[1] + c[3] / 2
            placed = False
            for r in rows_of_cells:
                r_cy = sum(x[1] + x[3] / 2 for x in r) / len(r)
                if abs(cy - r_cy) < max(12, c[3] * 0.45):
                    r.append(c)
                    placed = True
                    break
            if not placed:
                rows_of_cells.append([c])

        # Sort each row by x0
        for r in rows_of_cells:
            r.sort(key=lambda c: c[0])

        # Filter out outlier rows with only 1 narrow cell if other rows have many
        max_cols = max(len(r) for r in rows_of_cells) if rows_of_cells else 0
        if max_cols >= 2:
            rows_of_cells = [r for r in rows_of_cells if len(r) >= max(1, int(max_cols * 0.5))]

        table_rows: list[TableRow] = []
        uncertain_cells: list[dict[str, Any]] = []

        for ri, r in enumerate(rows_of_cells):
            t_cells: list[TableCell] = []
            for ci, (cx, cy, cw, ch) in enumerate(r):
                # Find OCR text boxes inside this cell
                matched_texts = []
                cell_conf = 1.0
                for b in ocr_boxes:
                    bx_c = (b["x0"] + b["x1"]) / 2
                    by_c = (b["y0"] + b["y1"]) / 2
                    # Check if center falls inside the cell (with small tolerance)
                    if (cx - 4 <= bx_c <= cx + cw + 4) and (cy - 4 <= by_c <= cy + ch + 4):
                        matched_texts.append(b)
                        if b["conf"] < 0.65:
                            cell_conf = min(cell_conf, b["conf"])

                matched_texts.sort(key=lambda b: (b["y0"], b["x0"]))
                cell_text = " ".join(b["text"] for b in matched_texts).strip()

                if cell_conf < 0.65 and cell_text:
                    uncertain_cells.append({
                        "row": ri, "col": ci, "text": cell_text, "confidence": cell_conf
                    })

                t_cells.append(TableCell(
                    text=cell_text,
                    row_index=ri,
                    col_index=ci,
                    is_header=(ri == 0),
                ))

            table_rows.append(TableRow(
                cells=t_cells,
                row_index=ri,
                is_header=(ri == 0),
            ))

        col_count = max((len(r.cells) for r in table_rows), default=0)
        # Normalize column count across all rows
        for r in table_rows:
            while len(r.cells) < col_count:
                r.cells.append(TableCell(
                    text="",
                    row_index=r.row_index,
                    col_index=len(r.cells),
                    is_header=r.is_header,
                ))

        t_data = TableData(
            rows=table_rows,
            column_count=col_count,
            row_count=len(table_rows),
            has_header=len(table_rows) > 1,
        )
        return t_data, uncertain_cells

    def _reconstruct_borderless_table(
        self,
        ocr_boxes: list[dict[str, Any]],
    ) -> tuple[TableData, list[dict[str, Any]]]:
        """
        Reconstruct borderless table by clustering OCR text boxes into rows and columns.
        """
        if not ocr_boxes:
            return TableData(rows=[], column_count=0, row_count=0), []

        # Sort by y0
        ocr_boxes.sort(key=lambda b: b["y0"])

        # 1. Cluster into horizontal rows
        row_groups: list[list[dict[str, Any]]] = []
        for b in ocr_boxes:
            yc = (b["y0"] + b["y1"]) / 2
            placed = False
            for rg in row_groups:
                rg_yc = sum((x["y0"] + x["y1"]) / 2 for x in rg) / len(rg)
                if abs(yc - rg_yc) < 16:
                    rg.append(b)
                    placed = True
                    break
            if not placed:
                row_groups.append([b])

        # Sort items in each row by x0
        for rg in row_groups:
            rg.sort(key=lambda b: b["x0"])

        # 2. Determine global column centers from rows with multiple items
        x_centers = []
        for rg in row_groups:
            for b in rg:
                x_centers.append((b["x0"] + b["x1"]) / 2)

        x_centers.sort()
        col_clusters: list[float] = []
        for xc in x_centers:
            placed = False
            for i, cc in enumerate(col_clusters):
                if abs(xc - cc) < 45:
                    col_clusters[i] = (cc + xc) / 2
                    placed = True
                    break
            if not placed:
                col_clusters.append(xc)

        col_clusters.sort()
        num_cols = len(col_clusters)
        if num_cols < 2:
            num_cols = max(len(rg) for rg in row_groups)

        table_rows: list[TableRow] = []
        uncertain_cells: list[dict[str, Any]] = []

        for ri, rg in enumerate(row_groups):
            # Map each item in row to closest column index
            cell_slots: list[list[dict]] = [[] for _ in range(num_cols)]
            for b in rg:
                bx_c = (b["x0"] + b["x1"]) / 2
                best_ci = min(range(num_cols), key=lambda ci: abs(bx_c - col_clusters[ci]))
                cell_slots[best_ci].append(b)

            t_cells = []
            for ci in range(num_cols):
                items = cell_slots[ci]
                items.sort(key=lambda b: b["x0"])
                txt = " ".join(b["text"] for b in items).strip()
                min_c = min((b["conf"] for b in items), default=1.0)
                if min_c < 0.65 and txt:
                    uncertain_cells.append({
                        "row": ri, "col": ci, "text": txt, "confidence": min_c
                    })
                t_cells.append(TableCell(
                    text=txt,
                    row_index=ri,
                    col_index=ci,
                    is_header=(ri == 0),
                ))

            table_rows.append(TableRow(
                cells=t_cells,
                row_index=ri,
                is_header=(ri == 0),
            ))

        t_data = TableData(
            rows=table_rows,
            column_count=num_cols,
            row_count=len(table_rows),
            has_header=len(table_rows) > 1,
        )
        return t_data, uncertain_cells
