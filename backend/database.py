import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "storage", "question_bank.db")

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as db:
        db.execute('''
        CREATE TABLE IF NOT EXISTS question_banks (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            source_filename TEXT,
            source_document_id TEXT,
            file_hash TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            subject TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        db.execute('''
        CREATE TABLE IF NOT EXISTS blueprints (
            id TEXT PRIMARY KEY,
            bank_id TEXT NOT NULL,
            name TEXT,
            paper_total INTEGER DEFAULT 30,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (bank_id) REFERENCES question_banks (id) ON DELETE CASCADE
        )
        ''')
        
        db.execute('''
        CREATE TABLE IF NOT EXISTS main_questions (
            id TEXT PRIMARY KEY,
            blueprint_id TEXT NOT NULL,
            order_index INTEGER,
            label TEXT,
            instruction TEXT,
            unit TEXT,
            attempt_rule TEXT DEFAULT 'ALL',
            attempt_count INTEGER,
            target_marks REAL DEFAULT 0,
            FOREIGN KEY (blueprint_id) REFERENCES blueprints (id) ON DELETE CASCADE
        )
        ''')
        
        db.execute('''
        CREATE TABLE IF NOT EXISTS subquestions (
            id TEXT PRIMARY KEY,
            main_question_id TEXT NOT NULL,
            order_index INTEGER,
            label TEXT,
            marks REAL DEFAULT 1.0,
            cognitive_level TEXT,
            difficulty TEXT,
            question_type TEXT,
            FOREIGN KEY (main_question_id) REFERENCES main_questions (id) ON DELETE CASCADE
        )
        ''')
        
        db.execute('''
        CREATE TABLE IF NOT EXISTS questions (
            id TEXT PRIMARY KEY,
            bank_id TEXT NOT NULL,
            source_document_id TEXT,
            source_page INTEGER,
            unit TEXT,
            topic TEXT,
            difficulty TEXT,
            cognitive_level TEXT,
            question_type TEXT,
            estimated_marks REAL,
            tags TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (bank_id) REFERENCES question_banks (id) ON DELETE CASCADE
        )
        ''')

        db.execute('''
        CREATE TABLE IF NOT EXISTS content_blocks (
            id TEXT PRIMARY KEY,
            question_id TEXT NOT NULL,
            block_type TEXT NOT NULL,
            sequence_number INTEGER NOT NULL,
            text_content TEXT,
            table_data TEXT, -- JSON
            image_path TEXT,
            FOREIGN KEY (question_id) REFERENCES questions (id) ON DELETE CASCADE
        )
        ''')


        db.execute('''
        CREATE TABLE IF NOT EXISTS generated_papers (
            id TEXT PRIMARY KEY,
            blueprint_id TEXT NOT NULL,
            bank_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_valid INTEGER DEFAULT 0,
            paper_total REAL DEFAULT 0,
            warnings TEXT,
            FOREIGN KEY (blueprint_id) REFERENCES blueprints (id) ON DELETE CASCADE
        )
        ''')

        db.execute('''
        CREATE TABLE IF NOT EXISTS generated_slots (
            id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL,
            mq_label TEXT,
            mq_instruction TEXT,
            mq_unit TEXT,
            mq_order INTEGER,
            mq_target_marks REAL,
            mq_attempt_rule TEXT,
            mq_attempt_count INTEGER,
            sq_label TEXT,
            sq_marks REAL,
            sq_order INTEGER,
            question_id TEXT,
            is_filled INTEGER DEFAULT 0,
            edited_content TEXT,
            FOREIGN KEY (paper_id) REFERENCES generated_papers (id) ON DELETE CASCADE
        )
        ''')

        db.execute('''
        CREATE TABLE IF NOT EXISTS paper_assets (
            id TEXT PRIMARY KEY,
            paper_id TEXT,
            slot_id TEXT,
            filename TEXT NOT NULL,
            original_filename TEXT,
            mime_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            width INTEGER,
            height INTEGER,
            alignment TEXT DEFAULT 'center',
            caption TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (paper_id) REFERENCES generated_papers (id) ON DELETE CASCADE
        )
        ''')

        db.execute('CREATE INDEX IF NOT EXISTS idx_questions_bank ON questions(bank_id)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_blocks_question ON content_blocks(question_id)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_gen_slots_paper ON generated_slots(paper_id)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_gen_papers_blueprint ON generated_papers(blueprint_id)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_paper_assets_paper ON paper_assets(paper_id)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_paper_assets_slot ON paper_assets(slot_id)')

        # Bank snapshots for safe reprocessing and rollback
        db.execute('''
        CREATE TABLE IF NOT EXISTS bank_snapshots (
            id TEXT PRIMARY KEY,
            bank_id TEXT NOT NULL,
            snapshot_data TEXT NOT NULL,
            question_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (bank_id) REFERENCES question_banks (id) ON DELETE CASCADE
        )
        ''')

        # Safe schema migrations for metadata, review status, and provenance
        existing_cols = {r['name'] for r in db.execute('PRAGMA table_info(questions)').fetchall()}
        migration_cols = [
            ('course_outcome', 'TEXT'),
            ('bloom_level', 'TEXT'),
            ('confidence', 'REAL DEFAULT 1.0'),
            ('review_status', "TEXT DEFAULT 'accepted'"),
            ('review_reasons', 'TEXT'),
            ('source_question_number', 'TEXT'),
            ('original_text', 'TEXT'),
            ('is_repaired', 'INTEGER DEFAULT 0'),
            ('repair_metadata', 'TEXT'),
            ('extraction_method', "TEXT DEFAULT 'local'"),
            ('options', 'TEXT'),
            ('subquestions', 'TEXT'),
        ]
        for col_name, col_type in migration_cols:
            if col_name not in existing_cols:
                db.execute(f'ALTER TABLE questions ADD COLUMN {col_name} {col_type}')

        # Migration for generated_slots edited_content
        existing_slot_cols = {r['name'] for r in db.execute('PRAGMA table_info(generated_slots)').fetchall()}
        if 'edited_content' not in existing_slot_cols:
            db.execute('ALTER TABLE generated_slots ADD COLUMN edited_content TEXT')
        if 'sq_order' not in existing_slot_cols:
            db.execute('ALTER TABLE generated_slots ADD COLUMN sq_order INTEGER')

        # Migration for question_banks (file_hash, updated_at, subject)
        existing_bank_cols = {r['name'] for r in db.execute('PRAGMA table_info(question_banks)').fetchall()}
        if 'file_hash' not in existing_bank_cols:
            db.execute('ALTER TABLE question_banks ADD COLUMN file_hash TEXT')
        if 'updated_at' not in existing_bank_cols:
            db.execute('ALTER TABLE question_banks ADD COLUMN updated_at TIMESTAMP')
            db.execute('UPDATE question_banks SET updated_at = created_at WHERE updated_at IS NULL')
        if 'subject' not in existing_bank_cols:
            db.execute('ALTER TABLE question_banks ADD COLUMN subject TEXT')

        db.execute('CREATE INDEX IF NOT EXISTS idx_banks_hash ON question_banks(file_hash)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_snapshots_bank ON bank_snapshots(bank_id)')

        # Backfill file_hash for existing banks if upload file is present
        try:
            import hashlib
            uploads_dir = os.path.join(os.path.dirname(__file__), "..", "storage", "uploads")
            if os.path.isdir(uploads_dir):
                rows = db.execute('''
                    SELECT id, source_document_id 
                    FROM question_banks 
                    WHERE (file_hash IS NULL OR file_hash = '') AND source_document_id IS NOT NULL
                ''').fetchall()
                for r in rows:
                    b_id = r['id']
                    doc_id = r['source_document_id']
                    for ext in ('pdf', 'docx'):
                        target_file = os.path.join(uploads_dir, f"{doc_id}.{ext}")
                        if os.path.isfile(target_file):
                            try:
                                with open(target_file, 'rb') as f:
                                    h = hashlib.sha256(f.read()).hexdigest()
                                db.execute('UPDATE question_banks SET file_hash = ? WHERE id = ?', (h, b_id))
                                break
                            except Exception:
                                pass
        except Exception:
            pass

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
