import uuid
import random
import json
import itertools
from backend.database import get_db
from backend.services.blueprint_service import BlueprintService
from backend.services.question_bank_service import QuestionBankService
from backend.services.table_classifier import is_question_bank_table, is_question_bank_table_html

blueprint_service = BlueprintService()
qb_service = QuestionBankService()


def is_valid_candidate_question(q: dict) -> bool:
    """
    Check if a question record is a valid individual candidate question.
    Rejects:
    - Empty or non-dict records
    - Records containing only question-bank index tables
    - Records with no meaningful text and no valid question assets
    - Non-question header/category labels
    """
    if not q or not isinstance(q, dict):
        return False

    blocks = q.get('blocks', [])
    if not blocks:
        return False

    has_meaningful_content = False
    for b in blocks:
        b_type = b.get('block_type')
        if b_type == 'text':
            txt = (b.get('text_content') or '').strip()
            if txt:
                if not any(k in txt.lower() for k in ['coding based questions', 'part a', 'part b', 'section a', 'section b']):
                    has_meaningful_content = True
            else:
                has_meaningful_content = True
        elif b_type in ('image', 'figure') and b.get('image_path'):
            has_meaningful_content = True
        elif b_type == 'table' and b.get('table_data'):
            t_data = b.get('table_data')
            if not is_question_bank_table(t_data) and not (isinstance(t_data, dict) and is_question_bank_table_html(t_data.get('html', ''))):
                has_meaningful_content = True

    return has_meaningful_content


class PaperGenerator:

    def generate(self, blueprint_id: str, bank_id: str, seed: int = None) -> dict:
        """Main entry point. Returns the full generated paper dict."""
        # 1. Load and validate blueprint
        bp = blueprint_service.get_blueprint(blueprint_id)
        if not bp:
            raise ValueError(f"Blueprint {blueprint_id} not found")

        if not bp['validation']['valid']:
            return {
                'error': 'Blueprint is invalid. Fix it before generating.',
                'blueprint_errors': bp['validation']['errors']
            }

        # 2. Load all questions in bank
        all_questions = qb_service.list_questions(bank_id)

        # 3. Set random seed for reproducibility
        rng = random.Random(seed)

        selected_ids = set()
        paper_slots = []
        warnings = []
        is_valid = True

        for mq in bp.get('main_questions', []):
            mq_label = mq.get('label', '')
            mq_unit = (mq.get('unit') or '').strip()
            attempt_rule = mq.get('attempt_rule', 'ALL')
            attempt_count = mq.get('attempt_count')
            mq_target_marks = float(mq.get('target_marks', 0))
            mq_order = mq.get('order_index', 0)

            def normalize_unit(u):
                if not u: return ''
                u = str(u).strip().lower()
                if u.isdigit(): return f"unit {u}"
                return u

            norm_mq_unit = normalize_unit(mq_unit)

            # Build unit-specific candidate pool of valid individual questions
            unit_pool = [
                q for q in all_questions
                if is_valid_candidate_question(q)
                and normalize_unit(q.get('unit')) == norm_mq_unit
                and q['id'] not in selected_ids
            ]

            for sq_idx, sq in enumerate(mq.get('subquestions', [])):
                sq_marks = float(sq.get('marks', 0))
                sq_label = sq.get('label', str(sq_idx + 1))
                sq_difficulty = sq.get('difficulty')
                sq_type = sq.get('question_type')

                # Filter candidates not yet selected
                candidates = [q for q in unit_pool if q['id'] not in selected_ids]

                # Apply difficulty filter (fallback to any if insufficient)
                if sq_difficulty:
                    diff_filtered = [
                        q for q in candidates
                        if (q.get('difficulty') or '').lower() == sq_difficulty.lower()
                    ]
                    if diff_filtered:
                        candidates = diff_filtered

                # Apply question type filter (fallback to any)
                if sq_type:
                    type_filtered = [
                        q for q in candidates
                        if (q.get('question_type') or '').lower() == sq_type.lower()
                    ]
                    if type_filtered:
                        candidates = type_filtered

                if candidates:
                    chosen = rng.choice(candidates)
                    selected_ids.add(chosen['id'])
                    # Remove from unit_pool so it won't be selected again in this MQ
                    unit_pool = [q for q in unit_pool if q['id'] != chosen['id']]
                    paper_slots.append({
                        'mq_label': mq_label,
                        'mq_instruction': mq.get('instruction', ''),
                        'mq_unit': mq_unit,
                        'mq_order': mq_order,
                        'mq_target_marks': mq_target_marks,
                        'mq_attempt_rule': attempt_rule,
                        'mq_attempt_count': attempt_count,
                        'sq_label': sq_label,
                        'sq_marks': sq_marks,
                        'sq_order': sq_idx,
                        'question_id': chosen['id'],
                        'is_filled': True,
                    })
                else:
                    is_valid = False
                    
                    # Diagnostics
                    total_in_bank = len(all_questions)
                    total_unclassified = len([q for q in all_questions if not normalize_unit(q.get('unit'))])
                    total_in_unit = len([q for q in all_questions if normalize_unit(q.get('unit')) == norm_mq_unit])
                    
                    reason = ""
                    if total_in_bank == 0:
                        reason = "No questions exist in this bank."
                    elif total_in_unit == 0:
                        if total_unclassified > 0:
                            reason = f"No questions classified under '{mq_unit}'. However, there are {total_unclassified} questions missing a unit assignment. Please assign units in the Document Processing review."
                        else:
                            reason = f"No questions classified under '{mq_unit}' in the bank."
                    else:
                        reason = f"Available in '{mq_unit}' = {total_in_unit} (already used = {total_in_unit - len(unit_pool)}). Add more questions to this unit."

                    warnings.append(
                        f"{mq_label}-{sq_label}: Cannot fill slot (Unit='{mq_unit}', Marks={sq_marks}). {reason}"
                    )
                    paper_slots.append({
                        'mq_label': mq_label,
                        'mq_instruction': mq.get('instruction', ''),
                        'mq_unit': mq_unit,
                        'mq_order': mq_order,
                        'mq_target_marks': mq_target_marks,
                        'mq_attempt_rule': attempt_rule,
                        'mq_attempt_count': attempt_count,
                        'sq_label': sq_label,
                        'sq_marks': sq_marks,
                        'sq_order': sq_idx,
                        'question_id': None,
                        'is_filled': False,
                    })

        # 4. Persist the generated paper
        paper_id = str(uuid.uuid4())
        with get_db() as db:
            db.execute(
                '''INSERT INTO generated_papers (id, blueprint_id, bank_id, is_valid, paper_total, warnings)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (paper_id, blueprint_id, bank_id, int(is_valid), bp['paper_total'], json.dumps(warnings))
            )
            for slot in paper_slots:
                slot_id = str(uuid.uuid4())
                db.execute(
                    '''INSERT INTO generated_slots
                       (id, paper_id, mq_label, mq_instruction, mq_unit, mq_order, mq_target_marks,
                        mq_attempt_rule, mq_attempt_count, sq_label, sq_marks, sq_order, question_id, is_filled)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (
                        slot_id, paper_id,
                        slot['mq_label'], slot['mq_instruction'], slot['mq_unit'],
                        slot['mq_order'], slot['mq_target_marks'],
                        slot['mq_attempt_rule'], slot['mq_attempt_count'],
                        slot['sq_label'], slot['sq_marks'], slot['sq_order'],
                        slot['question_id'], int(slot['is_filled'])
                    )
                )

        return self.get_paper(paper_id)

    def get_paper(self, paper_id: str) -> dict:
        """Load a generated paper from DB, enriched with question content."""
        with get_db() as db:
            paper = db.execute(
                'SELECT * FROM generated_papers WHERE id = ?', (paper_id,)
            ).fetchone()
            if not paper:
                return None
            paper_dict = dict(paper)
            paper_dict['warnings'] = json.loads(paper_dict.get('warnings') or '[]')

            slots = db.execute(
                '''SELECT * FROM generated_slots WHERE paper_id = ?
                   ORDER BY mq_order ASC, sq_order ASC''',
                (paper_id,)
            ).fetchall()
            slot_list = [dict(s) for s in slots]

        # Group slots into main questions
        mq_map = {}
        for slot in slot_list:
            label = slot['mq_label']
            if label not in mq_map:
                mq_map[label] = {
                    'label': label,
                    'instruction': slot['mq_instruction'],
                    'unit': slot['mq_unit'],
                    'order': slot['mq_order'],
                    'target_marks': slot['mq_target_marks'],
                    'attempt_rule': slot['mq_attempt_rule'],
                    'attempt_count': slot['mq_attempt_count'],
                    'subquestions': []
                }

            q_content = None
            is_edited = False
            edited_raw = slot.get('edited_content')
            if edited_raw:
                try:
                    edited_data = json.loads(edited_raw)
                    if isinstance(edited_data, dict):
                        is_edited = True
                        q_content = {
                            'id': slot['question_id'],
                            'is_edited': True,
                            'source_question_id': slot['question_id'],
                            'blocks': edited_data.get('blocks', []),
                            'html': edited_data.get('html', ''),
                        }
                    elif isinstance(edited_data, list):
                        is_edited = True
                        q_content = {
                            'id': slot['question_id'],
                            'is_edited': True,
                            'source_question_id': slot['question_id'],
                            'blocks': edited_data,
                            'html': '',
                        }
                except Exception:
                    pass

            if q_content is None and slot['question_id']:
                q_content = qb_service.get_question(slot['question_id'])
                if q_content and 'blocks' in q_content:
                    q_content['blocks'] = [
                        b for b in q_content['blocks']
                        if not (b.get('block_type') == 'table' and (is_question_bank_table(b.get('table_data')) or is_question_bank_table_html((b.get('table_data') or {}).get('html', ''))))
                    ]
                    q_content['is_edited'] = False
                    q_content['source_question_id'] = slot['question_id']

            mq_map[label]['subquestions'].append({
                'slot_id': slot['id'],
                'label': slot['sq_label'],
                'marks': slot['sq_marks'],
                'order': slot['sq_order'],
                'question_id': slot['question_id'],
                'is_filled': bool(slot['is_filled']),
                'is_edited': is_edited,
                'question': q_content,
            })

        paper_dict['main_questions'] = sorted(mq_map.values(), key=lambda x: x['order'])
        return paper_dict

    def save_paper_edits(self, paper_id: str, edits: dict) -> dict:
        """
        Save edited content and question assignments for a generated paper.
        Supports both full paper update (main_questions list or slots list)
        and individual slot updates.
        """
        with get_db() as db:
            paper = db.execute('SELECT * FROM generated_papers WHERE id = ?', (paper_id,)).fetchone()
            if not paper:
                raise ValueError(f"Paper {paper_id} not found")

            # Collect slot updates
            slot_updates = []
            if 'slots' in edits and isinstance(edits['slots'], list):
                slot_updates = edits['slots']
            elif 'main_questions' in edits and isinstance(edits['main_questions'], list):
                for mq in edits['main_questions']:
                    mq_label = mq.get('label')
                    for sq in mq.get('subquestions', []):
                        slot_id = sq.get('slot_id')
                        sq_label = sq.get('label')
                        q = sq.get('question')
                        edited_content = sq.get('edited_content')
                        if edited_content is None and q and q.get('is_edited'):
                            edited_content = {
                                'html': q.get('html', ''),
                                'blocks': q.get('blocks', [])
                            }
                        slot_updates.append({
                            'slot_id': slot_id,
                            'mq_label': mq_label,
                            'sq_label': sq_label,
                            'question_id': sq.get('question_id') or (q.get('id') if q else None),
                            'edited_content': edited_content,
                            'is_filled': 1 if sq.get('is_filled') else 0
                        })

            for item in slot_updates:
                slot_id = item.get('slot_id')
                edited_val = item.get('edited_content')
                if edited_val is not None and not isinstance(edited_val, str):
                    edited_val = json.dumps(edited_val)

                if slot_id:
                    db.execute(
                        '''UPDATE generated_slots
                           SET question_id = COALESCE(?, question_id),
                               edited_content = ?,
                               is_filled = COALESCE(?, is_filled)
                           WHERE id = ? AND paper_id = ?''',
                        (item.get('question_id'), edited_val, item.get('is_filled'), slot_id, paper_id)
                    )
                elif item.get('mq_label') and item.get('sq_label'):
                    db.execute(
                        '''UPDATE generated_slots
                           SET question_id = COALESCE(?, question_id),
                               edited_content = ?,
                               is_filled = COALESCE(?, is_filled)
                           WHERE paper_id = ? AND mq_label = ? AND sq_label = ?''',
                        (item.get('question_id'), edited_val, item.get('is_filled'), paper_id, item['mq_label'], item['sq_label'])
                    )

        return self.get_paper(paper_id)

    def save_slot_edit(self, paper_id: str, slot_id: str, edited_data: dict) -> dict:
        with get_db() as db:
            paper = db.execute('SELECT id FROM generated_papers WHERE id = ?', (paper_id,)).fetchone()
            if not paper:
                raise ValueError(f"Paper {paper_id} not found")

            content_json = json.dumps(edited_data) if not isinstance(edited_data, str) else edited_data
            cur = db.execute(
                '''UPDATE generated_slots
                   SET edited_content = ?, is_filled = 1
                   WHERE id = ? AND paper_id = ?''',
                (content_json, slot_id, paper_id)
            )
            if cur.rowcount == 0:
                raise ValueError(f"Slot {slot_id} not found in paper {paper_id}")
        return self.get_paper(paper_id)

    def reset_slot_edit(self, paper_id: str, slot_id: str) -> dict:
        with get_db() as db:
            cur = db.execute(
                '''UPDATE generated_slots
                   SET edited_content = NULL
                   WHERE id = ? AND paper_id = ?''',
                (slot_id, paper_id)
            )
            if cur.rowcount == 0:
                raise ValueError(f"Slot {slot_id} not found in paper {paper_id}")
        return self.get_paper(paper_id)

    def record_paper_asset(self, asset_dict: dict) -> dict:
        with get_db() as db:
            db.execute(
                '''INSERT INTO paper_assets
                   (id, paper_id, slot_id, filename, original_filename, mime_type, file_size, width, height, alignment, caption)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    asset_dict['id'],
                    asset_dict.get('paper_id'),
                    asset_dict.get('slot_id'),
                    asset_dict['filename'],
                    asset_dict.get('original_filename'),
                    asset_dict['mime_type'],
                    asset_dict['file_size'],
                    asset_dict.get('width'),
                    asset_dict.get('height'),
                    asset_dict.get('alignment', 'center'),
                    asset_dict.get('caption', '')
                )
            )
        return asset_dict

    def get_paper_asset(self, asset_id: str) -> dict | None:
        with get_db() as db:
            row = db.execute('SELECT * FROM paper_assets WHERE id = ?', (asset_id,)).fetchone()
            return dict(row) if row else None

    def delete_paper_asset(self, asset_id: str) -> bool:
        with get_db() as db:
            cur = db.execute('DELETE FROM paper_assets WHERE id = ?', (asset_id,))
            return cur.rowcount > 0

    def list_papers_for_blueprint(self, blueprint_id: str) -> list:
        with get_db() as db:
            papers = db.execute(
                '''SELECT id, created_at, is_valid, paper_total
                   FROM generated_papers WHERE blueprint_id = ?
                   ORDER BY created_at DESC''',
                (blueprint_id,)
            ).fetchall()
            return [dict(p) for p in papers]

    def swap_question(self, bank_id: str, unit: str, exclude_ids: list[str]) -> dict | None:
        all_questions = qb_service.list_questions(bank_id)
        
        def normalize_unit(u):
            if not u: return ''
            u = str(u).strip().lower()
            if u.isdigit(): return f"unit {u}"
            return u
            
        norm_unit = normalize_unit(unit)
        exclude_set = set(exclude_ids)
        
        candidates = [
            q for q in all_questions
            if is_valid_candidate_question(q)
            and normalize_unit(q.get('unit')) == norm_unit
            and q['id'] not in exclude_set
        ]
        
        if not candidates:
            return None
            
        chosen = random.choice(candidates)
        if chosen and 'blocks' in chosen:
            chosen['blocks'] = [
                b for b in chosen['blocks']
                if not (b.get('block_type') == 'table' and (is_question_bank_table(b.get('table_data')) or is_question_bank_table_html((b.get('table_data') or {}).get('html', ''))))
            ]
        return chosen
