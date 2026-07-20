"""
main.py - FastAPI Webhook Server สำหรับ LINE HR Bot
รับข้อความจาก LINE, ประมวลผล, ส่ง daily report อัตโนมัติ
"""
import os
import logging
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, Request, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import (
    MessageEvent, TextMessageContent, ImageMessageContent, FileMessageContent
)

from config import LINE_CHANNEL_SECRET, IMAGE_DIR, UPLOAD_DIR
from processors.humansoft_processor import parse_humansoft_file
from database import init_db, get_messages_by_date, get_attendance_by_date
from handlers.message_handler import handle_text, handle_image, handle_file
from scheduler import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

parser = WebhookParser(LINE_CHANNEL_SECRET)
logger.info(f"LINE_CHANNEL_SECRET loaded, length={len(LINE_CHANNEL_SECRET)}")

os.makedirs(IMAGE_DIR,  exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    logger.info("LINE HR Bot เริ่มทำงานแล้ว")
    yield
    logger.info("LINE HR Bot หยุดทำงาน")


app = FastAPI(title="LINE HR Bot", lifespan=lifespan)

# Serve รูป report ผ่าน URL สาธารณะ (ใช้ส่ง ImageMessage ใน LINE)
app.mount("/images", StaticFiles(directory=IMAGE_DIR), name="images")


# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "date": date.today().isoformat()}


# ─── Workplan Status API (ใช้โดย local_tasks/save_workplan_status.py) ────────

@app.get("/workplan-status")
async def workplan_status(target_date: str = Query(default=None, alias="date")):
    """
    คืน JSON สถานะแผนงาน — ใช้โดย local script บนเครื่อง HR
    {
      "date": "2026-06-29",
      "checked_at": "09:30 น.",
      "submitted": ["ชื่อ A", "ชื่อ B"],
      "not_submitted": ["ชื่อ C"],
      "all_staff": ["ชื่อ A", "ชื่อ B", "ชื่อ C"]
    }
    """
    if not target_date:
        target_date = date.today().isoformat()

    messages   = get_messages_by_date(target_date)
    attendance = get_attendance_by_date(target_date)

    submitted  = sorted({
        m["sender_name"]
        for m in messages
        if m["category"] == "work_plan"
    })
    all_staff  = sorted({
        a["employee_name"]
        for a in attendance
        if a["status"] in ("present", "late")
    })
    not_submitted = [n for n in all_staff if n not in submitted]

    from datetime import datetime, timezone, timedelta
    _thai = datetime.now(timezone(timedelta(hours=7)))
    return {
        "date":          target_date,
        "checked_at":    _thai.strftime("%H:%M น."),
        "submitted":     submitted,
        "not_submitted": not_submitted,
        "all_staff":     all_staff,
    }


# ─── Fetch Attendance from HumanSoft API ─────────────────────────────────────

@app.get("/fetch-attendance")
async def fetch_attendance_endpoint(target_date: str = Query(default=None, alias="date")):
    """ดึงข้อมูลเข้างานจาก HumanSoft API → บันทึกลง DB ทันที
       ใช้ทดสอบ: /fetch-attendance  หรือ /fetch-attendance?date=2026-07-18
    """
    import traceback
    from processors.humansoft_api import fetch_attendance
    td = target_date or date.today().isoformat()
    try:
        count = await fetch_attendance(td)
        return {"status": "ok", "date": td, "records_saved": count}
    except Exception as e:
        return {"status": "error", "message": str(e), "detail": traceback.format_exc()}


# ─── Debug Raw API (ดูโครงสร้าง JSON ดิบจาก HumanSoft) ─────────────────────────

@app.get("/debug-raw-api")
async def debug_raw_api(target_date: str = Query(default=None, alias="date")):
    """ดูโครงสร้าง JSON ดิบจาก HumanSoft API — ใช้ debug ชื่อ field เวลาเข้างาน
       เปิด: /debug-raw-api  หรือ /debug-raw-api?date=2026-07-20
    """
    import traceback, httpx
    td = target_date or date.today().isoformat()
    hs_key = os.getenv("HUMANSOFT_API_KEY", "")
    hs_emp = os.getenv("HUMANSOFT_EMP_CODE", "")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://openapi.humansoft.co.th/api/v1/open-apis/salary/get-data-filter",
                headers={"Ocp-Apim-Subscription-Key": hs_key},
                params={
                    "path_action":   "list_employee_inout_daily",
                    "work_date":     td,
                    "employee_code": hs_emp,
                    "language_code": "TH",
                },
            )
        data = resp.json()
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("payload", data.get("data", data.get("result", [])))
        else:
            items = []

        # หาพนักงานคนแรกที่มี time data
        sample_with_time = next((e for e in items if e.get("time")), None)

        return {
            "http_status":        resp.status_code,
            "date":               td,
            "total_employees":    len(items),
            "top_level_keys":     list(data.keys()) if isinstance(data, dict) else "list_direct",
            "employee_keys":      list(items[0].keys()) if items else [],
            "sample_with_time":   sample_with_time,
            "sample_no_time":     next((e for e in items if not e.get("time")), None),
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "detail": traceback.format_exc()}


# ─── Clean DB (ลบ duplicate attendance rows) ─────────────────────────────────

@app.get("/clean-db")
async def clean_db_endpoint(target_date: str = Query(default=None, alias="date")):
    """ลบ attendance rows ที่ซ้ำกัน — เก็บแถวล่าสุดต่อพนักงาน 1 แถว
       เปิด: /clean-db?date=2026-07-20
    """
    from database import get_conn
    td = target_date or date.today().isoformat()
    conn = get_conn()
    before = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE att_date=?", (td,)
    ).fetchone()[0]
    conn.execute("""
        DELETE FROM attendance
        WHERE att_date=? AND id NOT IN (
            SELECT MAX(id) FROM attendance WHERE att_date=? GROUP BY employee_id
        )
    """, (td, td))
    conn.commit()
    after = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE att_date=?", (td,)
    ).fetchone()[0]
    conn.close()
    return {
        "date":       td,
        "rows_before": before,
        "rows_after":  after,
        "deleted":     before - after,
    }


# ─── Test Report (ทดสอบสร้างรูป report ทันที) ──────────────────────────────────

@app.get("/test-report")
async def test_report():
    """สร้าง report image จากข้อมูลวันนี้แล้วส่ง LINE ทันที (ทดสอบ)"""
    import traceback
    try:
        from generators.report_generator import generate_and_send_report
        from config import LINE_GROUP_ID
        target_date = date.today().isoformat()
        att_rows = get_attendance_by_date(target_date)
        await generate_and_send_report(LINE_GROUP_ID, target_date)
        return {
            "status": "ok",
            "message": f"Report sent to LINE for {target_date}",
            "attendance_rows": len(att_rows),
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "detail": traceback.format_exc()}


# ─── DB Check (debug: ดูข้อมูลใน DB) ─────────────────────────────────────────

@app.get("/db-check")
async def db_check(target_date: str = Query(default=None, alias="date")):
    """ตรวจสอบข้อมูลใน DB — /db-check?date=2026-07-13"""
    if not target_date:
        target_date = date.today().isoformat()
    attendance = get_attendance_by_date(target_date)
    from collections import Counter
    status_count = Counter(a["status"] for a in attendance)
    return {
        "date": target_date,
        "total_rows": len(attendance),
        "status_breakdown": dict(status_count),
        "sample": attendance[:5],  # แสดง 5 แถวแรก
    }


# ─── Debug Scheduler ──────────────────────────────────────────────────────────

@app.get("/debug-scheduler")
async def debug_scheduler():
    """ตรวจสอบ APScheduler jobs — next fire time และเวลาปัจจุบัน"""
    from scheduler import _scheduler, _now_thai
    from datetime import timezone

    now_utc   = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    now_thai  = _now_thai()

    jobs = []
    for job in _scheduler.get_jobs():
        nft = job.next_run_time
        jobs.append({
            "id":            job.id,
            "next_utc":      nft.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if nft else None,
            "next_thai":     nft.astimezone(__import__("datetime").timezone(__import__("datetime").timedelta(hours=7))).strftime("%Y-%m-%d %H:%M น.") if nft else None,
            "trigger":       str(job.trigger),
        })

    return {
        "scheduler_running": _scheduler.running,
        "server_utc":        now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "thai_time":         now_thai.strftime("%Y-%m-%d %H:%M น."),
        "jobs":              jobs,
    }


# ─── HumanSoft Upload (เว็บอัปโหลดไฟล์โดยตรง ไม่ต้องผ่าน LINE) ───────────────

UPLOAD_TOKEN = os.getenv("UPLOAD_TOKEN", "")  # ตั้งใน Railway Variables เพื่อป้องกัน

@app.get("/upload", response_class=HTMLResponse)
async def upload_page():
    """หน้าเว็บอัปโหลดไฟล์ HumanSoft"""
    token_field = f'<input name="token" placeholder="Upload Token" required style="width:100%;padding:8px;margin-bottom:12px">' if UPLOAD_TOKEN else ""
    return f"""<!DOCTYPE html>
<html lang="th"><head><meta charset="utf-8">
<title>อัปโหลด HumanSoft</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font-family:sans-serif;max-width:480px;margin:60px auto;padding:20px}}
h2{{color:#2d6a4f}}input,select{{width:100%;padding:8px;margin-bottom:12px;box-sizing:border-box}}
button{{background:#2d6a4f;color:#fff;padding:10px 24px;border:none;border-radius:6px;cursor:pointer;font-size:16px}}
button:hover{{background:#1b4332}}.msg{{margin-top:16px;padding:12px;border-radius:6px}}</style>
</head><body>
<h2>📊 อัปโหลดข้อมูล HumanSoft</h2>
<form method="post" enctype="multipart/form-data">
  {token_field}
  <input type="file" name="file" accept=".xlsx,.xls,.csv" required>
  <input name="att_date" type="date" value="{date.today().isoformat()}" required>
  <button type="submit">อัปโหลด</button>
</form>
</body></html>"""

@app.post("/upload", response_class=HTMLResponse)
async def upload_humansoft(
    file: UploadFile = File(...),
    att_date: str = Form(default=None),
    token: str = Form(default=""),
):
    """รับไฟล์ HumanSoft จากหน้าเว็บ"""
    if UPLOAD_TOKEN and token != UPLOAD_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")

    target_date = att_date or date.today().isoformat()
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    save_path = os.path.join(UPLOAD_DIR, f"{target_date}_{file.filename}")

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    try:
        count = parse_humansoft_file(save_path, target_date)
        logger.info(f"[UPLOAD] HumanSoft {file.filename} → {count} rows, date={target_date}")
        return f"""<!DOCTYPE html><html lang="th"><head><meta charset="utf-8">
<title>สำเร็จ</title><style>body{{font-family:sans-serif;max-width:480px;margin:60px auto;padding:20px}}</style></head>
<body><h2 style="color:#2d6a4f">✅ อัปโหลดสำเร็จ</h2>
<p>ไฟล์: <b>{file.filename}</b><br>วันที่: <b>{target_date}</b><br>บันทึก: <b>{count} รายการ</b></p>
<a href="/upload">← อัปโหลดไฟล์อื่น</a></body></html>"""
    except Exception as e:
        logger.error(f"[UPLOAD] parse error: {e}")
        return f"""<!DOCTYPE html><html lang="th"><head><meta charset="utf-8">
<title>ผิดพลาด</title></head>
<body><h2 style="color:#c0392b">❌ เกิดข้อผิดพลาด</h2><p>{e}</p>
<a href="/upload">← ลองใหม่</a></body></html>"""


# ─── LINE Webhook ─────────────────────────────────────────────────────────────

@app.post("/webhook")
async def webhook(request: Request):
    """LINE Webhook endpoint - รับ events ทั้งหมดจาก LINE"""
    signature = request.headers.get("X-Line-Signature", "")
    body      = await request.body()

    try:
        events = parser.parse(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        logger.error(f"Invalid signature! SECRET length={len(LINE_CHANNEL_SECRET)}, sig={signature[:20] if signature else 'EMPTY'}")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error(f"Parse error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    for event in events:
        if not isinstance(event, MessageEvent):
            continue
        if event.source.type != "group":
            continue

        group_id = event.source.group_id
        user_id  = event.source.user_id

        # log group_id ครั้งแรกเพื่อให้ admin copy ไปใส่ env
        logger.info(f"group_id = {group_id}")

        try:
            if isinstance(event.message, TextMessageContent):
                await handle_text(group_id=group_id, user_id=user_id,
                                  text=event.message.text)

            elif isinstance(event.message, ImageMessageContent):
                await handle_image(group_id=group_id, user_id=user_id,
                                   message_id=event.message.id)

            elif isinstance(event.message, FileMessageContent):
                await handle_file(group_id=group_id, user_id=user_id,
                                  message_id=event.message.id,
                                  file_name=event.message.file_name)

        except Exception as e:
            logger.error(f"Handle event error: {e}")

    return JSONResponse(content={"status": "ok"})
