"""
Table reconstructor — converts TableData into semantic HTML.

Produces valid HTML5 tables with thead/tbody/tr/th/td.
Handles:
- Header rows (first row treated as header by default, or flagged by TableRow.is_header)
- Empty cells (preserved as empty <td>)
- Long text (CSS classes for wrapping)
- Wide tables (horizontal scrolling wrapper)
- Merged cells via rowspan/colspan
- NA and other literal values preserved exactly
"""

from __future__ import annotations

import html
import logging

from backend.schema import TableData, TableRow, TableCell

logger = logging.getLogger(__name__)

# Maximum column count before adding the "wide-table" CSS class
WIDE_TABLE_THRESHOLD = 8


class TableReconstructor:
    """Convert a TableData into a semantic HTML table string."""

    def to_html(self, table_data: TableData) -> str:
        """
        Return a complete <div class="table-wrapper">...</div> fragment
        containing a semantic HTML table.
        """
        if not table_data or not table_data.rows:
            return ""

        is_wide = table_data.column_count >= WIDE_TABLE_THRESHOLD
        table_class = "extracted-table wide-table" if is_wide else "extracted-table"

        parts: list[str] = []
        parts.append(f'<div class="table-wrapper">')
        parts.append(f'<table class="{table_class}">')

        # ── thead ──────────────────────────────────────────────────
        header_rows   = [r for r in table_data.rows if r.is_header]
        non_header_rows = [r for r in table_data.rows if not r.is_header]

        if header_rows:
            parts.append("  <thead>")
            for row in header_rows:
                parts.append("    <tr>")
                for cell in row.cells:
                    cell_html = self._render_cell(cell, tag="th")
                    parts.append(f"      {cell_html}")
                parts.append("    </tr>")
            parts.append("  </thead>")

        # ── tbody ──────────────────────────────────────────────────
        data_rows = non_header_rows if header_rows else table_data.rows
        parts.append("  <tbody>")
        for row in data_rows:
            parts.append("    <tr>")
            for cell in row.cells:
                cell_html = self._render_cell(cell, tag="td")
                parts.append(f"      {cell_html}")
            parts.append("    </tr>")
        parts.append("  </tbody>")

        parts.append("</table>")
        parts.append("</div>")

        return "\n".join(parts)

    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _render_cell(cell: TableCell, tag: str) -> str:
        attrs: list[str] = []
        if cell.rowspan > 1:
            attrs.append(f'rowspan="{cell.rowspan}"')
        if cell.colspan > 1:
            attrs.append(f'colspan="{cell.colspan}"')

        text = cell.text  # preserve as-is (NA, empty, etc.)

        # Add CSS class for long text
        if len(text) > 80:
            attrs.append('class="long-text"')
        elif not text.strip():
            attrs.append('class="empty-cell"')

        attr_str = " ".join(attrs)
        open_tag = f"<{tag} {attr_str}>" if attr_str else f"<{tag}>"
        escaped  = html.escape(text)
        return f"{open_tag}{escaped}</{tag}>"
