"""
Document API routes.

Endpoints:
    GET  /api/health
    POST /api/documents/process
    GET  /api/documents
    GET  /api/documents/<document_id>
    GET  /api/documents/<document_id>/assets/<asset_id>
"""

from __future__ import annotations

import logging
import os
import re

from flask import Blueprint, current_app, jsonify, request, send_from_directory

from backend.services.document_processor import DocumentProcessor, ProcessingError

logger = logging.getLogger(__name__)
document_bp = Blueprint("documents", __name__)

# Lazy-initialised processor (one per application instance)
_processor: DocumentProcessor | None = None


def _get_processor() -> DocumentProcessor:
    global _processor
    if _processor is None:
        _processor = DocumentProcessor(
            upload_folder=current_app.config["UPLOAD_FOLDER"],
            assets_folder=current_app.config["ASSETS_FOLDER"],
        )
    return _processor


get_processor = _get_processor


# ──────────────────────────────────────────────────────────────────────
# Health
# ──────────────────────────────────────────────────────────────────────

@document_bp.get("/health")
def health():
    """GET /api/health — dependency status check."""
    status: dict[str, str] = {}

    try:
        import pymupdf
        status["pymupdf"] = pymupdf.__version__
    except ImportError:
        status["pymupdf"] = "NOT AVAILABLE"

    try:
        import pdfplumber
        status["pdfplumber"] = pdfplumber.__version__
    except ImportError:
        status["pdfplumber"] = "NOT AVAILABLE"

    try:
        import docx
        status["python_docx"] = "available"
    except ImportError:
        status["python_docx"] = "NOT AVAILABLE"

    try:
        from PIL import Image
        status["pillow"] = Image.__version__
    except ImportError:
        status["pillow"] = "NOT AVAILABLE"

    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
        status["rapidocr"] = "available (local ONNX OCR engine)"
    except ImportError:
        status["rapidocr"] = "NOT AVAILABLE"

    try:
        import paddle                  # noqa: F401
        from paddleocr import PaddleOCR  # noqa: F401
        status["paddleocr"] = "available"
    except ImportError:
        status["paddleocr"] = "NOT AVAILABLE"

    all_core_ok = all(
        v != "NOT AVAILABLE"
        for k, v in status.items()
        if k in ("pymupdf", "pdfplumber", "python_docx", "pillow")
    )

    return jsonify({
        "status":       "ok" if all_core_ok else "degraded",
        "dependencies": status,
        "python_version": __import__("sys").version,
    })


# ──────────────────────────────────────────────────────────────────────
# Document processing
# ──────────────────────────────────────────────────────────────────────

@document_bp.post("/documents/process")
def process_document():
    """POST /api/documents/process — upload and extract a PDF or DOCX using local extraction."""
    if "file" not in request.files:
        return jsonify({"error": "No file part in the request."}), 400

    file = request.files["file"]
    if not file or file.filename == "":
        return jsonify({"error": "No file selected."}), 400

    original_filename = file.filename  # used for display; not used as path

    try:
        processor = _get_processor()
        doc_dict  = processor.process_upload(file, original_filename)
        return jsonify(doc_dict), 200

    except ProcessingError as exc:
        logger.warning("Processing error for %r: %s", original_filename, exc)
        return jsonify({"error": str(exc)}), 422

    except Exception as exc:
        logger.exception("Unexpected error processing %r", original_filename)
        return jsonify({"error": "An unexpected error occurred. Please try again."}), 500


@document_bp.post("/documents/reprocess")
def reprocess_saved_document():
    """
    POST /api/documents/reprocess — reprocess an already saved document file on disk using local pipeline.
    """
    data = request.json or request.form or {}
    file_path = data.get("file_path")
    original_filename = data.get("original_filename")
    document_id = data.get("document_id")

    if not file_path or not os.path.isfile(file_path):
        return jsonify({"error": "Valid file_path is required to reprocess a saved document."}), 400

    try:
        processor = _get_processor()
        doc_dict = processor.reprocess_file(
            file_path=file_path,
            original_filename=original_filename,
            document_id=document_id,
        )
        return jsonify(doc_dict), 200

    except ProcessingError as exc:
        logger.warning("Processing error during reprocess: %s", exc)
        return jsonify({"error": str(exc)}), 422

    except Exception as exc:
        logger.exception("Unexpected error during reprocess")
        return jsonify({"error": "An unexpected error occurred. Please try again."}), 500


# ──────────────────────────────────────────────────────────────────────
# Document retrieval
# ──────────────────────────────────────────────────────────────────────

@document_bp.get("/documents")
def list_documents():
    """GET /api/documents — list all processed documents (Phase 1: in-memory)."""
    processor = _get_processor()
    return jsonify({"documents": processor.list_documents()})


@document_bp.get("/documents/<document_id>")
def get_document(document_id: str):
    """GET /api/documents/<document_id> — retrieve extraction result."""
    if not _safe_id(document_id):
        return jsonify({"error": "Invalid document ID."}), 400

    processor = _get_processor()
    doc = processor.get_document(document_id)
    if doc is None:
        return jsonify({"error": "Document not found."}), 404
    return jsonify(doc)


# ──────────────────────────────────────────────────────────────────────
# Asset serving
# ──────────────────────────────────────────────────────────────────────

@document_bp.get("/documents/<document_id>/assets/<asset_id>")
def get_asset(document_id: str, asset_id: str):
    """
    GET /api/documents/<document_id>/assets/<asset_id>
    Serve an extracted image asset. Validates both IDs to prevent
    path traversal.
    """
    if not _safe_id(document_id) or not _safe_asset_id(asset_id):
        return jsonify({"error": "Invalid identifier."}), 400

    assets_folder = current_app.config["ASSETS_FOLDER"]
    # Verify the asset belongs to the declared document (prefix check)
    if not asset_id.startswith(document_id):
        return jsonify({"error": "Asset does not belong to this document."}), 403

    try:
        return send_from_directory(assets_folder, asset_id)
    except FileNotFoundError:
        return jsonify({"error": "Asset not found."}), 404


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_ASSET_RE = re.compile(r"^[0-9a-f\-]+_[a-zA-Z0-9_.]+$")


def _safe_id(value: str) -> bool:
    """Accept only UUID-formatted document IDs."""
    return bool(_UUID_RE.match(value))


def _safe_asset_id(value: str) -> bool:
    """
    Accept only asset IDs matching the generated pattern:
    <uuid>_p<n>_<xref>.<ext>  or  <uuid>_s<n>_<rId>.<ext>
    """
    return bool(_ASSET_RE.match(value)) and ".." not in value and "/" not in value and "\\" not in value
