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

# cache หลัก: line_user_id → {"employee_name": str, "department": str}
_mapping: dict[str, dict] = {}
# cache รอง: employee_name (lower) → {"room": str, "department": str, "order": int}
_name_map: dict[str, dict] = {}
_loaded = False


def _load():
    global _mapping, _name_map, _loaded
    if _loaded:
        return
    if not os.path.exists(MAPPING_FILE):
        logger.warning(f"[STAFF MAPPER] ไม่พบไฟล์ {MAPPING_FILE} — ใช้ชื่อ LINE แทน")
        _loaded = True
        return
    try:
        new_map: dict[str, dict] = {}
        new_name_map: dict[str, dict] = {}
        with open(MAPPING_FILE, newline="", encoding="utf-8-sig") as f:
            for order_idx, row in enumerate(csv.DictReader(f)):
                uid  = (row.get("line_user_id") or "").strip()
                name = (row.get("employee_name") or "").strip()
                dept = (row.get("department") or "").strip()
                room = (row.get("Room") or row.get("room") or "").strip()
                if name:
                    nickname = (row.get("nickname") or row.get("ชื่อเล่น") or "").strip()
                    new_name_map[name.lower()] = {
                        "room": room, "department": dept, "order": order_idx,
                        "display_name": name,
                        "nickname": nickname,
                    }
                if uid and name and not uid.startswith("U000000000"):
                    new_map[uid] = {"employee_name": name, "department": dept}
        _mapping  = new_map
        _name_map = new_name_map
        logger.info(
            f"[STAFF MAPPER] โหลดสำเร็จ {len(_mapping)} คน (LINE mapped), "
            f"{len(_name_map)} คน (name index) จาก {MAPPING_FILE}"
        )
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


def get_sort_order(employee_name: str) -> int:
    """คืน row index ใน staff_mapping.csv เพื่อใช้เรียงลำดับใน report"""
    _load()
    entry = _name_map.get(employee_name.strip().lower())
    return entry.get("order", 9999) if entry else 9999


def get_nickname_by_name(employee_name: str) -> str:
    """
    แปลงชื่อพนักงาน → ชื่อเล่น
    ถ้าไม่มีใน CSV ใช้ชื่อแรก (first word) เป็น fallback
    """
    _load()
    entry = _name_map.get(employee_name.strip().lower())
    if entry:
        nick = entry.get("nickname", "")
        if nick:
            return nick
    # fallback: ชื่อแรก (กรณียังไม่ได้กรอก nickname ใน CSV)
    parts = employee_name.strip().split()
    return parts[0] if parts else ""


def get_all_staff_names() -> list[tuple[str, str, str]]:
    """
    คืน [(employee_name, room, department)] พนักงานทุกคนตามลำดับ staff_mapping.csv
    ใช้สำหรับเติมคนที่ไม่อยู่ใน Excel เป็น 'ขาดงาน' ใน report
    """
    _load()
    sorted_entries = sorted(_name_map.values(), key=lambda x: x.get("order", 9999))
    return [
        (e["display_name"], e.get("room", ""), e.get("department", ""))
        for e in sorted_entries
        if e.get("display_name")
    ]


def get_room_by_name(employee_name: str) -> str:
    """
    แปลงชื่อพนักงาน → ห้อง (Room) จาก staff_mapping
    ใช้สำหรับ report_generator เพื่อจัดกลุ่มตามห้อง
    """
    _load()
    entry = _name_map.get(employee_name.strip().lower())
    return entry["room"] if entry else ""


def get_all_mapped() -> list[dict]:
    """คืน list ของพนักงานทั้งหมดที่ mapping ไว้"""
    _load()
    return [
        {"line_user_id": uid, **info}
        for uid, info in _mapping.items()
    ]
