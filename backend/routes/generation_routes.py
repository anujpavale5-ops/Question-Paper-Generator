import os
import re
import uuid
import base64
import io
from flask import Blueprint, request, jsonify, current_app, send_from_directory, send_file
from werkzeug.utils import secure_filename
from PIL import Image
from backend.services.paper_generator import PaperGenerator
from backend.services.docx_exporter import export_paper_to_docx
from backend.services.question_bank_service import QuestionBankService

generation_bp = Blueprint('generation_routes', __name__, url_prefix='/api/generate')
generator = PaperGenerator()
qb_service = QuestionBankService()

_SAFE_ASSET_RE = re.compile(r"^[0-9a-f\-]+(?:_[a-zA-Z0-9_]+)?\.(?:png|jpg|jpeg|webp|gif|svg)$", re.IGNORECASE)
ALLOWED_IMAGE_EXTS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


def _is_safe_asset_name(name: str) -> bool:
    if not name or '..' in name or '/' in name or '\\' in name:
        return False
    return bool(_SAFE_ASSET_RE.match(name))


@generation_bp.route('/', methods=['POST'])
def generate_paper():
    data = request.json or {}
    blueprint_id = data.get('blueprint_id')
    bank_id = data.get('bank_id')
    seed = data.get('seed')

    if not blueprint_id:
        return jsonify({'error': 'blueprint_id is required'}), 400

    try:
        result = generator.generate(blueprint_id, bank_id, seed=seed)
        if 'error' in result:
            return jsonify(result), 422
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@generation_bp.route('/<paper_id>', methods=['GET'])
def get_paper(paper_id):
    paper = generator.get_paper(paper_id)
    if not paper:
        return jsonify({'error': 'Paper not found'}), 404
    return jsonify(paper)


@generation_bp.route('/<paper_id>', methods=['PUT'])
def update_paper(paper_id):
    """Save paper edits across all or specific slots."""
    data = request.json or {}
    try:
        updated = generator.save_paper_edits(paper_id, data)
        return jsonify(updated)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@generation_bp.route('/<paper_id>/slots/<slot_id>', methods=['PUT'])
def update_slot(paper_id, slot_id):
    """Save edited content for a single slot in a paper."""
    data = request.json or {}
    edited_content = data.get('edited_content', data)
    try:
        updated = generator.save_slot_edit(paper_id, slot_id, edited_content)
        return jsonify({'success': True, 'paper': updated})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@generation_bp.route('/<paper_id>/slots/<slot_id>/reset', methods=['POST'])
def reset_slot(paper_id, slot_id):
    """Reset a slot to the original unedited question bank content."""
    try:
        updated = generator.reset_slot_edit(paper_id, slot_id)
        return jsonify({'success': True, 'paper': updated})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@generation_bp.route('/blueprint/<blueprint_id>', methods=['GET'])
def list_papers(blueprint_id):
    papers = generator.list_papers_for_blueprint(blueprint_id)
    return jsonify(papers)


@generation_bp.route('/swap', methods=['POST'])
def swap_question():
    data = request.json or {}
    bank_id = data.get('bank_id')
    unit = data.get('unit')
    exclude_ids = data.get('exclude_ids', [])
    
    if not bank_id or not unit:
        return jsonify({'error': 'bank_id and unit are required'}), 400
        
    try:
        replacement = generator.swap_question(bank_id, unit, exclude_ids)
        if not replacement:
            return jsonify({'error': 'No unused replacement questions are available for this unit.'}), 404
            
        return jsonify(replacement)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ── Image Upload & Asset Serving ───────────────────────────────────────────────

@generation_bp.route('/<paper_id>/upload-image', methods=['POST'])
@generation_bp.route('/upload-image', methods=['POST'])
def upload_image(paper_id=None):
    """
    Upload an image for a generated paper question.
    Accepts multipart/form-data ('image' file) or JSON with 'image_base64'.
    Validates MIME, size, Pillow decode, and stores safely in PAPER_ASSETS_FOLDER.
    """
    assets_folder = current_app.config.get("PAPER_ASSETS_FOLDER")
    if not assets_folder:
        assets_folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "storage", "paper_assets")
        os.makedirs(assets_folder, exist_ok=True)

    file_bytes = None
    orig_name = "upload.png"

    if 'image' in request.files:
        f = request.files['image']
        orig_name = f.filename or "upload.png"
        file_bytes = f.read()
    elif request.is_json:
        data = request.json or {}
        b64 = data.get('image_base64') or data.get('image')
        orig_name = data.get('filename') or "screenshot.png"
        if b64:
            if ',' in b64:
                b64 = b64.split(',', 1)[1]
            try:
                file_bytes = base64.b64decode(b64)
            except Exception:
                return jsonify({'error': 'Invalid base64 image data'}), 400

    if not file_bytes:
        return jsonify({'error': 'No image file provided'}), 400

    if len(file_bytes) > MAX_IMAGE_BYTES:
        return jsonify({'error': f'Image exceeds maximum size of {MAX_IMAGE_BYTES // (1024*1024)} MB'}), 413

    # Validate image with Pillow
    try:
        img_io = io.BytesIO(file_bytes)
        img = Image.open(img_io)
        img.verify()
        # Re-open for dimensions and format detection after verify()
        img_io.seek(0)
        img = Image.open(img_io)
        width, height = img.size
        img_format = (img.format or 'PNG').lower()
        if img_format == 'jpeg':
            img_format = 'jpg'
    except Exception as e:
        return jsonify({'error': f'Invalid or corrupted image: {str(e)}'}), 400

    if img_format not in ALLOWED_IMAGE_EXTS and not orig_name.lower().endswith(tuple(ALLOWED_IMAGE_EXTS)):
        return jsonify({'error': f'Unsupported image format "{img_format}"'}), 400

    ext = img_format if img_format in ALLOWED_IMAGE_EXTS else 'png'
    asset_id = str(uuid.uuid4())
    safe_filename = f"{asset_id}.{ext}"
    target_path = os.path.join(assets_folder, safe_filename)

    try:
        with open(target_path, 'wb') as out_f:
            out_f.write(file_bytes)
    except Exception as e:
        return jsonify({'error': f'Failed to write asset: {str(e)}'}), 500

    mime_type = f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}"
    slot_id = request.form.get('slot_id') or (request.json.get('slot_id') if request.is_json else None)

    asset_record = {
        'id': asset_id,
        'paper_id': paper_id,
        'slot_id': slot_id,
        'filename': safe_filename,
        'original_filename': orig_name,
        'mime_type': mime_type,
        'file_size': len(file_bytes),
        'width': width,
        'height': height,
        'alignment': 'center',
        'caption': ''
    }
    try:
        generator.record_paper_asset(asset_record)
    except Exception:
        pass  # Non-fatal if DB recording fails

    return jsonify({
        'asset_id': asset_id,
        'filename': safe_filename,
        'url': f'/api/generate/assets/{safe_filename}',
        'width': width,
        'height': height,
        'mime_type': mime_type
    }), 201


@generation_bp.route('/assets/<asset_id>', methods=['GET'])
def get_paper_asset(asset_id):
    """
    Serve a persisted paper image asset.
    Validates asset filename to prevent path traversal.
    """
    if not _is_safe_asset_name(asset_id):
        return jsonify({'error': 'Invalid asset identifier'}), 400

    assets_folder = current_app.config.get("PAPER_ASSETS_FOLDER")
    if not assets_folder:
        assets_folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "storage", "paper_assets")

    target_path = os.path.join(assets_folder, asset_id)
    if not os.path.isfile(target_path):
        return jsonify({'error': 'Asset not found'}), 404

    return send_from_directory(assets_folder, asset_id)


@generation_bp.route('/assets/<asset_id>', methods=['DELETE'])
def delete_paper_asset(asset_id):
    """Delete a paper asset record and file."""
    if not _is_safe_asset_name(asset_id):
        return jsonify({'error': 'Invalid asset identifier'}), 400

    assets_folder = current_app.config.get("PAPER_ASSETS_FOLDER")
    if assets_folder:
        target_path = os.path.join(assets_folder, asset_id)
        if os.path.isfile(target_path):
            try:
                os.remove(target_path)
            except Exception:
                pass

    generator.delete_paper_asset(asset_id)
    return jsonify({'success': True})


@generation_bp.route('/<paper_id>/export/docx', methods=['GET', 'POST'])
def export_paper_docx(paper_id):
    """
    Export a generated examination paper as a formal Microsoft Word (.docx) file.
    Includes college header, PRN boxes, instructions, questions, formulas, tables, and figures.
    """
    paper = generator.get_paper(paper_id)
    if not paper:
        return jsonify({'error': 'Paper not found'}), 404

    bank_info = None
    if paper.get('bank_id'):
        bank_info = qb_service.get_bank(paper['bank_id'])

    assets_folder = current_app.config.get("ASSETS_FOLDER")
    paper_assets_folder = current_app.config.get("PAPER_ASSETS_FOLDER")

    header_values = {}
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        header_values = body.get('header_values') or {}
    elif request.method == 'GET':
        hdr_param = request.args.get('header_values')
        if hdr_param:
            try:
                header_values = json.loads(hdr_param)
            except Exception:
                pass

    docx_io = export_paper_to_docx(
        paper,
        assets_folder=assets_folder,
        paper_assets_folder=paper_assets_folder,
        bank_info=bank_info,
        header_values=header_values
    )

    course_name = (
        header_values.get('header-course-name')
        or (bank_info.get('subject') if bank_info else None)
        or paper.get('course_name')
        or 'Question_Paper'
    )
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', course_name).strip('_') or 'Question_Paper'

    return send_file(
        docx_io,
        as_attachment=True,
        download_name=f"{safe_name}_Paper.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


