"""
staff_mapper.py
เชื่อมชื่อ LINE user_id → ชื่อพนักงานใน HumanSoft

วิธีใช้:
  1. แก้ไขไฟล์ data/staff_mapping.csv
  2. กรอก line_user_id (ดูจาก Railway logs), line_name, employee_name, department
  3. push ขึ้น GitHub → Railway redeploy อัตโนมัติ

คำสั่ง /reload-staff ใน LINE กลุ่มจะโหลดไฟล์ใหม่โดยไม่ต้อง restart
"""
import csv
import os
import logging

logger = logging.getLogger(__name__)

MAPPING_FILE = os.getenv("STAFF_MAPPING_FILE", "data/staff_mapping.csv")

# cache: line_user_id → {"employee_name": str, "department": str}
_mapping: dict[str, dict] = {}
_loaded = False


def _load():
    global _mapping, _loaded
    if _loaded:
        return
    if not os.path.exists(MAPPING_FILE):
        logger.warning(f"[STAFF MAPPER] ไม่พบไฟล์ {MAPPING_FILE} — ใช้ชื่อ LINE แทน")
        _loaded = True
        return
    try:
        new_map: dict[str, dict] = {}
        with open(MAPPING_FILE, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                uid = (row.get("line_user_id") or "").strip()
                name = (row.get("employee_name") or "").strip()
                dept = (row.get("department") or "").strip()
                if uid and name and not uid.startswith("U000000000"):
                    new_map[uid] = {"employee_name": name, "department": dept}
        _mapping = new_map
        logger.info(f"[STAFF MAPPER] โหลดสำเร็จ {len(_mapping)} คน จาก {MAPPING_FILE}")
    except Exception as e:
        logger.error(f"[STAFF MAPPER] โหลดล้มเหลว: {e}")
    _loaded = True


def reload():
    """โหลด staff_mapping.csv ใหม่ (ใช้เมื่อแก้ไขไฟล์แล้ว)"""
    global _loaded
    _loaded = False
    _load()
    return len(_mapping)


def get_employee_name(line_user_id: str, fallback: str = "") -> str:
    """
    แปลง LINE user_id → ชื่อพนักงานใน HumanSoft
    ถ้าไม่พบใน mapping ให้ใช้ fallback (ชื่อ LINE) แทน
    """
    _load()
    entry = _mapping.get(line_user_id)
    if entry:
        return entry["employee_name"]
    return fallback or line_user_id


def get_all_mapped() -> list[dict]:
    """คืน list ของพนักงานทั้งหมดที่ mapping ไว้"""
    _load()
    return [
        {"line_user_id": uid, **info}
        for uid, info in _mapping.items()
    ]
