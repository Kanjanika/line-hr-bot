"""
humansoft_processor.py
Parse ไฟล์ Excel / CSV ที่ Export จาก HumanSoft
แล้วบันทึกข้อมูล attendance เข้า SQLite

รองรับ 2 รูปแบบ column ทั่วไปของ HumanSoft:
  แบบ A: รหัส, ชื่อ, แผนก, วันที่, เวลาเข้า, เวลาออก, สถานะ, หมายเหตุ
  แบบ B: EmpCode, EmpName, Dept, Date, TimeIn, TimeOut, Status, Remark
"""
import logging
import pandas as pd
from database import upsert_attendance

logger = logging.getLogger(__name__)

# Mapping ชื่อ column หลายรูปแบบ → ชื่อมาตรฐาน
COL_MAP = {
    # รหัสพนักงาน
    "รหัส": "employee_id", "รหัสพนักงาน": "employee_id",
    "empcode": "employee_id", "emp_code": "employee_id", "id": "employee_id",
    # ชื่อ
    "ชื่อ": "employee_name", "ชื่อ-นามสกุล": "employee_name",
    "ชื่อพนักงาน": "employee_name", "empname": "employee_name",
    "emp_name": "employee_name", "name": "employee_name",
    # แผนก
    "แผนก": "department", "dept": "department", "department": "department",
    # วันที่
    "วันที่": "att_date", "date": "att_date",
    # เวลาเข้า
    "เวลาเข้า": "check_in", "เข้างาน": "check_in",
    "timein": "check_in", "time_in": "check_in", "checkin": "check_in",
    # เวลาออก
    "เวลาออก": "check_out", "ออกงาน": "check_out",
    "timeout": "check_out", "time_out": "check_out", "checkout": "check_out",
    # สถานะ
    "สถานะ": "status", "status": "status",
    # ประเภทการลา
    "ประเภทการลา": "leave_type", "leavetype": "leave_type",
    "leave_type": "leave_type", "ลา": "leave_type",
    # หมายเหตุ
    "หมายเหตุ": "remark", "remark": "remark", "note": "remark",
}

# Mapping สถานะ HumanSoft → มาตรฐาน
STATUS_MAP = {
    "มา": "present", "ปกติ": "present", "present": "present", "p": "present",
    "ขาด": "absent", "absent": "absent", "a": "absent",
    "สาย": "late", "late": "late", "l": "late",
    "ลา": "leave", "leave": "leave", "lv": "leave",
    "wfh": "present",
}


def _normalize_status(raw: str) -> str:
    if not raw:
        return "present"
    return STATUS_MAP.get(str(raw).strip().lower(), str(raw).strip())


def _normalize_time(raw) -> str:
    if pd.isna(raw) or raw == "" or raw is None:
        return ""
    s = str(raw).strip()
    # ตัด seconds ออก ถ้า HH:MM:SS
    if len(s) >= 5 and s[2] == ":":
        return s[:5]
    return s


def _normalize_date(raw, override_date: str) -> str:
    """แปลง date column → YYYY-MM-DD ถ้าแปลงไม่ได้ใช้ override_date"""
    if pd.isna(raw) or raw == "" or raw is None:
        return override_date
    try:
        return pd.to_datetime(str(raw)).strftime("%Y-%m-%d")
    except Exception:
        return override_date


def parse_humansoft_file(file_path: str, default_date: str) -> int:
    """
    อ่านไฟล์ Excel/CSV แล้วบันทึกเข้า DB
    Return: จำนวน row ที่บันทึกสำเร็จ
    """
    lower = file_path.lower()
    if lower.endswith(".csv"):
        df = pd.read_csv(file_path, dtype=str, encoding="utf-8-sig")
    else:
        df = pd.read_excel(file_path, dtype=str)

    # Normalize column names
    df.columns = [str(c).strip() for c in df.columns]
    rename = {}
    for col in df.columns:
        key = col.lower().replace(" ", "").replace("-", "").replace("_", "")
        if key in COL_MAP:
            rename[col] = COL_MAP[key]
        elif col.lower() in COL_MAP:
            rename[col] = COL_MAP[col.lower()]
    df.rename(columns=rename, inplace=True)

    logger.info(f"[HumanSoft] columns after map: {list(df.columns)}")

    count = 0
    for _, row in df.iterrows():
        emp_id = str(row.get("employee_id", "")).strip()
        emp_name = str(row.get("employee_name", "")).strip()
        if not emp_name or emp_name.lower() in ("nan", "none", ""):
            continue

        upsert_attendance(
            att_date=_normalize_date(row.get("att_date"), default_date),
            employee_id=emp_id or emp_name,
            employee_name=emp_name,
            department=str(row.get("department", "")).strip(),
            check_in=_normalize_time(row.get("check_in")),
            check_out=_normalize_time(row.get("check_out")),
            status=_normalize_status(row.get("status", "")),
            leave_type=str(row.get("leave_type", "")).strip(),
            remark=str(row.get("remark", "")).strip(),
        )
        count += 1

    logger.info(f"[HumanSoft] บันทึก {count} รายการ")
    return count
