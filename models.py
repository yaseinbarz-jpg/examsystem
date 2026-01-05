# backend/models.py
import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional, Any
import re

DB_PATH = "backend/database.db"

PROVINCES = [
    "آذربایجان شرقی","آذربایجان غربی","اردبیل","اصفهان","البرز","ایلام",
    "بوشهر","تهران","چهارمحال و بختیاری","خراسان جنوبی","خراسان رضوی",
    "خراسان شمالی","خوزستان","زنجان","سمنان","سیستان و بلوچستان",
    "فارس","قزوین","قم","کردستان","کرمان","کرمانشاه","کهگیلویه و بویراحمد",
    "گلستان","گیلان","لرستان","مازندران","مرکزی","هرمزگان","همدان","یزد"
]

# ---------------- DB utils ----------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def _table_has_column(cur, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r["name"] for r in cur.fetchall()]
    return column in cols

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # exams
    cur.execute("""
    CREATE TABLE IF NOT EXISTS exams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT,
        created_at TEXT
    )""")

    # questions
    cur.execute("""
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        option_a TEXT,
        option_b TEXT,
        option_c TEXT,
        option_d TEXT,
        correct TEXT,
        topic TEXT,
        FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE
    )""")

    # results
    cur.execute("""
    CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id INTEGER NOT NULL,
        student_name TEXT NOT NULL,
        phone TEXT DEFAULT '',
        province TEXT NOT NULL,
        score REAL,
        tazr REAL,
        rank_national INTEGER,
        rank_provincial INTEGER,
        details_json TEXT,
        created_at TEXT,
        FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE
    )""")
    try:
        if not _table_has_column(cur, "results", "phone"):
            cur.execute("ALTER TABLE results ADD COLUMN phone TEXT DEFAULT ''")
    except Exception:
        pass

    conn.commit()
    conn.close()

# init on import
init_db()

# ---------------- helpers for normalization ----------------
# Persian digits -> ASCII digits mapping (including Arabic-Indic digits)
_PERSIAN_TO_ASCII_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

def normalize_phone(phone_raw: str) -> str:
    """
    Convert any Persian digits to ASCII and strip non-digits.
    Returns ASCII-only digits string (or empty string).
    """
    if phone_raw is None:
        return ""
    s = str(phone_raw).translate(_PERSIAN_TO_ASCII_DIGITS)
    s = re.sub(r"\D", "", s)
    return s

def contains_latin_letters(s: str) -> bool:
    return re.search(r"[A-Za-z]", s) is not None

def contains_persian_letters(s: str) -> bool:
    return re.search(r"[\u0600-\u06FF]", s) is not None

def is_persian_name_strict(s: str) -> bool:
    """
    Strict check: only Persian letters and spaces allowed.
    (No digits, punctuation, latin chars, etc.)
    """
    if not s:
        return False
    # allow Persian/Arabic letters and spaces and zero-width non-joiner
    return re.fullmatch(r"^[\u0600-\u06FF\s\u200c]+$", s) is not None

def normalize_name(text: str) -> str:
    if not text:
        return ""
    t = text.translate(_PERSIAN_TO_ASCII_DIGITS)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def normalize_combined_input(input_str: str) -> (str, str):
    """
    Accept inputs like "علی-۰۹۱۲..." or "علی_0912...".
    Returns (combined_string, phone_ascii)
    combined_string is name_phone with underscore between.
    phone returned as ASCII digits (already translated).
    """
    if not input_str:
        return ("", "")
    s = str(input_str).strip()
    # convert persian digits to ascii for consistent detection
    s = s.translate(_PERSIAN_TO_ASCII_DIGITS)
    # replace any sequence of non-letter/digit with underscore (keep Persian letters too)
    s_clean = re.sub(r"[^\w\u0600-\u06FF]+", "_", s)
    m = re.search(r"\d+", s_clean)
    if m:
        phone = m.group()
        name_part = s_clean[:m.start()].strip("_")
        name_part = re.sub(r"_+", " ", name_part).strip()
        combined = f"{name_part}_{phone}" if name_part else phone
        combined = re.sub(r"_+", "_", combined).strip("_")
        return (combined, phone)
    else:
        name = re.sub(r"_+", " ", s_clean).strip()
        return (name, "")

# ---------------- Exams ----------------
def add_exam(title: str, description: str = "") -> int:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT INTO exams (title, description, created_at) VALUES (?, ?, ?)",
                (title, description, datetime.utcnow().isoformat()))
    conn.commit()
    eid = cur.lastrowid
    conn.close()
    return eid

def list_exams() -> List[Dict[str, Any]]:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM exams ORDER BY id DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def get_exam(exam_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM exams WHERE id = ?", (exam_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def delete_exam(exam_id: int):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM results WHERE exam_id = ?", (exam_id,))
    cur.execute("DELETE FROM questions WHERE exam_id = ?", (exam_id,))
    cur.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
    conn.commit()
    conn.close()

# ---------------- Questions ----------------
def add_question(exam_id: int, text: str, option_a: str, option_b: str, option_c: str, option_d: str, correct: str, topic: str = "") -> int:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""INSERT INTO questions (exam_id, text, option_a, option_b, option_c, option_d, correct, topic)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (exam_id, text, option_a, option_b, option_c, option_d, (correct or "").strip().upper(), topic))
    conn.commit()
    qid = cur.lastrowid
    conn.close()
    return qid

def get_exam_questions(exam_id: int) -> List[Dict[str, Any]]:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM questions WHERE exam_id = ? ORDER BY id", (exam_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def delete_question(question_id: int):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM questions WHERE id = ?", (question_id,))
    conn.commit()
    conn.close()

# ---------------- Students / Results helpers ----------------
def has_student_taken_by_phone(exam_id: int, phone_raw: str) -> bool:
    phone = normalize_phone(phone_raw)
    if not phone:
        return False
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM results WHERE exam_id = ? AND phone = ? LIMIT 1", (exam_id, phone))
    exists = cur.fetchone() is not None
    conn.close()
    return exists

def get_result_by_phone(exam_id: int, phone_raw: str) -> Optional[Dict[str, Any]]:
    phone = normalize_phone(phone_raw)
    if not phone:
        return None
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM results WHERE exam_id = ? AND phone = ? LIMIT 1", (exam_id, phone))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def get_results(exam_id: int, province: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_conn(); cur = conn.cursor()
    if province:
        cur.execute("SELECT * FROM results WHERE exam_id = ? AND province = ? ORDER BY tazr DESC, created_at ASC", (exam_id, province))
    else:
        cur.execute("SELECT * FROM results WHERE exam_id = ? ORDER BY tazr DESC, created_at ASC", (exam_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        try:
            r['details'] = json.loads(r.get('details_json') or "{}")
        except Exception:
            r['details'] = {}
    return rows

def get_result_by_id(result_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM results WHERE id = ?", (result_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    res = dict(row)
    try:
        res['details'] = json.loads(res.get('details_json') or "{}")
    except Exception:
        res['details'] = {}
    return res

# ---------------- NEW: exact-duplicate helper used by routes ----------------
def has_exact_duplicate(exam_id: int, student_name: str, phone_raw: Optional[str], province: str) -> Optional[Dict[str, Any]]:
    """
    Return existing result row dict if there's an exact duplicate:
    - if phone provided (non-empty), match by exam_id+phone
    - otherwise match by exam_id+student_name+province
    Returns dict(row) or None.
    """
    phone = normalize_phone(phone_raw or "")
    student_name_clean = normalize_name(student_name)
    province = (province or "").strip()

    conn = get_conn(); cur = conn.cursor()
    if phone:
        cur.execute("SELECT * FROM results WHERE exam_id = ? AND phone = ? LIMIT 1", (exam_id, phone))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None
    else:
        # match by exact student_name + province
        cur.execute("SELECT * FROM results WHERE exam_id = ? AND student_name = ? AND province = ? LIMIT 1", (exam_id, student_name_clean, province))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

# ---------------- ranking and save result ----------------
def recalc_ranks_for_exam(exam_id: int):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, tazr, province FROM results WHERE exam_id = ? ORDER BY tazr DESC, created_at ASC", (exam_id,))
    rows = cur.fetchall()

    # national (competition ranking: ties share same rank, next rank = index)
    rank = 0
    last_score = None
    for i, r in enumerate(rows, start=1):
        sid = r['id']; score = r['tazr']
        if score != last_score:
            rank = i
            last_score = score
        cur.execute("UPDATE results SET rank_national = ? WHERE id = ?", (rank, sid))

    # provincial: apply same "competition ranking" within each province
    provinces = {}
    for r in rows:
        provinces.setdefault(r['province'], []).append(r)

    for prov, prov_rows in provinces.items():
        # sort by tazr desc, created_at asc (prov_rows are already in global order by tazr,created_at,
        # but to be safe, sort explicitly using tazr desc then id asc)
        prov_sorted = sorted(prov_rows, key=lambda x: (-x['tazr'], x['id']))
        last_score_p = None
        rank_p = 0
        for idx, r in enumerate(prov_sorted, start=1):
            sid = r['id']; score = r['tazr']
            if score != last_score_p:
                rank_p = idx
                last_score_p = score
            cur.execute("UPDATE results SET rank_provincial = ? WHERE id = ?", (rank_p, sid))

    conn.commit()
    conn.close()

def save_result(exam_id: int, student_name: str, phone_raw: Optional[str], province: str, answers: Any, tazr: Optional[float] = None) -> Optional[int]:
    """
    Save a result if phone not already exists for this exam.
    Returns inserted id, or None if duplicate phone/name+province detected.
    This function now performs the existence-check + insert inside a BEGIN IMMEDIATE transaction
    to avoid race conditions on concurrent submissions.
    """
    student_name = normalize_name(student_name)
    phone = normalize_phone(phone_raw or "")
    province = (province or "").strip()

    # parse answers safely
    if isinstance(answers, str):
        try:
            parsed = json.loads(answers)
            answers = parsed if isinstance(parsed, dict) else {}
        except Exception:
            answers = {}
    if not isinstance(answers, dict):
        answers = {}

    conn = get_conn(); cur = conn.cursor()
    try:
        # Start an immediate transaction to acquire a RESERVED lock (prevents writers racing).
        # This makes the following SELECT+INSERT atomic relative to other writers.
        cur.execute("BEGIN IMMEDIATE")

        # duplicate check inside transaction (same logic as has_exact_duplicate)
        if phone:
            cur.execute("SELECT id FROM results WHERE exam_id = ? AND phone = ? LIMIT 1", (exam_id, phone))
            if cur.fetchone():
                conn.rollback()
                conn.close()
                return None
        else:
            cur.execute("SELECT id FROM results WHERE exam_id = ? AND student_name = ? AND province = ? LIMIT 1", (exam_id, student_name, province))
            if cur.fetchone():
                conn.rollback()
                conn.close()
                return None

        # calculate score
        questions = get_exam_questions(exam_id)
        if not questions:
            score_percent = 0.0
            topic_percent = {}
            details_list = {}
        else:
            total = len(questions)
            correct_count = 0
            wrong_count = 0
            topic_totals = {}
            topic_correct = {}
            details_list = {}
            for q in questions:
                qid = str(q['id'])
                your = (answers.get(qid) or "").strip().upper()
                correct = (q.get('correct') or "").strip().upper()
                is_correct = (your != "" and your == correct)
                if is_correct:
                    correct_count += 1
                elif your:
                    wrong_count += 1
                t = q.get('topic') or "نامشخص"
                topic_totals[t] = topic_totals.get(t, 0) + 1
                if is_correct:
                    topic_correct[t] = topic_correct.get(t, 0) + 1
                details_list[qid] = {
                    "text": q.get('text'),
                    "your_answer": your,
                    "correct": correct,
                    "is_correct": is_correct
                }

            # penalty
            penalty = wrong_count // 3
            adjusted_correct = max(correct_count - penalty, 0)
            score_percent = round(100.0 * adjusted_correct / total, 2) if total > 0 else 0.0
            topic_percent = {}
            for t in topic_totals:
                topic_percent[t] = round(100.0 * (topic_correct.get(t, 0)) / topic_totals[t], 2)

        # tazr mapping 1000..13500
        if tazr is None:
            tazr_value = round(1000 + (score_percent / 100.0) * (13500 - 1000), 2)
        else:
            tazr_value = float(tazr)

        details_json = json.dumps({"per_topic": topic_percent, "per_question": details_list}, ensure_ascii=False)

        cur.execute("""
            INSERT INTO results (exam_id, student_name, phone, province, score, tazr, rank_national, rank_provincial, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (exam_id, student_name, phone, province, score_percent, tazr_value, 0, 0, details_json, datetime.utcnow().isoformat()))
        rid = cur.lastrowid

        conn.commit()
        conn.close()

        # recalc ranks after commit
        recalc_ranks_for_exam(exam_id)
        return rid
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        raise

def list_provinces():
    return PROVINCES.copy()