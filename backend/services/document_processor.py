"""
Document processing orchestrator.

Coordinates:
- File validation
- Routing to PDF or DOCX extractor
- Running the document validator
- Running the document profiler
- Running the question segmenter
- Storing results in a simple in-memory registry
"""

from __future__ import annotations

import logging
import os
import uuid
import hashlib
from pathlib import Path

from backend.schema import DocumentResult, SourceFormat
from backend.services.pdf_extractor import PdfExtractor
from backend.services.docx_extractor import DocxExtractor
from backend.services.document_validator import DocumentValidator
from backend.services.document_profiler import DocumentProfiler
from backend.services.question_segmenter import QuestionSegmenter, SegmentationResult

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"pdf", "docx"}
MAX_FILE_SIZE = 50 * 1024 * 1024   # 50 MB


class ProcessingError(Exception):
    """Raised for known, user-facing processing errors."""
    pass


class DocumentProcessor:
    """Orchestrate extraction, validation, and segmentation using local document processing."""

    def __init__(self, upload_folder: str, assets_folder: str):
        self.upload_folder = upload_folder
        self.assets_folder = assets_folder

        self._pdf_extractor   = PdfExtractor(assets_folder)
        self._docx_extractor  = DocxExtractor(assets_folder)
        self._validator       = DocumentValidator(assets_folder)
        self._profiler        = DocumentProfiler()
        self._segmenter       = QuestionSegmenter()

        # In-memory document registry
        self._registry: dict[str, dict] = {}

    # ──────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────

    def process_upload(self, file_storage, original_filename: str, mode: str = "local") -> dict:
        """
        Validate, save, extract, validate result, and segment using the local pipeline.
        Returns the serialized document dict ready for the API.
        """
        ext = self._validate_extension(original_filename)
        document_id = str(uuid.uuid4())
        safe_filename = f"{document_id}.{ext}"
        save_path = os.path.join(self.upload_folder, safe_filename)

        # ── Save upload ──────────────────────────────────────────
        try:
            file_storage.save(save_path)
        except Exception as exc:
            logger.exception("Failed to save uploaded file %s", safe_filename)
            raise ProcessingError(f"Could not save uploaded file: {exc}") from exc

        file_size = os.path.getsize(save_path)
        if file_size > MAX_FILE_SIZE:
            self._cleanup_file(save_path)
            raise ProcessingError(f"File exceeds maximum allowed size of {MAX_FILE_SIZE // (1024*1024)} MB.")

        if file_size == 0:
            self._cleanup_file(save_path)
            raise ProcessingError("Uploaded file is empty (0 bytes).")

        # Compute SHA-256 hash of the uploaded document
        hasher = hashlib.sha256()
        with open(save_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        file_hash = hasher.hexdigest()

        # ── Extract (Stage 1) ────────────────────────────────────
        try:
            result = self._extract(save_path, ext, document_id, original_filename)
        except Exception as exc:
            self._cleanup_file(save_path)
            raise ProcessingError(f"Extraction failed: {exc}") from exc

        # ── Validate result (Stage 2) ────────────────────────────
        validation_warnings = self._validator.validate(result)
        for w in validation_warnings:
            if w not in result.summary.warnings:
                result.summary.warnings.append(w)

        # ── Profile (Stage A) ────────────────────────────────────
        try:
            profile = self._profiler.profile(result.blocks, original_filename)
        except Exception as exc:
            logger.warning("Document profiling failed: %s", exc)
            profile = None

        # ── Local Question Segmentation ──────────────────────────
        try:
            seg_result = self._segmenter.segment(result.blocks, profile=profile)
            for q in seg_result.questions:
                q.extraction_method = "local"
        except Exception as exc:
            logger.warning("Question segmentation failed: %s", exc)
            seg_result = SegmentationResult(
                questions=[], unclassified_block_ids=[], warnings=[str(exc)]
            )

        # Check if recognized units have 0 questions
        unit_dist = dict(getattr(seg_result, 'unit_distribution', {}))
        seg_warnings = list(seg_result.warnings)
        for u_name, count in unit_dist.items():
            if count == 0:
                warn_msg = f"{u_name} was detected in document structure but 0 questions were extracted."
                if warn_msg not in seg_warnings:
                    seg_warnings.append(warn_msg)

        text_blocks_count = sum(1 for b in result.blocks if getattr(b.block_type, 'value', b.block_type) == "text")
        tables_detected = sum(1 for b in result.blocks if getattr(b.block_type, 'value', b.block_type) == "table")
        figures_detected = sum(1 for b in result.blocks if getattr(b.block_type, 'value', b.block_type) in ("image", "figure"))
        unassigned_count = sum(1 for q in seg_result.questions if not q.unit)
        uncertain_count = sum(1 for q in seg_result.questions if q.uncertain)

        diagnostics = {
            "pages_processed": result.summary.total_pages,
            "text_blocks_count": text_blocks_count,
            "questions_detected": len(seg_result.questions),
            "unit_distribution": unit_dist,
            "tables_detected": tables_detected,
            "figures_detected": figures_detected,
            "unassigned_count": unassigned_count,
            "uncertain_count": uncertain_count,
            "extraction_mode": "local",
        }

        # ── Store in registry ────────────────────────────────────
        doc_dict = result.to_dict()
        doc_dict["extraction_mode"] = "local"
        doc_dict["profile"] = profile.to_dict() if profile else {}
        doc_dict["diagnostics"] = diagnostics
        doc_dict["segmentation"] = {
            "extraction_mode": "local",
            "questions": [
                {
                    "question_id":   q.question_id,
                    "block_id":      q.block_id,
                    "text":          q.text,
                    "page_number":   q.page_number,
                    "level":         q.level,
                    "parent_id":     q.parent_id,
                    "associated_blocks": q.associated_blocks,
                    "uncertain":     q.uncertain,
                    "unit":          q.unit,
                    "classification_source": q.classification_source,
                    "difficulty":    q.difficulty,
                    "estimated_marks": q.estimated_marks,
                    "topic":         q.topic,
                    "course_outcome": q.course_outcome,
                    "bloom_level":   q.bloom_level,
                    "source_question_number": q.source_question_number,
                    "confidence":    q.confidence,
                    "needs_review":  q.needs_review,
                    "review_reasons": q.review_reasons,
                    "original_text": q.original_text,
                    "is_repaired":   getattr(q, 'is_repaired', False),
                    "repair_metadata": q.repair_metadata,
                    "repaired_confidence": q.repaired_confidence,
                    "extraction_method": "local",
                    "options": getattr(q, "options", None),
                    "subquestions": getattr(q, "subquestions", None),
                }
                for q in seg_result.questions
            ],
            "unclassified_count": len(seg_result.unclassified_block_ids),
            "warnings": seg_warnings,
            "unit_distribution": unit_dist,
            "diagnostics": diagnostics,
            "auto_accepted_count": getattr(seg_result, 'auto_accepted_count', 0),
            "review_needed_count": getattr(seg_result, 'review_needed_count', 0),
        }
        doc_dict["file_hash"] = file_hash
        doc_dict["upload_info"] = {
            "original_filename": original_filename,
            "safe_filename":     safe_filename,
            "file_size_bytes":   file_size,
            "file_hash":         file_hash,
            "saved_file_path":   save_path,
        }

        self._registry[document_id] = doc_dict
        return doc_dict

    def get_document(self, document_id: str) -> dict | None:
        """Retrieve a processed document by ID from the in-memory registry."""
        return self._registry.get(document_id)

    def reprocess_file(self, file_path: str, original_filename: str | None = None, document_id: str | None = None, mode: str = "local") -> dict:
        """
        Re-extract and re-segment an already uploaded file using local extraction.
        """
        if not os.path.isfile(file_path):
            raise ProcessingError("Source document file no longer exists on disk.")

        orig_name = original_filename or os.path.basename(file_path)
        ext = self._validate_extension(orig_name)
        doc_id = document_id or str(uuid.uuid4())
        file_size = os.path.getsize(file_path)

        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        file_hash = hasher.hexdigest()

        result = self._extract(file_path, ext, doc_id, orig_name)
        validation_warnings = self._validator.validate(result)
        for w in validation_warnings:
            if w not in result.summary.warnings:
                result.summary.warnings.append(w)

        try:
            profile = self._profiler.profile(result.blocks, orig_name)
        except Exception as exc:
            logger.warning("Document profiling failed: %s", exc)
            profile = None

        try:
            seg_result = self._segmenter.segment(result.blocks, profile=profile)
            for q in seg_result.questions:
                q.extraction_method = "local"
        except Exception as exc:
            logger.warning("Question segmentation failed: %s", exc)
            seg_result = SegmentationResult(
                questions=[], unclassified_block_ids=[], warnings=[str(exc)]
            )

        unit_dist = dict(getattr(seg_result, 'unit_distribution', {}))
        seg_warnings = list(seg_result.warnings)
        for u_name, count in unit_dist.items():
            if count == 0:
                warn_msg = f"{u_name} was detected in document structure but 0 questions were extracted."
                if warn_msg not in seg_warnings:
                    seg_warnings.append(warn_msg)

        text_blocks_count = sum(1 for b in result.blocks if getattr(b.block_type, 'value', b.block_type) == "text")
        tables_detected = sum(1 for b in result.blocks if getattr(b.block_type, 'value', b.block_type) == "table")
        figures_detected = sum(1 for b in result.blocks if getattr(b.block_type, 'value', b.block_type) in ("image", "figure"))
        unassigned_count = sum(1 for q in seg_result.questions if not q.unit)
        uncertain_count = sum(1 for q in seg_result.questions if q.uncertain)

        diagnostics = {
            "pages_processed": result.summary.total_pages,
            "text_blocks_count": text_blocks_count,
            "questions_detected": len(seg_result.questions),
            "unit_distribution": unit_dist,
            "tables_detected": tables_detected,
            "figures_detected": figures_detected,
            "unassigned_count": unassigned_count,
            "uncertain_count": uncertain_count,
            "extraction_mode": "local",
        }

        doc_dict = result.to_dict()
        doc_dict["extraction_mode"] = "local"
        doc_dict["profile"] = profile.to_dict() if profile else {}
        doc_dict["diagnostics"] = diagnostics
        doc_dict["segmentation"] = {
            "extraction_mode": "local",
            "questions": [
                {
                    "question_id":   q.question_id,
                    "block_id":      q.block_id,
                    "text":          q.text,
                    "page_number":   q.page_number,
                    "level":         q.level,
                    "parent_id":     q.parent_id,
                    "associated_blocks": q.associated_blocks,
                    "uncertain":     q.uncertain,
                    "unit":          q.unit,
                    "classification_source": q.classification_source,
                    "difficulty":    q.difficulty,
                    "estimated_marks": q.estimated_marks,
                    "topic":         q.topic,
                    "course_outcome": q.course_outcome,
                    "bloom_level":   q.bloom_level,
                    "source_question_number": q.source_question_number,
                    "confidence":    q.confidence,
                    "needs_review":  q.needs_review,
                    "review_reasons": q.review_reasons,
                    "original_text": q.original_text,
                    "is_repaired":   getattr(q, 'is_repaired', False),
                    "repair_metadata": q.repair_metadata,
                    "repaired_confidence": q.repaired_confidence,
                    "extraction_method": "local",
                    "options": getattr(q, "options", None),
                    "subquestions": getattr(q, "subquestions", None),
                }
                for q in seg_result.questions
            ],
            "unclassified_count": len(seg_result.unclassified_block_ids),
            "warnings": seg_warnings,
            "unit_distribution": unit_dist,
            "diagnostics": diagnostics,
            "auto_accepted_count": getattr(seg_result, 'auto_accepted_count', 0),
            "review_needed_count": getattr(seg_result, 'review_needed_count', 0),
        }
        doc_dict["file_hash"] = file_hash
        doc_dict["upload_info"] = {
            "original_filename": orig_name,
            "safe_filename":     os.path.basename(file_path),
            "file_size_bytes":   file_size,
            "file_hash":         file_hash,
            "saved_file_path":   file_path,
        }

        self._registry[doc_id] = doc_dict
        return doc_dict

    # ──────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────

    def _validate_extension(self, filename: str) -> str:
        """Validate filename has an allowed extension and return it lowercased."""
        if not filename or "." not in filename:
            raise ProcessingError("Uploaded file has no extension.")
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            allowed = ", ".join(f".{e}" for e in sorted(ALLOWED_EXTENSIONS))
            raise ProcessingError(f"Unsupported file type '.{ext}'. Allowed types: {allowed}")
        return ext

    def _extract(self, file_path: str, ext: str, document_id: str, original_filename: str) -> DocumentResult:
        """Route to appropriate local extractor."""
        if ext == "pdf":
            return self._pdf_extractor.extract(file_path, document_id, original_filename)
        elif ext == "docx":
            return self._docx_extractor.extract(file_path, document_id, original_filename)
        else:
            raise ProcessingError(f"No extractor registered for extension '.{ext}'.")

    @staticmethod
    def _cleanup_file(file_path: str) -> None:
        """Silently remove a file if it exists."""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass
