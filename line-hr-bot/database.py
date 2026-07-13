"""
database.py - SQLite storage สำหรับ LINE HR Bot
ใช้เก็บข้อมูลข้อความจาก LINE กลุ่ม และข้อมูล HumanSoft
"""
import sqlite3
import os
from datetime import date, datetime
from typing import Optional

DB_PATH = os.path.join(os.getenv("DATA_DIR", "."), "hr_bot.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """สร้างตารางทั้งหมดครั้งแรก"""
    conn = get_conn()
    cur = conn.cursor()

    # ตารางเก็บข้อความจาก LINE
    cur.execute("""
        CREATE TABLE IF NOT EXISTS line_messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_date    TEXT NOT NULL,          -- YYYY-MM-DD
            sender_id   TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            msg_type    TEXT NOT NULL,          -- 'text' | 'image' | 'file'
            content     TEXT,                   -- ข้อความหรือ path ของไฟล์
            category    TEXT,                   -- 'work_plan' | 'leave' | 'other' (AI จัดหมวด)
            summary     TEXT,                   -- สรุปจาก Claude
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # ตารางเก็บข้อมูล HumanSoft attendance
    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            att_date        TEXT NOT NULL,      -- YYYY-MM-DD
            employee_id     TEXT NOT NULL,
            employee_name   TEXT NOT NULL,
            department      TEXT,
            check_in        TEXT,               -- HH:MM
            check_out       TEXT,               -- HH:MM
            status          TEXT,               -- 'present' | 'absent' | 'late' | 'leave'
            leave_type      TEXT,               -- ประเภทการลา (ถ้ามี)
            remark          TEXT,
            created_at      TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # ตารางเก็บ log การส่ง report
    cur.execute("""
        CREATE TABLE IF NOT EXISTS report_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL,
            image_path  TEXT,
            sent_at     TEXT DEFAULT (datetime('now','localtime')),
            status      TEXT DEFAULT 'sent'
        )
    """)

    conn.commit()
    conn.close()
    print("[DB] init_db() สำเร็จ")


# ─── LINE Messages ───────────────────────────────────────────────────────────

def save_message(msg_date: str, sender_id: str, sender_name: str,
                 msg_type: str, content: str, category: str = "other",
                 summary: str = "") -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO line_messages (msg_date, sender_id, sender_name, msg_type, content, category, summary)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (msg_date, sender_id, sender_name, msg_type, content, category, summary))
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def update_message_category(msg_id: int, category: str, summary: str):
    conn = get_conn()
    conn.execute(
        "UPDATE line_messages SET category=?, summary=? WHERE id=?",
        (category, summary, msg_id)
    )
    conn.commit()
    conn.close()


def get_messages_by_date(target_date: str) -> list[dict]:
    """ดึงข้อความทั้งหมดของวันที่กำหนด"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM line_messages WHERE msg_date=? ORDER BY created_at",
        (target_date,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Attendance (HumanSoft) ───────────────────────────────────────────────────

def upsert_attendance(att_date: str, employee_id: str, employee_name: str,
                      department: str = "", check_in: str = "", check_out: str = "",
                      status: str = "present", leave_type: str = "", remark: str = ""):
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO attendance
            (att_date, employee_id, employee_name, department, check_in, check_out, status, leave_type, remark)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (att_date, employee_id, employee_name, department, check_in, check_out,
          status, leave_type, remark))
    conn.commit()
    conn.close()


def get_attendance_by_date(target_date: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM attendance WHERE att_date=? ORDER BY department, employee_name",
        (target_date,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Report Log ──────────────────────────────────────────────────────────────

def log_report(report_date: str, image_path: str, status: str = "sent"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO report_log (report_date, image_path, status) VALUES (?, ?, ?)",
        (report_date, image_path, status)
    )
    conn.commit()
    conn.close()
