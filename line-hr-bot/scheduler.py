"""
scheduler.py
APScheduler jobs อัตโนมัติ 2 งาน:
  1. 10:30 น. — ตรวจว่าใครยังไม่ส่งแผนงาน → แจ้ง LINE + บันทึก Excel
  2. 18:00 น. — ส่ง Daily Report รูปภาพเข้า LINE กลุ่ม
"""
import asyncio
import logging
import os
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import REPORT_TIME, WORKPLAN_DEADLINE, TIMEZONE, LINE_GROUP_ID, WORKPLAN_SAVE_PATH

logger = logging.getLogger(__name__)
_scheduler = BackgroundScheduler(timezone=TIMEZONE)


# ──────────────────────────────────────────────────────────────────────────────
# JOB 1 : ตรวจ 10:30 น. — ใครยังไม่ส่งแผนงาน
# ──────────────────────────────────────────────────────────────────────────────

def _run_workplan_check():
    today = date.today().isoformat()
    logger.info(f"[SCHEDULER] ตรวจแผนงาน {today} 10:30 น.")
    try:
        asyncio.run(_check_and_notify(today))
    except Exception as e:
        logger.error(f"[SCHEDULER workplan_check] {e}")


async def _check_and_notify(today: str):
    """10:30 น. — บันทึก Excel เท่านั้น ไม่ส่งแจ้งใน LINE"""
    from database import get_messages_by_date, get_attendance_by_date

    messages   = get_messages_by_date(today)
    attendance = get_attendance_by_date(today)

    submitted = {
        m["sender_name"]
        for m in messages
        if m["category"] == "work_plan"
    }
    all_staff = [
        a["employee_name"]
        for a in attendance
        if a["status"] in ("present", "late")
    ]
    not_submitted = [name for name in all_staff if name not in submitted]

    # บันทึก Excel ลง WORKPLAN_SAVE_PATH — ไม่ส่งอะไรเข้า LINE
    await _save_workplan_excel(today, submitted, not_submitted, all_staff)
    logger.info(f"[WORKPLAN CHECK] ส่งแล้ว={len(submitted)}, ยังไม่ส่ง={len(not_submitted)} (บันทึก Excel เท่านั้น)")


async def _save_workplan_excel(today: str, submitted: set, not_submitted: list, all_staff: list):
    """บันทึกสถานะแผนงานเป็น Excel ลง WORKPLAN_SAVE_PATH"""
    import pandas as pd
    from datetime import datetime

    try:
        os.makedirs(WORKPLAN_SAVE_PATH, exist_ok=True)

        # หมายเลข Revision (ถ้าไฟล์วันนี้มีแล้ว ให้เพิ่ม Rev)
        date_prefix = today.replace("-", "")
        existing = [
            f for f in os.listdir(WORKPLAN_SAVE_PATH)
            if f.startswith(date_prefix) and f.endswith(".xlsx")
        ]
        rev = len(existing) + 1
        filename = f"{date_prefix}_WorkPlanStatus_Rev.{rev}.xlsx"
        filepath = os.path.join(WORKPLAN_SAVE_PATH, filename)

        # สร้าง DataFrame
        rows = []
        for name in sorted(all_staff):
            rows.append({
                "ลำดับ": len(rows) + 1,
                "ชื่อพนักงาน": name,
                "สถานะ": "✅ ส่งแล้ว" if name in submitted else "❌ ยังไม่ส่ง",
                "เวลาตรวจ": datetime.now().strftime("%H:%M น."),
                "วันที่": today,
            })
        # พนักงานที่ส่งแต่ไม่อยู่ใน HumanSoft
        extra = sorted(submitted - set(all_staff))
        for name in extra:
            rows.append({
                "ลำดับ": len(rows) + 1,
                "ชื่อพนักงาน": name,
                "สถานะ": "✅ ส่งแล้ว (ไม่มีใน HumanSoft)",
                "เวลาตรวจ": datetime.now().strftime("%H:%M น."),
                "วันที่": today,
            })

        if not rows:
            rows.append({
                "ลำดับ": "-",
                "ชื่อพนักงาน": "ยังไม่มีข้อมูลวันนี้",
                "สถานะ": "-",
                "เวลาตรวจ": datetime.now().strftime("%H:%M น."),
                "วันที่": today,
            })

        df = pd.DataFrame(rows)

        with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="สถานะแผนงาน")
            ws = writer.sheets["สถานะแผนงาน"]

            # ปรับความกว้าง column
            for col in ws.columns:
                max_len = max(len(str(cell.value or "")) for cell in col) + 4
                ws.column_dimensions[col[0].column_letter].width = min(max_len, 40)

            # สีแถว header
            from openpyxl.styles import PatternFill, Font, Alignment
            header_fill = PatternFill("solid", fgColor="1E3C72")
            for cell in ws[1]:
                cell.fill = header_fill
                cell.font = Font(color="FFFFFF", bold=True)
                cell.alignment = Alignment(horizontal="center")

        logger.info(f"[WORKPLAN EXCEL] บันทึก: {filepath}")

    except Exception as e:
        logger.error(f"[WORKPLAN EXCEL] save error: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# JOB 2 : Daily Report 18:00 น.
# ──────────────────────────────────────────────────────────────────────────────

def _run_daily_report():
    from generators.report_generator import generate_and_send_report
    today = date.today().isoformat()
    logger.info(f"[SCHEDULER] เริ่มสร้าง report วันที่ {today}")
    try:
        asyncio.run(generate_and_send_report(LINE_GROUP_ID, today))
    except Exception as e:
        logger.error(f"[SCHEDULER daily_report] {e}")


# ──────────────────────────────────────────────────────────────────────────────

def start_scheduler():
    # Job 1 — 10:30 น. ตรวจแผนงาน
    wp_hour, wp_min = WORKPLAN_DEADLINE.split(":")
    _scheduler.add_job(
        _run_workplan_check,
        CronTrigger(hour=int(wp_hour), minute=int(wp_min), timezone=TIMEZONE),
        id="workplan_check",
        replace_existing=True,
    )

    # Job 2 — 18:00 น. (หรือเวลาที่กำหนด) daily report
    rp_hour, rp_min = REPORT_TIME.split(":")
    _scheduler.add_job(
        _run_daily_report,
        CronTrigger(hour=int(rp_hour), minute=int(rp_min), timezone=TIMEZONE),
        id="daily_report",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        f"[SCHEDULER] ✅ ตั้งเวลา:\n"
        f"  • ตรวจแผนงาน  : ทุกวัน {WORKPLAN_DEADLINE} น.\n"
        f"  • Daily Report : ทุกวัน {REPORT_TIME} น."
    )
