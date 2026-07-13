"""
humansoft_processor.py
Parse ไฟล์ Excel ที่ Export จาก HumanSoft แล้วบันทึก attendance เข้า SQLite

รองรับ format:
  Row 1  : Title ("ข้อมูลการลงเวลาทำงานตั้งแต่วันที่ ...")
  Row 2  : Headers (ลำดับ, รหัสพนักงาน, รหัสลายนิ้วมือ, ชื่อ-นามสกุล, ตำแหน่ง, ของวันที่, IN, OUT, ...)
  Row 3+ : แผนก: XXX (separator) หรือ data row

สำหรับ CSV รองรับแบบ standard (row 1 = headers)
"""
import re
import logging
import openpyxl
import pandas as pd
from database import upsert_attendance

logger = logging.getLogger(__name__)

MAPS_RE = re.compile(r'maps\.google\.com|goo\.gl/maps|google\.com/maps', re.I)

STATUS_MAP = {
    "มา": "present", "ปกติ": "present", "present": "present", "p": "present",
    "ขาด": "absent", "absent": "absent", "a": "absent",
    "สาย": "late", "late": "late", "l": "late",
    "ลา": "leave", "leave": "leave", "lv": "leave",
    "wfh": "present",
}

# Mapping สำหรับ CSV (ชื่อ column → ชื่อมาตรฐาน)
COL_MAP = {
    "รหัสพนักงาน": "employee_id", "empcode": "employee_id", "รหัส": "employee_id",
    "ชื่อ-นามสกุล": "employee_name", "ชื่อ": "employee_name", "empname": "employee_name",
    "แผนก": "department", "dept": "department",
    "ของวันที่": "att_date", "วันที่": "att_date", "date": "att_date",
    "in": "check_in", "timein": "check_in", "เวลาเข้า": "check_in",
    "out": "check_out", "timeout": "check_out", "เวลาออก": "check_out",
    "สถานะ": "status", "status": "status",
    "หมายเหตุ": "remark", "remark": "remark",
}


def _normalize_time(val) -> str:
    if val is None or str(val).strip() in ("", "nan", "None", "none"):
        return ""
    s = str(val).strip()
    if len(s) >= 5 and s[2] == ":":
        return s[:5]
    return s


def _normalize_date(val, override: str) -> str:
    if val is None or str(val).strip() in ("", "nan", "None"):
        return override
    try:
        return pd.to_datetime(str(val)).strftime("%Y-%m-%d")
    except Exception:
        return override


def _normalize_status(raw: str) -> str:
    if not raw:
        return "present"
    return STATUS_MAP.get(str(raw).strip().lower(), str(raw).strip())


def _cell_str(val) -> str:
    """แปลง cell value → string ตัด None / nan ออก"""
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() in ("nan", "none") else s


def parse_humansoft_file(file_path: str, default_date: str) -> int:
    """
    อ่านไฟล์ Excel/CSV แล้วบันทึกเข้า DB
    Return: จำนวน row ที่บันทึกสำเร็จ
    """
    lower = file_path.lower()
    if lower.endswith(".csv"):
        return _parse_csv(file_path, default_date)
    else:
        return _parse_excel(file_path, default_date)


# ─── Excel parser ──────────────────────────────────────────────────────────────

def _parse_excel(file_path: str, default_date: str) -> int:
    """
    อ่าน HumanSoft Excel format:
      - ค้นหา header row จาก 'ชื่อ-นามสกุล' หรือ 'ชื่อ'
      - rows ที่ขึ้นต้นด้วย 'แผนก:' คือ department separator
      - HYPERLINK formula ใน column หมายเหตุ → Google Maps → ประชุม/WFH
    """
    wb = openpyxl.load_workbook(file_path, data_only=False)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=False))

    # ── หา header row ──────────────────────────────────────────────────────────
    header_row_idx = None
    for i, row in enumerate(all_rows):
        for cell in row:
            v = _cell_str(cell.value)
            if "ชื่อ" in v and ("สกุล" in v or i < 5):
                header_row_idx = i
                break
        if header_row_idx is not None:
            break

    if header_row_idx is None:
        logger.error("[HumanSoft] ไม่พบ header row ใน Excel")
        return 0

    # ── map column index จาก headers ──────────────────────────────────────────
    headers = [_cell_str(c.value) for c in all_rows[header_row_idx]]
    logger.info(f"[HumanSoft] headers: {headers}")

    name_idx = date_idx = id_idx = in_idx = out_idx = remark_idx = status_idx = None
    for i, h in enumerate(headers):
        hl = h.lower()
        if "ชื่อ" in h and name_idx is None:
            name_idx = i
        if ("ของวันที่" in h or h == "วันที่" or hl == "date") and date_idx is None:
            date_idx = i
        if ("รหัสพนักงาน" in h or hl in ("empcode", "emp_code", "รหัส")) and id_idx is None:
            id_idx = i
        if h == "IN" and in_idx is None:
            in_idx = i
        if h == "OUT" and out_idx is None:
            out_idx = i
        if ("หมายเหตุ" in h or hl in ("remark", "note")) and remark_idx is None:
            remark_idx = i
        if ("สถานะ" in h or hl == "status") and status_idx is None:
            status_idx = i

    logger.info(
        f"[HumanSoft] col idx → name={name_idx} date={date_idx} "
        f"id={id_idx} in={in_idx} out={out_idx} remark={remark_idx} status={status_idx}"
    )

    if name_idx is None:
        logger.error("[HumanSoft] ไม่พบ column ชื่อ-นามสกุล")
        return 0

    # ── วนอ่าน data rows ───────────────────────────────────────────────────────
    current_dept = ""
    count = 0

    for row in all_rows[header_row_idx + 1:]:
        first = _cell_str(row[0].value)

        # department separator row
        if first.startswith("แผนก:"):
            current_dept = first[len("แผนก:"):].strip()
            continue

        emp_name = _cell_str(row[name_idx].value)
        if not emp_name or emp_name == "ชื่อ-นามสกุล":
            continue

        emp_id   = _cell_str(row[id_idx].value) if id_idx is not None else ""
        date_raw = _cell_str(row[date_idx].value) if date_idx is not None else ""
        in_raw   = _cell_str(row[in_idx].value) if in_idx is not None else ""
        out_raw  = _cell_str(row[out_idx].value) if out_idx is not None else ""

        # remark จาก column หมายเหตุ
        remark_raw = ""
        if remark_idx is not None:
            cell = row[remark_idx]
            remark_raw = _cell_str(cell.value) if cell.value else ""
            if remark_raw.upper().startswith("=HYPERLINK"):
                m = re.search(r'"(https?://[^"]+)"', remark_raw)
                remark_raw = m.group(1) if m else remark_raw

        # scan ทุก cell ใน row หา Google Maps URL (อาจอยู่ใน สถานที่ หรือ column อื่น)
        maps_url = ""
        for cell in row:
            cv = _cell_str(cell.value) if cell.value else ""
            if cv.upper().startswith("=HYPERLINK"):
                m = re.search(r'"(https?://[^"]+)"', cv)
                cv = m.group(1) if m else cv
            if MAPS_RE.search(cv):
                maps_url = cv
                break
        if maps_url and not remark_raw:
            remark_raw = maps_url

        # อ่าน status จาก column สถานะ ถ้ามี (เช่น L=ลา, A=ขาด, P=ปกติ)
        excel_status = ""
        if status_idx is not None:
            excel_status = _cell_str(row[status_idx].value).lower()

        # status logic: Excel สถานะ → ลา / ขาด / สาย / มาทำงาน
        leave_keywords = {"l", "lv", "ลา", "leave", "ป่วย", "กิจ", "พักร้อน", "sick", "วันลา"}
        absent_keywords = {"a", "absent", "ขาด"}
        late_keywords = {"late", "สาย"}

        if any(kw in excel_status for kw in leave_keywords):
            status = "leave"
        elif any(kw in excel_status for kw in absent_keywords):
            status = "absent"
        elif any(kw in excel_status for kw in late_keywords):
            status = "late"
        elif in_raw and maps_url:
            status = "meeting"
        elif in_raw:
            status = "present"
        else:
            status = "absent"

        upsert_attendance(
            att_date=_normalize_date(date_raw or None, default_date),
            employee_id=emp_id or emp_name,
            employee_name=emp_name,
            department=current_dept,
            check_in=_normalize_time(in_raw),
            check_out=_normalize_time(out_raw),
            status=status,
            leave_type="",
            remark=remark_raw,
        )
        count += 1

    logger.info(f"[HumanSoft] บันทึก {count} รายการ (Excel)")
    return count


# ─── CSV parser ────────────────────────────────────────────────────────────────

def _parse_csv(file_path: str, default_date: str) -> int:
    df = pd.read_csv(file_path, dtype=str, encoding="utf-8-sig")
    df.columns = [str(c).strip() for c in df.columns]

    rename = {}
    for col in df.columns:
        key = col.lower().replace(" ", "").replace("-", "").replace("_", "")
        if key in COL_MAP:
            rename[col] = COL_MAP[key]
        elif col.lower() in COL_MAP:
            rename[col] = COL_MAP[col.lower()]
    df.rename(columns=rename, inplace=True)

    count = 0
    for _, row in df.iterrows():
        emp_name = _cell_str(row.get("employee_name", ""))
        if not emp_name:
            continue
        upsert_attendance(
            att_date=_normalize_date(row.get("att_date"), default_date),
            employee_id=_cell_str(row.get("employee_id", "")) or emp_name,
            employee_name=emp_name,
            department=_cell_str(row.get("department", "")),
            check_in=_normalize_time(row.get("check_in", "")),
            check_out=_normalize_time(row.get("check_out", "")),
            status=_normalize_status(row.get("status", "")),
            leave_type=_cell_str(row.get("leave_type", "")),
            remark=_cell_str(row.get("remark", "")),
        )
        count += 1

    logger.info(f"[HumanSoft] บันทึก {count} รายการ (CSV)")
    return count
