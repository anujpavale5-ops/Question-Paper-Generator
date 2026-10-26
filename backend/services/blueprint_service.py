import sqlite3
import uuid
import itertools
from backend.database import get_db

class BlueprintService:
    def get_blueprint(self, bp_id):
        with get_db() as db:
            bp = db.execute('SELECT * FROM blueprints WHERE id = ?', (bp_id,)).fetchone()
            if not bp: return None
            
            res = dict(bp)
            
            mqs = db.execute('SELECT * FROM main_questions WHERE blueprint_id = ? ORDER BY order_index ASC', (bp_id,)).fetchall()
            res['main_questions'] = []
            
            for mq in mqs:
                mq_dict = dict(mq)
                sqs = db.execute('SELECT * FROM subquestions WHERE main_question_id = ? ORDER BY order_index ASC', (mq['id'],)).fetchall()
                mq_dict['subquestions'] = [dict(sq) for sq in sqs]
                res['main_questions'].append(mq_dict)
                
            # Perform server-side validation against marks math
            validation = self._validate_blueprint_math(res)
            res['validation'] = validation
            
            return res

    def save_blueprint(self, bp_id, data):
        """Deep save of the entire blueprint structure."""
        with get_db() as db:
            # 1. Update Blueprint record
            if 'paper_total' in data:
                db.execute('UPDATE blueprints SET paper_total = ?, name = ? WHERE id = ?', 
                           (data['paper_total'], data.get('name', 'Blueprint'), bp_id))
            
            # 2. Clear old main questions (cascades to subquestions)
            db.execute('DELETE FROM main_questions WHERE blueprint_id = ?', (bp_id,))
            
            # 3. Insert new structure
            for mq_idx, mq in enumerate(data.get('main_questions', [])):
                mq_id = str(uuid.uuid4())
                db.execute('''INSERT INTO main_questions 
                              (id, blueprint_id, order_index, label, instruction, unit, attempt_rule, attempt_count, target_marks)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                           (mq_id, bp_id, mq_idx, mq.get('label'), mq.get('instruction'), mq.get('unit'), 
                            mq.get('attempt_rule', 'ALL'), mq.get('attempt_count'), mq.get('target_marks', 0.0)))
                
                for sq_idx, sq in enumerate(mq.get('subquestions', [])):
                    sq_id = str(uuid.uuid4())
                    db.execute('''INSERT INTO subquestions 
                                  (id, main_question_id, order_index, label, marks, cognitive_level, difficulty, question_type)
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                               (sq_id, mq_id, sq_idx, sq.get('label'), float(sq.get('marks', 1.0)),
                                sq.get('cognitive_level'), sq.get('difficulty'), sq.get('question_type')))
        
        return self.get_blueprint(bp_id)
        
    def create_blueprint(self, bank_id, name="Blueprint", paper_total=30):
        bp_id = str(uuid.uuid4())
        paper_total = int(paper_total)
        with get_db() as db:
            db.execute('''INSERT INTO blueprints (id, bank_id, name, paper_total)
                          VALUES (?, ?, ?, ?)''', (bp_id, bank_id, name, paper_total))
            
            if paper_total == 30:
                default_mqs = [
                    ('Q.1', 'Answer the following questions', 'Unit 1', 'ALL', None, 7.0, [('A', 3.0), ('B', 4.0)]),
                    ('Q.2', 'Answer the following questions', 'Unit 2', 'ALL', None, 7.0, [('A', 3.0), ('B', 4.0)]),
                    ('Q.3', 'Answer the following (Any 2)', 'Unit 3', 'ANY_N', 2, 8.0, [('A', 4.0), ('B', 4.0), ('C', 4.0)]),
                    ('Q.4', 'Answer the following (Any 2)', 'Unit 4', 'ANY_N', 2, 8.0, [('A', 4.0), ('B', 4.0), ('C', 4.0), ('D', 4.0)])
                ]
            else:
                default_mqs = [
                    ('Q.1', 'Answer the following questions', 'Unit 1', 'ALL', None, 10.0, [('A', 5.0), ('B', 5.0)]),
                    ('Q.2', 'Answer the following questions', 'Unit 2', 'ALL', None, 10.0, [('A', 5.0), ('B', 5.0)]),
                    ('Q.3', 'Answer the following questions', 'Unit 3', 'ALL', None, 10.0, [('A', 5.0), ('B', 5.0)]),
                    ('Q.4', 'Answer the following questions', 'Unit 4', 'ALL', None, 10.0, [('A', 5.0), ('B', 5.0)]),
                    ('Q.5', 'Answer the following questions', 'Unit 5', 'ALL', None, 10.0, [('A', 5.0), ('B', 5.0)]),
                    ('Q.6', 'Answer the following questions', 'Unit 6', 'ALL', None, 10.0, [('A', 5.0), ('B', 5.0)])
                ]
            
            for mq_idx, (lbl, instr, unit, rule, cnt, target, sqs) in enumerate(default_mqs):
                mq_id = str(uuid.uuid4())
                db.execute('''INSERT INTO main_questions 
                              (id, blueprint_id, order_index, label, instruction, unit, attempt_rule, attempt_count, target_marks)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                           (mq_id, bp_id, mq_idx, lbl, instr, unit, rule, cnt, target))
                for sq_idx, (sq_lbl, sq_marks) in enumerate(sqs):
                    sq_id = str(uuid.uuid4())
                    db.execute('''INSERT INTO subquestions 
                                  (id, main_question_id, order_index, label, marks)
                                  VALUES (?, ?, ?, ?, ?)''',
                               (sq_id, mq_id, sq_idx, sq_lbl, sq_marks))
                               
        return self.get_blueprint(bp_id)

    def _validate_blueprint_math(self, bp: dict) -> dict:
        errors = []
        total_allocated = 0.0
        
        for mq in bp.get('main_questions', []):
            sqs = mq.get('subquestions', [])
            rule = mq.get('attempt_rule', 'ALL')
            attempt_cnt = mq.get('attempt_count')
            target_marks = float(mq.get('target_marks', 0.0))
            
            if target_marks <= 0:
                errors.append(f"{mq.get('label', 'A question')} must have positive target marks.")
            
            total_allocated += target_marks
            
            if not sqs:
                errors.append(f"{mq.get('label', 'A question')} has no subquestions.")
                continue
            
            marks_list = [float(sq.get('marks', 0)) for sq in sqs]
            mq_calc = 0.0
            
            if rule == 'ALL':
                mq_calc = sum(marks_list)
                if mq_calc != target_marks:
                    errors.append(f"{mq.get('label')} subquestions ({mq_calc}m) do not add up to its specified total ({target_marks}m).")
            elif rule == 'ANY_N':
                if not attempt_cnt or attempt_cnt < 1:
                    errors.append(f"{mq.get('label')} is set to 'ANY_N' but attempt count is invalid.")
                    continue
                if attempt_cnt > len(marks_list):
                    errors.append(f"{mq.get('label')} requires attempting {attempt_cnt} questions, but only {len(marks_list)} are available.")
                    continue
                
                possible_sums = set()
                for combo in itertools.combinations(marks_list, attempt_cnt):
                    possible_sums.add(sum(combo))
                
                if len(possible_sums) > 1:
                    errors.append(f"{mq.get('label')} has unequal marks which makes an 'ANY {attempt_cnt}' rule mathematically ambiguous (possible totals: {', '.join(str(s) for s in possible_sums)}).")
                else:
                    mq_calc = possible_sums.pop()
                    if mq_calc != target_marks:
                        errors.append(f"{mq.get('label')} configured attempt rule produces {mq_calc}m, which does not match its specified total ({target_marks}m).")
        
        paper_total = float(bp.get('paper_total', 0))
        
        if total_allocated != paper_total:
            errors.append(f"Marks mismatch: Allocated marks ({total_allocated}) do not equal the paper total ({paper_total}).")
            
        return {
            "valid": len(errors) == 0,
            "allocated": total_allocated,
            "errors": errors
        }
