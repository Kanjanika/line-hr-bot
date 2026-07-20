"""
processors/humansoft_api.py
ดึงข้อมูลการเข้างานจาก HumanSoft Open API แล้วบันทึกลง SQLite DB
"""
import os
import logging
import httpx
from database import upsert_attendance

logger = logging.getLogger(__name__)

HS_BASE    = "https://openapi.humansoft.co.th"
HS_KEY     = os.getenv("HUMANSOFT_API_KEY", "")
HS_EMP     = os.getenv("HUMANSOFT_EMP_CODE", "")
WORK_START = "08:30"

LEAVE_KEYWORDS = ("ลาป่วย", "ลาพักร้อน", "ลากิจ", "ลาไม่รับเงิน", "ลา", "หยุด")


def _extract_checkin(time_list: list) -> str:
    """ดึงเวลา check-in แรกสุดจาก time[]"""
    checkins = [
        t["time_attendance_dt"]
        for t in time_list
        if t.get("time_attendance_type_lv") in (
            "Checkin", "Fingerprint", "Facial", "QR",
            "LINE-Checkin", "TimeApp", "Beacon", "Wifi",
        )
    ]
    # ถ้าไม่มี type ที่ match ให้ใช้ทุก record
    if not checkins:
        all_times = [t.get("time_attendance_dt", "") for t in time_list if t.get("time_attendance_dt")]
        if all_times:
            return sorted(all_times)[0][11:16]
        return ""
    return sorted(checkins)[0][11:16]  # "HH:MM"


def _extract_checkout(time_list: list) -> str:
    """ดึงเวลา check-out ล่าสุดจาก time[]"""
    all_times = [t.get("time_attendance_dt", "") for t in time_list if t.get("time_attendance_dt")]
    if len(all_times) >= 2:
        return sorted(all_times)[-1][11:16]
    return ""


def _map_status(emp: dict, check_in: str) -> tuple[str, str]:
    """คืน (status, leave_type)"""
    day_status = (emp.get("day_status") or "").strip()

    # ตรวจสอบสถานะการลา
    for kw in LEAVE_KEYWORDS:
        if kw in day_status:
            return "leave", day_status

    # ไม่มีเวลาเลย = ขาดงาน
    if not emp.get("time"):
        return "absent", ""

    # มีเวลา check-in → ตรวจมาสาย
    if check_in and check_in > WORK_START:
        return "late", ""

    return "present", ""


async def fetch_attendance(target_date: str) -> int:
    """
    ดึงข้อมูลเข้างานจาก HumanSoft API แล้วบันทึกลง DB
    คืนจำนวน records ที่บันทึก
    """
    if not HS_KEY:
        raise ValueError("ไม่พบ HUMANSOFT_API_KEY ใน environment variables")
    if not HS_EMP:
        raise ValueError("ไม่พบ HUMANSOFT_EMP_CODE ใน environment variables")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{HS_BASE}/api/v1/open-apis/salary/get-data-filter",
            headers={"Ocp-Apim-Subscription-Key": HS_KEY},
            params={
                "path_action":   "list_employee_inout_daily",
                "work_date":     target_date,
                "employee_code": HS_EMP,
                "language_code": "TH",
            },
        )
        if resp.status_code != 200:
            # log response body เพื่อ debug
            raise ValueError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()

    # HumanSoft อาจส่งมาเป็น list โดยตรง หรือ wrapped ใน key ต่างๆ
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("payload", data.get("data", data.get("result", [])))
    else:
        items = []

    logger.info(f"[HS API] {target_date}: ดึงข้อมูล {len(items)} คน")

    count = 0
    for emp in items:
        first     = (emp.get("employee_name") or "").strip()
        last      = (emp.get("employee_last_name") or "").strip()
        full_name = f"{first} {last}".strip()
        if not full_name:
            continue

        time_list = emp.get("time", []) or []
        check_in  = _extract_checkin(time_list)
        check_out = _extract_checkout(time_list)
        status, leave_type = _map_status(emp, check_in)

        upsert_attendance(
            att_date=target_date,
            employee_id=emp.get("employee_code", full_name),
            employee_name=full_name,
            department=emp.get("department_id", ""),
            check_in=check_in,
            check_out=check_out,
            status=status,
            leave_type=leave_type,
            remark=(emp.get("day_status") or ""),
        )
        count += 1

    logger.info(f"[HS API] บันทึก {count} records ลง DB สำหรับ {target_date}")
    return count
