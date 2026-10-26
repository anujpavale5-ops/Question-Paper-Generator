import uuid
import json
from datetime import datetime
from backend.database import get_db
from backend.services.table_classifier import is_question_bank_table, is_question_bank_table_html

class QuestionBankService:
    def list_banks(self):
        with get_db() as db:
            banks = db.execute('''
                SELECT 
                    b.*,
                    (SELECT COUNT(*) FROM questions q WHERE q.bank_id = b.id) as question_count,
                    (SELECT COUNT(DISTINCT unit) FROM questions q WHERE q.bank_id = b.id AND unit IS NOT NULL AND TRIM(unit) != '') as unit_count,
                    (SELECT COUNT(*) FROM questions q WHERE q.bank_id = b.id AND review_status = 'needs_review') as review_needed_count,
                    (SELECT COUNT(*) FROM questions q WHERE q.bank_id = b.id AND (review_status = 'accepted' OR review_status IS NULL)) as auto_accepted_count,
                    (SELECT COUNT(*) FROM generated_papers gp WHERE gp.bank_id = b.id) as paper_count
                FROM question_banks b
                ORDER BY COALESCE(b.updated_at, b.created_at) DESC
            ''').fetchall()
            
            result = []
            for b in banks:
                b_dict = dict(b)
                # Compute review status
                q_cnt = b_dict['question_count']
                rev_needed = b_dict['review_needed_count']
                if rev_needed > 0:
                    b_dict['review_status'] = 'needs_review'
                elif q_cnt == 0:
                    b_dict['review_status'] = 'empty'
                else:
                    b_dict['review_status'] = 'ready'

                # Unit distribution summary
                unit_rows = db.execute('''
                    SELECT unit, COUNT(*) as count 
                    FROM questions 
                    WHERE bank_id = ? AND unit IS NOT NULL AND TRIM(unit) != ''
                    GROUP BY unit
                    ORDER BY unit ASC
                ''', (b['id'],)).fetchall()
                b_dict['unit_distribution'] = {r['unit']: r['count'] for r in unit_rows}
                result.append(b_dict)
            return result

    def create_bank(self, name, description="", source_filename=None, source_document_id=None, file_hash=None, subject=None):
        bank_id = str(uuid.uuid4())
        with get_db() as db:
            db.execute('''INSERT INTO question_banks 
                          (id, name, description, source_filename, source_document_id, file_hash, subject, updated_at) 
                          VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)''',
                       (bank_id, name, description, source_filename, source_document_id, file_hash, subject))
        return self.get_bank(bank_id)
        
    def get_bank(self, bank_id):
        with get_db() as db:
            bank = db.execute('''
                SELECT 
                    b.*,
                    (SELECT COUNT(*) FROM questions q WHERE q.bank_id = b.id) as question_count,
                    (SELECT COUNT(DISTINCT unit) FROM questions q WHERE q.bank_id = b.id AND unit IS NOT NULL AND TRIM(unit) != '') as unit_count,
                    (SELECT COUNT(*) FROM questions q WHERE q.bank_id = b.id AND review_status = 'needs_review') as review_needed_count,
                    (SELECT COUNT(*) FROM questions q WHERE q.bank_id = b.id AND (review_status = 'accepted' OR review_status IS NULL)) as auto_accepted_count,
                    (SELECT COUNT(*) FROM generated_papers gp WHERE gp.bank_id = b.id) as paper_count
                FROM question_banks b 
                WHERE b.id = ?
            ''', (bank_id,)).fetchone()
            if not bank:
                return None
            bank_dict = dict(bank)
            
            q_cnt = bank_dict['question_count']
            rev_needed = bank_dict['review_needed_count']
            if rev_needed > 0:
                bank_dict['review_status'] = 'needs_review'
            elif q_cnt == 0:
                bank_dict['review_status'] = 'empty'
            else:
                bank_dict['review_status'] = 'ready'

            unit_rows = db.execute('''
                SELECT unit, COUNT(*) as count 
                FROM questions 
                WHERE bank_id = ? AND unit IS NOT NULL AND TRIM(unit) != ''
                GROUP BY unit
                ORDER BY unit ASC
            ''', (bank_id,)).fetchall()
            bank_dict['unit_distribution'] = {r['unit']: r['count'] for r in unit_rows}
            return bank_dict
            
    def delete_bank(self, bank_id):
        with get_db() as db:
            # Clean up snapshots and paper assets for papers in this bank
            db.execute('DELETE FROM bank_snapshots WHERE bank_id = ?', (bank_id,))
            paper_ids = [r['id'] for r in db.execute('SELECT id FROM generated_papers WHERE bank_id = ?', (bank_id,)).fetchall()]
            for pid in paper_ids:
                db.execute('DELETE FROM paper_assets WHERE paper_id = ?', (pid,))
                db.execute('DELETE FROM generated_slots WHERE paper_id = ?', (pid,))
            db.execute('DELETE FROM generated_papers WHERE bank_id = ?', (bank_id,))
            # Delete blueprints
            bp_ids = [r['id'] for r in db.execute('SELECT id FROM blueprints WHERE bank_id = ?', (bank_id,)).fetchall()]
            for bpid in bp_ids:
                mq_ids = [r['id'] for r in db.execute('SELECT id FROM main_questions WHERE blueprint_id = ?', (bpid,)).fetchall()]
                for mqid in mq_ids:
                    db.execute('DELETE FROM subquestions WHERE main_question_id = ?', (mqid,))
                db.execute('DELETE FROM main_questions WHERE blueprint_id = ?', (bpid,))
            db.execute('DELETE FROM blueprints WHERE bank_id = ?', (bank_id,))
            # Delete questions and content blocks
            db.execute('DELETE FROM content_blocks WHERE question_id IN (SELECT id FROM questions WHERE bank_id = ?)', (bank_id,))
            db.execute('DELETE FROM questions WHERE bank_id = ?', (bank_id,))
            # Delete bank
            db.execute('DELETE FROM question_banks WHERE id = ?', (bank_id,))
            
    def check_duplicate_document(self, source_filename=None, file_hash=None):
        """
        Check for existing question banks by SHA-256 hash or filename.
        Returns a dict containing match_type ('exact' | 'filename_only'), bank dict,
        and backward-compatible top-level properties so existing callers continue to work.
        """
        if not source_filename and not file_hash:
            return None

        with get_db() as db:
            # 1. Exact match by content hash (preferred and definitive)
            if file_hash:
                bank = db.execute('''
                    SELECT * FROM question_banks 
                    WHERE file_hash = ? 
                    ORDER BY COALESCE(updated_at, created_at) DESC 
                    LIMIT 1
                ''', (file_hash,)).fetchone()
                if bank:
                    bank_data = self.get_bank(bank['id'])
                    result = dict(bank_data)
                    result['match_type'] = 'exact'
                    result['bank'] = bank_data
                    result['message'] = f"An identical question bank ('{bank['name']}') already exists for this exact file."
                    return result

            # 2. Filename match
            if source_filename:
                bank = db.execute('''
                    SELECT * FROM question_banks 
                    WHERE source_filename = ? 
                    ORDER BY COALESCE(updated_at, created_at) DESC 
                    LIMIT 1
                ''', (source_filename,)).fetchone()
                if bank:
                    bank_data = self.get_bank(bank['id'])
                    result = dict(bank_data)
                    # If content hash was supplied and differed, it's a filename_only match
                    if file_hash and bank['file_hash'] and bank['file_hash'] != file_hash:
                        result['match_type'] = 'filename_only'
                        result['message'] = f"A question bank with the filename '{source_filename}' exists, but the file content is different."
                    else:
                        result['match_type'] = 'exact' if (file_hash and bank['file_hash'] == file_hash) else 'filename_only'
                        result['message'] = f"A question bank for '{source_filename}' already exists."
                    result['bank'] = bank_data
                    return result

            return None

    def duplicate_bank(self, bank_id, new_name):
        bank = self.get_bank(bank_id)
        if not bank:
            raise ValueError("Bank not found")
            
        new_bank = self.create_bank(new_name, bank['description'])
        
        # Duplicate questions
        questions = self.list_questions(bank_id)
        for q in questions:
            # We need to recreate question with new id and its blocks
            self._duplicate_question_full(q['id'], new_bank['id'])
            
        return new_bank
        
    def _duplicate_question_full(self, original_q_id, new_bank_id):
        new_q_id = str(uuid.uuid4())
        with get_db() as db:
            q = db.execute('SELECT * FROM questions WHERE id = ?', (original_q_id,)).fetchone()
            if not q: return
            
            db.execute('''
                INSERT INTO questions (id, bank_id, source_document_id, source_page, unit, topic, 
                difficulty, cognitive_level, question_type, estimated_marks, tags,
                course_outcome, bloom_level, confidence, review_status, review_reasons, source_question_number,
                original_text, is_repaired, repair_metadata, extraction_method, options, subquestions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (new_q_id, new_bank_id, q['source_document_id'], q['source_page'], q['unit'], q['topic'],
                  q['difficulty'], q['cognitive_level'], q['question_type'], q['estimated_marks'], q['tags'],
                  q['course_outcome'] if 'course_outcome' in q.keys() else None,
                  q['bloom_level'] if 'bloom_level' in q.keys() else None,
                  q['confidence'] if 'confidence' in q.keys() else 1.0,
                  q['review_status'] if 'review_status' in q.keys() else 'accepted',
                  q['review_reasons'] if 'review_reasons' in q.keys() else None,
                  q['source_question_number'] if 'source_question_number' in q.keys() else None,
                  q['original_text'] if 'original_text' in q.keys() else None,
                  q['is_repaired'] if 'is_repaired' in q.keys() else 0,
                  q['repair_metadata'] if 'repair_metadata' in q.keys() else None,
                  q['extraction_method'] if 'extraction_method' in q.keys() else 'local',
                  q['options'] if 'options' in q.keys() else None,
                  q['subquestions'] if 'subquestions' in q.keys() else None))
                  
            blocks = db.execute('SELECT * FROM content_blocks WHERE question_id = ? ORDER BY sequence_number ASC', (original_q_id,)).fetchall()
            seq = 0
            for b in blocks:
                # Skip index tables
                if b['block_type'] == 'table' and b['table_data']:
                    t_data = json.loads(b['table_data'])
                    if is_question_bank_table(t_data) or (isinstance(t_data, dict) and is_question_bank_table_html(t_data.get('html', ''))):
                        continue
                b_id = str(uuid.uuid4())
                db.execute('''
                    INSERT INTO content_blocks (id, question_id, block_type, sequence_number, text_content, table_data, image_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (b_id, new_q_id, b['block_type'], seq, b['text_content'], b['table_data'], b['image_path']))
                seq += 1
                
    def list_questions(self, bank_id):
        with get_db() as db:
            questions = db.execute('SELECT * FROM questions WHERE bank_id = ? ORDER BY created_at ASC', (bank_id,)).fetchall()
            result = []
            for q in questions:
                q_dict = dict(q)
                if q_dict.get('options') and isinstance(q_dict['options'], str):
                    try:
                        q_dict['options'] = json.loads(q_dict['options'])
                    except Exception:
                        pass
                if q_dict.get('subquestions') and isinstance(q_dict['subquestions'], str):
                    try:
                        q_dict['subquestions'] = json.loads(q_dict['subquestions'])
                    except Exception:
                        pass
                blocks = db.execute('SELECT * FROM content_blocks WHERE question_id = ? ORDER BY sequence_number ASC', (q['id'],)).fetchall()
                clean_blocks = []
                for b in blocks:
                    b_dict = dict(b)
                    if b_dict['table_data']:
                        t_data = json.loads(b_dict['table_data'])
                        if is_question_bank_table(t_data) or (isinstance(t_data, dict) and is_question_bank_table_html(t_data.get('html', ''))):
                            continue
                        b_dict['table_data'] = t_data
                    clean_blocks.append(b_dict)
                q_dict['blocks'] = clean_blocks
                result.append(q_dict)
            return result
            
    def get_question(self, question_id):
        with get_db() as db:
            q = db.execute('SELECT * FROM questions WHERE id = ?', (question_id,)).fetchone()
            if not q: return None
            q_dict = dict(q)
            if q_dict.get('options') and isinstance(q_dict['options'], str):
                try:
                    q_dict['options'] = json.loads(q_dict['options'])
                except Exception:
                    pass
            if q_dict.get('subquestions') and isinstance(q_dict['subquestions'], str):
                try:
                    q_dict['subquestions'] = json.loads(q_dict['subquestions'])
                except Exception:
                    pass
            blocks = db.execute('SELECT * FROM content_blocks WHERE question_id = ? ORDER BY sequence_number ASC', (question_id,)).fetchall()
            clean_blocks = []
            for b in blocks:
                b_dict = dict(b)
                if b_dict['table_data']:
                    t_data = json.loads(b_dict['table_data'])
                    if is_question_bank_table(t_data) or (isinstance(t_data, dict) and is_question_bank_table_html(t_data.get('html', ''))):
                        continue
                    b_dict['table_data'] = t_data
                clean_blocks.append(b_dict)
            q_dict['blocks'] = clean_blocks
            return q_dict

    def create_question(self, bank_id, data):
        q_id = str(uuid.uuid4())
        reasons = data.get('review_reasons')
        if isinstance(reasons, list):
            reasons = json.dumps(reasons)
        confidence = float(data.get('confidence', 1.0)) if data.get('confidence') is not None else 1.0
        review_status = data.get('review_status', 'accepted')

        orig_text = data.get('original_text')
        is_rep = 1 if data.get('is_repaired') else 0
        repair_meta = data.get('repair_metadata')
        if isinstance(repair_meta, (dict, list)):
            repair_meta = json.dumps(repair_meta)

        ext_method = data.get('extraction_method') or 'local'
        options = data.get('options')
        if isinstance(options, list):
            options = json.dumps(options)
        subquestions = data.get('subquestions')
        if isinstance(subquestions, list):
            subquestions = json.dumps(subquestions)

        with get_db() as db:
            db.execute('''
                INSERT INTO questions (id, bank_id, source_document_id, source_page, unit, topic, 
                difficulty, cognitive_level, question_type, estimated_marks, tags,
                course_outcome, bloom_level, confidence, review_status, review_reasons, source_question_number,
                original_text, is_repaired, repair_metadata, extraction_method, options, subquestions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (q_id, bank_id, data.get('source_document_id'), data.get('source_page'), 
                  data.get('unit'), data.get('topic'), data.get('difficulty'), data.get('cognitive_level'),
                  data.get('question_type'), data.get('estimated_marks'), data.get('tags'),
                  data.get('course_outcome'), data.get('bloom_level'), confidence, review_status, reasons,
                  data.get('source_question_number'), orig_text, is_rep, repair_meta,
                  ext_method, options, subquestions))
                  
            blocks_to_insert = list(data.get('blocks', []))
            has_text_block = any(b.get('block_type') == 'text' and b.get('text_content') for b in blocks_to_insert)
            fallback_text = data.get('text') or data.get('question_text') or data.get('raw_text')
            if not has_text_block and fallback_text:
                blocks_to_insert.insert(0, {
                    'block_type': 'text',
                    'text_content': fallback_text,
                    'table_data': None,
                    'image_path': None
                })

            seq = 0
            for block in blocks_to_insert:
                if block.get('block_type') == 'table':
                    t_data = block.get('table_data')
                    if is_question_bank_table(t_data) or (isinstance(t_data, dict) and is_question_bank_table_html(t_data.get('html', ''))):
                        continue
                t_data = block.get('table_data')
                t_dict = t_data.to_dict() if hasattr(t_data, 'to_dict') else t_data
                table_json = json.dumps(t_dict) if t_dict else None
                b_id = str(uuid.uuid4())
                db.execute('''
                    INSERT INTO content_blocks (id, question_id, block_type, sequence_number, text_content, table_data, image_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (b_id, q_id, block.get('block_type', 'text'), seq, block.get('text_content'), table_json, block.get('image_path')))
                seq += 1
        return self.get_question(q_id)

    def update_question(self, question_id, data):
        reasons = data.get('review_reasons')
        if isinstance(reasons, list):
            reasons = json.dumps(reasons)
        confidence = float(data['confidence']) if 'confidence' in data and data['confidence'] is not None else None

        with get_db() as db:
            current = db.execute('SELECT * FROM questions WHERE id = ?', (question_id,)).fetchone()
            if not current:
                return None

            unit = data['unit'] if 'unit' in data else current['unit']
            topic = data['topic'] if 'topic' in data else current['topic']
            difficulty = data['difficulty'] if 'difficulty' in data else current['difficulty']
            cognitive_level = data['cognitive_level'] if 'cognitive_level' in data else current['cognitive_level']
            question_type = data['question_type'] if 'question_type' in data else current['question_type']
            estimated_marks = data['estimated_marks'] if 'estimated_marks' in data else current['estimated_marks']
            tags = data['tags'] if 'tags' in data else current['tags']
            course_outcome = data['course_outcome'] if 'course_outcome' in data else current['course_outcome']
            bloom_level = data['bloom_level'] if 'bloom_level' in data else current['bloom_level']
            conf = confidence if confidence is not None else current['confidence']
            review_status = data['review_status'] if 'review_status' in data else current['review_status']
            rev_reasons = reasons if reasons is not None else current['review_reasons']
            source_q_no = data['source_question_number'] if 'source_question_number' in data else current['source_question_number']
            orig_text = data['original_text'] if 'original_text' in data else current['original_text']
            is_rep = data['is_repaired'] if 'is_repaired' in data else (current['is_repaired'] if 'is_repaired' in current.keys() else 0)
            repair_meta = data['repair_metadata'] if 'repair_metadata' in data else current['repair_metadata']
            if isinstance(repair_meta, (dict, list)):
                repair_meta = json.dumps(repair_meta)
            ext_method = data['extraction_method'] if 'extraction_method' in data else current['extraction_method']
            opts = data['options'] if 'options' in data else current['options']
            if isinstance(opts, list):
                opts = json.dumps(opts)
            subqs = data['subquestions'] if 'subquestions' in data else current['subquestions']
            if isinstance(subqs, list):
                subqs = json.dumps(subqs)

            db.execute('''
                UPDATE questions SET 
                    unit = ?, topic = ?, difficulty = ?, cognitive_level = ?, 
                    question_type = ?, estimated_marks = ?, tags = ?,
                    course_outcome = ?, bloom_level = ?, confidence = ?,
                    review_status = ?, review_reasons = ?, source_question_number = ?,
                    original_text = ?, is_repaired = ?, repair_metadata = ?,
                    extraction_method = ?, options = ?, subquestions = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (unit, topic, difficulty, cognitive_level, question_type, estimated_marks, tags,
                  course_outcome, bloom_level, conf, review_status, rev_reasons, source_q_no,
                  orig_text, is_rep, repair_meta, ext_method, opts, subqs, question_id))
                  
            if 'blocks' in data:
                db.execute('DELETE FROM content_blocks WHERE question_id = ?', (question_id,))
                seq = 0
                for block in data['blocks']:
                    if block.get('block_type') == 'table':
                        t_data = block.get('table_data')
                        if is_question_bank_table(t_data) or (isinstance(t_data, dict) and is_question_bank_table_html(t_data.get('html', ''))):
                            continue
                    b_id = str(uuid.uuid4())
                    table_json = json.dumps(block['table_data']) if block.get('table_data') else None
                    db.execute('''
                        INSERT INTO content_blocks (id, question_id, block_type, sequence_number, text_content, table_data, image_path)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (b_id, question_id, block.get('block_type', 'text'), seq, block.get('text_content'), table_json, block.get('image_path')))
                    seq += 1
        return self.get_question(question_id)

    def list_review_exceptions(self, bank_id):
        with get_db() as db:
            rows = db.execute('''
                SELECT * FROM questions 
                WHERE bank_id = ? AND review_status = 'needs_review'
                ORDER BY source_page ASC, created_at ASC
            ''', (bank_id,)).fetchall()
            result = []
            for r in rows:
                q_dict = dict(r)
                blocks = db.execute('SELECT * FROM content_blocks WHERE question_id = ? ORDER BY sequence_number ASC', (r['id'],)).fetchall()
                q_dict['blocks'] = [dict(b) for b in blocks]
                result.append(q_dict)
            return result

    def batch_update_review(self, bank_id, items: list[dict]):
        updated = []
        for it in items:
            q_id = it.get('id')
            if not q_id: continue
            patch = {}
            if 'unit' in it: patch['unit'] = it['unit']
            if 'review_status' in it: patch['review_status'] = it['review_status']
            if 'difficulty' in it: patch['difficulty'] = it['difficulty']
            res = self.update_question(q_id, patch)
            if res: updated.append(res)
        return updated

    def delete_question(self, question_id):
        with get_db() as db:
            db.execute('DELETE FROM questions WHERE id = ?', (question_id,))

    def create_bank_snapshot(self, bank_id):
        """Preserve a recoverable previous snapshot before reprocessing."""
        bank = self.get_bank(bank_id)
        if not bank:
            return None
        questions = self.list_questions(bank_id)
        snapshot_id = str(uuid.uuid4())
        snapshot_payload = {
            "bank": bank,
            "questions": questions
        }
        with get_db() as db:
            db.execute('''
                INSERT INTO bank_snapshots (id, bank_id, snapshot_data, question_count)
                VALUES (?, ?, ?, ?)
            ''', (snapshot_id, bank_id, json.dumps(snapshot_payload), len(questions)))
        return snapshot_id

    def list_bank_snapshots(self, bank_id):
        with get_db() as db:
            rows = db.execute('''
                SELECT id, bank_id, question_count, created_at
                FROM bank_snapshots
                WHERE bank_id = ?
                ORDER BY created_at DESC
            ''', (bank_id,)).fetchall()
            return [dict(r) for r in rows]

    def restore_bank_snapshot(self, bank_id, snapshot_id=None):
        """Roll back questions and blocks to a saved snapshot."""
        with get_db() as db:
            if snapshot_id:
                snap = db.execute('SELECT * FROM bank_snapshots WHERE id = ? AND bank_id = ?', (snapshot_id, bank_id)).fetchone()
            else:
                snap = db.execute('SELECT * FROM bank_snapshots WHERE bank_id = ? ORDER BY created_at DESC LIMIT 1', (bank_id,)).fetchone()
            if not snap:
                return None
            data = json.loads(snap['snapshot_data'])
            questions = data.get('questions', [])

            # Clear current questions & blocks
            db.execute('DELETE FROM content_blocks WHERE question_id IN (SELECT id FROM questions WHERE bank_id = ?)', (bank_id,))
            db.execute('DELETE FROM questions WHERE bank_id = ?', (bank_id,))

            for q in questions:
                q_id = q.get('id') or str(uuid.uuid4())
                reasons = q.get('review_reasons')
                if isinstance(reasons, list):
                    reasons = json.dumps(reasons)
                confidence = float(q.get('confidence', 1.0)) if q.get('confidence') is not None else 1.0
                orig_text = q.get('original_text')
                is_rep = 1 if q.get('is_repaired') else 0
                repair_meta = q.get('repair_metadata')
                if isinstance(repair_meta, (dict, list)):
                    repair_meta = json.dumps(repair_meta)
                ext_method = q.get('extraction_method') or 'local'
                opts = q.get('options')
                if isinstance(opts, list):
                    opts = json.dumps(opts)
                subqs = q.get('subquestions')
                if isinstance(subqs, list):
                    subqs = json.dumps(subqs)

                db.execute('''
                    INSERT INTO questions (id, bank_id, source_document_id, source_page, unit, topic, 
                    difficulty, cognitive_level, question_type, estimated_marks, tags,
                    course_outcome, bloom_level, confidence, review_status, review_reasons, source_question_number,
                    original_text, is_repaired, repair_metadata, extraction_method, options, subquestions)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (q_id, bank_id, q.get('source_document_id'), q.get('source_page'),
                      q.get('unit'), q.get('topic'), q.get('difficulty'), q.get('cognitive_level'),
                      q.get('question_type'), q.get('estimated_marks'), q.get('tags'),
                      q.get('course_outcome'), q.get('bloom_level'), confidence,
                      q.get('review_status', 'accepted'), reasons, q.get('source_question_number'),
                      orig_text, is_rep, repair_meta, ext_method, opts, subqs))
                
                seq = 0
                for b in q.get('blocks', []):
                    b_id = b.get('id') or str(uuid.uuid4())
                    t_data = b.get('table_data')
                    table_json = json.dumps(t_data) if t_data else None
                    db.execute('''
                        INSERT INTO content_blocks (id, question_id, block_type, sequence_number, text_content, table_data, image_path)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (b_id, q_id, b.get('block_type', 'text'), seq, b.get('text_content'), table_json, b.get('image_path')))
                    seq += 1

            db.execute('UPDATE question_banks SET updated_at = CURRENT_TIMESTAMP WHERE id = ?', (bank_id,))
        return self.get_bank(bank_id)

    def reprocess_bank(self, bank_id, questions_data: list[dict], new_document_id=None, new_hash=None, new_filename=None):
        """
        Safely reprocess questions for an existing bank.
        Preserves snapshot, reconciles questions transactionally, preserves bank identity,
        retains existing generated papers, and reports what changed.
        """
        bank = self.get_bank(bank_id)
        if not bank:
            raise ValueError(f"Bank {bank_id} not found")

        old_count = bank.get('question_count', 0)
        # 1. Preserve a recoverable snapshot
        snapshot_id = self.create_bank_snapshot(bank_id)

        try:
            with get_db() as db:
                # 2. Transactionally clear old questions & blocks
                db.execute('DELETE FROM content_blocks WHERE question_id IN (SELECT id FROM questions WHERE bank_id = ?)', (bank_id,))
                db.execute('DELETE FROM questions WHERE bank_id = ?', (bank_id,))

                # 3. Update bank metadata if provided
                updates = ["updated_at = CURRENT_TIMESTAMP"]
                params = []
                if new_document_id:
                    updates.append("source_document_id = ?")
                    params.append(new_document_id)
                if new_hash:
                    updates.append("file_hash = ?")
                    params.append(new_hash)
                if new_filename:
                    updates.append("source_filename = ?")
                    params.append(new_filename)
                params.append(bank_id)
                db.execute(f"UPDATE question_banks SET {', '.join(updates)} WHERE id = ?", tuple(params))

            # 4. Re-insert new questions
            imported = []
            for q_data in questions_data:
                imported.append(self.create_question(bank_id, q_data))

            updated_bank = self.get_bank(bank_id)
            new_count = updated_bank.get('question_count', 0)
            updated_bank['reprocess_summary'] = {
                "previous_count": old_count,
                "new_count": new_count,
                "diff": new_count - old_count,
                "snapshot_id": snapshot_id
            }
            return updated_bank

        except Exception as e:
            # Revert to snapshot if anything fails
            try:
                self.restore_bank_snapshot(bank_id, snapshot_id)
            except Exception:
                pass
            raise e

