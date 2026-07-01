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
      "checked_at": "10:30 น.",
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

    from datetime import datetime
    return {
        "date":          target_date,
        "checked_at":    datetime.now().strftime("%H:%M น."),
        "submitted":     submitted,
        "not_submitted": not_submitted,
        "all_staff":     all_staff,
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
