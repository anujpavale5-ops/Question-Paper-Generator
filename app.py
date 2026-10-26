"""
Question Paper Generator — Phase 1
Flask application entry point.
"""

import os
import logging
from dotenv import load_dotenv
from flask import Flask
from flask_cors import CORS

load_dotenv()

from backend.routes.document_routes import document_bp

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def create_app(config: dict | None = None) -> Flask:
    """Application factory."""
    app = Flask(__name__, static_folder="frontend", static_url_path="")
    CORS(app)

    # ── Default config ──────────────────────────
    app.config.update(
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,   # 50 MB upload limit
        UPLOAD_FOLDER=os.path.join(os.path.dirname(__file__), "storage", "uploads"),
        ASSETS_FOLDER=os.path.join(os.path.dirname(__file__), "storage", "extracted_assets"),
        PAPER_ASSETS_FOLDER=os.path.join(os.path.dirname(__file__), "storage", "paper_assets"),
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-in-production"),
        ALLOWED_EXTENSIONS={"pdf", "docx"},
        ALLOWED_IMAGE_EXTENSIONS={"png", "jpg", "jpeg", "webp", "gif"},
    )
    if config:
        app.config.update(config)

    # ── Ensure storage directories exist ────────
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["ASSETS_FOLDER"], exist_ok=True)
    os.makedirs(app.config["PAPER_ASSETS_FOLDER"], exist_ok=True)

    # ── Register blueprints ──────────────────────
    from backend.routes.bank_routes import bank_bp
    from backend.routes.blueprint_routes import blueprint_bp
    from backend.routes.generation_routes import generation_bp
    from backend.database import init_db
    init_db()
    app.register_blueprint(document_bp, url_prefix="/api")
    app.register_blueprint(bank_bp, url_prefix="/api")
    app.register_blueprint(blueprint_bp)
    app.register_blueprint(generation_bp)

    # ── Serve frontend at root ───────────────────
    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    # ── Error handlers ───────────────────────────
    @app.errorhandler(413)
    def too_large(e):
        return {"error": "File too large. Maximum size is 50 MB."}, 413

    @app.errorhandler(404)
    def not_found(e):
        return {"error": "Resource not found."}, 404

    @app.errorhandler(500)
    def internal_error(e):
        logger.exception("Internal server error")
        return {"error": "An internal error occurred. Please try again."}, 500

    return app


if __name__ == "__main__":
    flask_app = create_app()
    flask_app.run(debug=True, host="0.0.0.0", port=5000)
