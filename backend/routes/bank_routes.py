import logging
from flask import Blueprint, request, jsonify
from backend.services.question_bank_service import QuestionBankService

logger = logging.getLogger(__name__)
bank_bp = Blueprint("bank", __name__)
service = QuestionBankService()

@bank_bp.route("/banks", methods=["GET"])
def list_banks():
    try:
        return jsonify(service.list_banks())
    except Exception as e:
        logger.exception("Error listing banks")
        return jsonify({"error": str(e)}), 500

@bank_bp.route("/banks", methods=["POST"])
def create_bank():
    try:
        data = request.json
        if not data or not data.get("name"):
            return jsonify({"error": "Name is required"}), 400
        return jsonify(service.create_bank(data["name"], data.get("description", "")))
    except Exception as e:
        logger.exception("Error creating bank")
        return jsonify({"error": str(e)}), 500

@bank_bp.route("/banks/<bank_id>", methods=["GET"])
def get_bank(bank_id):
    bank = service.get_bank(bank_id)
    if not bank: return jsonify({"error": "Not found"}), 404
    return jsonify(bank)

@bank_bp.route("/banks/<bank_id>", methods=["DELETE"])
def delete_bank(bank_id):
    service.delete_bank(bank_id)
    return jsonify({"success": True})

@bank_bp.route("/banks/<bank_id>/duplicate", methods=["POST"])
def duplicate_bank(bank_id):
    data = request.json
    if not data or not data.get("name"):
        return jsonify({"error": "Name is required"}), 400
    try:
        new_bank = service.duplicate_bank(bank_id, data["name"])
        return jsonify(new_bank)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bank_bp.route("/banks/<bank_id>/questions", methods=["GET"])
def list_questions(bank_id):
    return jsonify(service.list_questions(bank_id))

@bank_bp.route("/banks/<bank_id>/questions", methods=["POST"])
def create_question(bank_id):
    return jsonify(service.create_question(bank_id, request.json))

@bank_bp.route("/banks/check-duplicate", methods=["GET"])
def check_duplicate():
    filename = request.args.get("filename")
    file_hash = request.args.get("hash")
    if not filename and not file_hash:
        return jsonify({"exists": False, "match_type": "none", "bank": None})
    dup = service.check_duplicate_document(source_filename=filename, file_hash=file_hash)
    if not dup:
        return jsonify({"exists": False, "match_type": "none", "bank": None})
    return jsonify({
        "exists": True,
        "match_type": dup.get("match_type", "exact"),
        "bank": dup.get("bank", dup),
        "message": dup.get("message", "")
    })

@bank_bp.route("/banks/import", methods=["POST"])
def import_document():
    data = request.json or {}
    filename = data.get("filename")
    document_id = data.get("document_id")
    file_hash = data.get("file_hash")
    subject = data.get("subject")
    questions = data.get("questions", [])
    target_bank_id = data.get("target_bank_id") or data.get("bank_id")
    
    if not filename and not target_bank_id:
        return jsonify({"error": "Filename or target_bank_id is required"}), 400
        
    bank = None
    if target_bank_id:
        bank = service.get_bank(target_bank_id)
        if bank and data.get("replace_existing", True):
            from backend.database import get_db
            with get_db() as db:
                db.execute("DELETE FROM content_blocks WHERE question_id IN (SELECT id FROM questions WHERE bank_id = ?)", (target_bank_id,))
                db.execute("DELETE FROM questions WHERE bank_id = ?", (target_bank_id,))
                # Also update hash and filename if provided
                updates = ["updated_at = CURRENT_TIMESTAMP"]
                params = []
                if file_hash:
                    updates.append("file_hash = ?")
                    params.append(file_hash)
                if filename:
                    updates.append("source_filename = ?")
                    params.append(filename)
                if document_id:
                    updates.append("source_document_id = ?")
                    params.append(document_id)
                params.append(target_bank_id)
                db.execute(f"UPDATE question_banks SET {', '.join(updates)} WHERE id = ?", tuple(params))

    if not bank:
        bank_name = f"Imported: {filename}"
        bank = service.create_bank(
            name=bank_name, 
            description="Automatically created from uploaded document.", 
            source_filename=filename, 
            source_document_id=document_id,
            file_hash=file_hash,
            subject=subject
        )
    
    blocks_by_id = {}
    if document_id:
        try:
            from backend.routes.document_routes import get_processor
            processor = get_processor()
            doc = processor.get_document(document_id)
            if doc and "blocks" in doc:
                blocks_by_id = {b["block_id"]: b for b in doc["blocks"]}
        except Exception:
            pass

    imported = []
    for q_data in questions:
        if blocks_by_id and q_data.get("associated_blocks"):
            q_blocks = list(q_data.get("blocks", []))
            existing_asset_ids = {
                b.get("image_path") for b in q_blocks if b.get("image_path")
            }
            existing_block_ids = {
                b.get("block_id") for b in q_blocks if b.get("block_id")
            }
            for assoc_id in q_data["associated_blocks"]:
                if assoc_id in existing_block_ids:
                    continue
                ab = blocks_by_id.get(assoc_id)
                if ab and ab.get("block_type") in ("table", "image", "figure"):
                    aid = ab.get("asset_id")
                    if aid and aid in existing_asset_ids:
                        continue
                    b_type = ab.get("block_type")
                    q_blocks.append({
                        "block_id": assoc_id,
                        "block_type": b_type,
                        "text_content": ab.get("text"),
                        "table_data": ab.get("table_data"),
                        "image_path": aid,
                        "metadata": ab.get("metadata", {}),
                    })
                    if aid:
                        existing_asset_ids.add(aid)
                    existing_block_ids.add(assoc_id)
            q_data["blocks"] = q_blocks

        imported.append(service.create_question(bank["id"], q_data))
        
    return jsonify({"bank": service.get_bank(bank["id"]), "imported": len(imported)})

@bank_bp.route("/banks/<bank_id>/reprocess", methods=["POST"])
def reprocess_bank_route(bank_id):
    """
    Safely reprocess questions for an existing question bank.
    Accepts:
    - multipart/form-data with file
    - JSON with document_id
    - Empty payload: automatically re-extracts the source document from storage/uploads.
    Preserves snapshot, reconciles questions transactionally, and keeps generated papers intact.
    """
    bank = service.get_bank(bank_id)
    if not bank:
        return jsonify({"error": "Question bank not found"}), 404

    from backend.routes.document_routes import get_processor
    from flask import current_app
    import os

    processor = get_processor()
    doc_dict = None

    try:
        if "file" in request.files and request.files["file"].filename != "":
            f = request.files["file"]
            doc_dict = processor.process_upload(f, f.filename)
        elif request.is_json and request.json and request.json.get("document_id"):
            doc_id = request.json["document_id"]
            doc_dict = processor.get_document(doc_id)
            if not doc_dict:
                return jsonify({"error": f"Processed document {doc_id} not found"}), 404
        else:
            # Re-extract from disk using source_document_id
            src_doc_id = bank.get("source_document_id")
            if not src_doc_id:
                return jsonify({"error": "No source document associated with this bank. Please upload a file to reprocess."}), 400

            uploads_folder = current_app.config.get("UPLOAD_FOLDER") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "storage", "uploads")
            target_file = None
            for ext in ("pdf", "docx"):
                p = os.path.join(uploads_folder, f"{src_doc_id}.{ext}")
                if os.path.isfile(p):
                    target_file = p
                    break

            if not target_file:
                return jsonify({"error": "Original document file is no longer on disk. Please upload the file to reprocess."}), 400

            doc_dict = processor.process_file_path(target_file, bank.get("source_filename"))

        # Convert doc_dict segmentation questions to bank question format
        seg_questions = doc_dict.get("segmentation", {}).get("questions", [])
        blocks_by_id = {b["block_id"]: b for b in doc_dict.get("blocks", [])}

        prepared_questions = []
        for q in seg_questions:
            blocks = [{
                "block_type": "text",
                "text_content": q.get("text", ""),
                "table_data": None,
                "image_path": None
            }]
            for assoc_id in (q.get("associated_blocks") or []):
                ab = blocks_by_id.get(assoc_id)
                if ab and ab.get("block_type") in ("table", "image", "figure"):
                    blocks.append({
                        "block_type": ab["block_type"],
                        "text_content": ab.get("text"),
                        "table_data": ab.get("table_data"),
                        "image_path": ab.get("asset_id") or None
                    })

            prepared_questions.append({
                "text": q.get("text", ""),
                "blocks": blocks,
                "unit": q.get("unit"),
                "difficulty": q.get("difficulty"),
                "estimated_marks": q.get("estimated_marks"),
                "topic": q.get("topic"),
                "course_outcome": q.get("course_outcome"),
                "bloom_level": q.get("bloom_level"),
                "source_question_number": q.get("source_question_number"),
                "confidence": q.get("confidence", 1.0),
                "review_status": "needs_review" if q.get("needs_review") else "accepted",
                "review_reasons": q.get("review_reasons", []),
                "source_document_id": doc_dict.get("document_id"),
                "source_page": q.get("page_number", 1),
                "original_text": q.get("original_text"),
                "is_repaired": q.get("is_repaired", False),
                "repair_metadata": q.get("repair_metadata"),
            })

        up_info = doc_dict.get("upload_info", {})
        new_hash = doc_dict.get("file_hash") or up_info.get("file_hash")
        new_fname = up_info.get("original_filename") or bank.get("source_filename")
        new_doc_id = doc_dict.get("document_id")

        updated_bank = service.reprocess_bank(
            bank_id=bank_id,
            questions_data=prepared_questions,
            new_document_id=new_doc_id,
            new_hash=new_hash,
            new_filename=new_fname
        )

        return jsonify({
            "success": True,
            "bank": updated_bank,
            "summary": updated_bank.get("reprocess_summary", {})
        })

    except Exception as e:
        logger.exception("Reprocessing failed for bank %s", bank_id)
        return jsonify({"error": f"Reprocessing failed: {str(e)}"}), 500

@bank_bp.route("/banks/<bank_id>/snapshots", methods=["GET"])
def list_snapshots(bank_id):
    return jsonify(service.list_bank_snapshots(bank_id))

@bank_bp.route("/banks/<bank_id>/restore-snapshot", methods=["POST"])
def restore_snapshot(bank_id):
    data = request.json or {}
    snapshot_id = data.get("snapshot_id")
    try:
        restored = service.restore_bank_snapshot(bank_id, snapshot_id)
        if not restored:
            return jsonify({"error": "Snapshot not found"}), 404
        return jsonify({"success": True, "bank": restored})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bank_bp.route("/banks/<bank_id>/blueprints", methods=["GET"])
def get_bank_blueprints(bank_id):
    from backend.database import get_db
    with get_db() as db:
        rows = db.execute('SELECT * FROM blueprints WHERE bank_id = ? ORDER BY created_at DESC', (bank_id,)).fetchall()
        return jsonify([dict(r) for r in rows])

@bank_bp.route("/banks/<bank_id>/papers", methods=["GET"])
def get_bank_papers(bank_id):
    from backend.database import get_db
    with get_db() as db:
        rows = db.execute('''
            SELECT id, blueprint_id, bank_id, created_at, is_valid, paper_total, warnings 
            FROM generated_papers 
            WHERE bank_id = ? 
            ORDER BY created_at DESC
        ''', (bank_id,)).fetchall()
        return jsonify([dict(r) for r in rows])

@bank_bp.route("/banks/<bank_id>/review-items", methods=["GET"])
def list_review_items(bank_id):
    try:
        return jsonify(service.list_review_exceptions(bank_id))
    except Exception as e:
        logger.exception("Error listing review exceptions")
        return jsonify({"error": str(e)}), 500

@bank_bp.route("/banks/<bank_id>/batch-review", methods=["POST"])
def batch_review(bank_id):
    try:
        data = request.json or {}
        items = data.get("items", [])
        return jsonify(service.batch_update_review(bank_id, items))
    except Exception as e:
        logger.exception("Error updating review batch")
        return jsonify({"error": str(e)}), 500

@bank_bp.route("/questions/<question_id>", methods=["GET"])
def get_question(question_id):
    q = service.get_question(question_id)
    if not q: return jsonify({"error": "Not found"}), 404
    return jsonify(q)

@bank_bp.route("/questions/<question_id>", methods=["PUT"])
def update_question(question_id):
    return jsonify(service.update_question(question_id, request.json))

@bank_bp.route("/questions/<question_id>", methods=["DELETE"])
def delete_question(question_id):
    service.delete_question(question_id)
    return jsonify({"success": True})
