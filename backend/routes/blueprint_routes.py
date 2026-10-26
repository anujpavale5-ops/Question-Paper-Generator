from flask import Blueprint, request, jsonify
from backend.services.blueprint_service import BlueprintService

blueprint_bp = Blueprint('blueprint_routes', __name__, url_prefix='/api/blueprints')
service = BlueprintService()

@blueprint_bp.route("/", methods=["POST"])
def create_blueprint():
    data = request.json
    bank_id = data.get('bank_id')
    if not bank_id:
        return jsonify({"error": "bank_id is required"}), 400
    bp = service.create_blueprint(bank_id, data.get('name', 'Blueprint'), data.get('paper_total', 30))
    return jsonify(bp)

@blueprint_bp.route("/<bp_id>", methods=["GET"])
def get_blueprint(bp_id):
    bp = service.get_blueprint(bp_id)
    if not bp:
        return jsonify({"error": "Not found"}), 404
    return jsonify(bp)

@blueprint_bp.route("/<bp_id>", methods=["PUT"])
def save_blueprint(bp_id):
    bp = service.save_blueprint(bp_id, request.json)
    return jsonify(bp)
