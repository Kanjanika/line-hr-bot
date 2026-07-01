"""
message_handler.py
รับและจัดการข้อความจาก LINE กลุ่ม
  - text  → ตรวจว่าเป็นแผนงาน / ใบลา / คำสั่ง /report
  - image → ดาวน์โหลด + ส่งให้ Claude วิเคราะห์
  - file  → ดาวน์โหลด Excel HumanSoft แล้ว parse
"""
import os
import asyncio
import logging
from datetime import date

import httpx
from linebot.v3.messaging import (
    ApiClient, Configuration, MessagingApi, PushMessageRequest, TextMessage
)

from config import LINE_CHANNEL_ACCESS_TOKEN, LINE_GROUP_ID, UPLOAD_DIR
from database import save_message, update_message_category
from processors.claude_processor import classify_text, analyze_image
from processors.humansoft_processor import parse_humansoft_file

logger = logging.getLogger(__name__)

line_config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)


async def _get_user_display_name(user_id: str, group_id: str = None) -> str:
    """ดึงชื่อสมาชิก LINE จาก user_id (ใช้ group member API สำหรับกลุ่ม)"""
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    # ลอง group member API ก่อน (ไม่ต้อง add bot เป็นเพื่อน)
    if group_id:
        url = f"https://api.line.me/v2/bot/group/{group_id}/member/{user_id}"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.json().get("displayName", user_id)
        except Exception:
            pass
    # fallback: profile API (สำหรับ user ที่ add เป็นเพื่อน)
    url = f"https://api.line.me/v2/bot/profile/{user_id}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json().get("displayName", user_id)
    except Exception as e:
        logger.warning(f"Cannot get display name: {e}")
    return user_id


async def _download_line_content(message_id: str) -> bytes:
    """ดาวน์โหลด binary content จาก LINE API"""
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.content


async def _push_text_to_group(group_id: str, text: str):
    """ส่งข้อความ text กลับเข้า LINE กลุ่ม"""
    with ApiClient(line_config) as api_client:
        api = MessagingApi(api_client)
        api.push_message(
            PushMessageRequest(
                to=group_id,
                messages=[TextMessage(type="text", text=text)]
            )
        )


# ─────────────────────────────────────────────────────────────────────────────

async def handle_text(group_id: str, user_id: str, text: str):
    """จัดการข้อความ text"""
    today = date.today().isoformat()
    sender_name = await _get_user_display_name(user_id, group_id)

    # ─── คำสั่งพิเศษ ───
    stripped = text.strip().lower()
    if stripped in ("/report", "รายงาน", "/สรุป"):
        # สั่ง report ทันที
        from generators.report_generator import generate_and_send_report
        await generate_and_send_report(group_id, today)
        return

    if stripped.startswith("/upload") or stripped.startswith("ส่งไฟล์ humansoft"):
        await _push_text_to_group(
            group_id,
            "📎 กรุณาแนบไฟล์ Excel ข้อมูล HumanSoft มาในกลุ่มนี้ได้เลยครับ"
        )
        return

    # ─── บันทึกและจัดหมวดข้อความปกติ ───
    msg_id = save_message(
        msg_date=today,
        sender_id=user_id,
        sender_name=sender_name,
        msg_type="text",
        content=text,
    )

    # ส่งให้ Claude จัดหมวดหมู่ (non-blocking)
    asyncio.create_task(_classify_and_update(msg_id, sender_name, text))
    logger.info(f"[TEXT] {sender_name}: {text[:60]}...")


async def _classify_and_update(msg_id: int, sender_name: str, text: str):
    try:
        category, summary = await classify_text(sender_name, text)
        update_message_category(msg_id, category, summary)
        logger.info(f"[CLASSIFY] id={msg_id} category={category}")
    except Exception as e:
        logger.error(f"Classify error: {e}")


# ─────────────────────────────────────────────────────────────────────────────

async def handle_image(group_id: str, user_id: str, message_id: str):
    """ดาวน์โหลดรูปภาพและให้ Claude วิเคราะห์"""
    today = date.today().isoformat()
    sender_name = await _get_user_display_name(user_id, group_id)

    # ดาวน์โหลดรูปจาก LINE
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    save_path = os.path.join(UPLOAD_DIR, f"{today}_{message_id}.jpg")

    try:
        content = await _download_line_content(message_id)
        with open(save_path, "wb") as f:
            f.write(content)
    except Exception as e:
        logger.error(f"Download image error: {e}")
        return

    # บันทึกก่อน (category จะอัปเดตทีหลัง)
    msg_id = save_message(
        msg_date=today,
        sender_id=user_id,
        sender_name=sender_name,
        msg_type="image",
        content=save_path,
    )

    # วิเคราะห์รูปด้วย Claude Vision (non-blocking)
    asyncio.create_task(_analyze_image_and_update(msg_id, sender_name, save_path))
    logger.info(f"[IMAGE] {sender_name}: saved to {save_path}")


async def _analyze_image_and_update(msg_id: int, sender_name: str, image_path: str):
    try:
        category, summary = await analyze_image(sender_name, image_path)
        update_message_category(msg_id, category, summary)
        logger.info(f"[IMAGE ANALYZE] id={msg_id} category={category}")
    except Exception as e:
        logger.error(f"Analyze image error: {e}")


# ─────────────────────────────────────────────────────────────────────────────

async def handle_file(group_id: str, user_id: str, message_id: str, file_name: str):
    """รับไฟล์ Excel HumanSoft แล้ว parse เข้า DB"""
    today = date.today().isoformat()
    sender_name = await _get_user_display_name(user_id, group_id)

    # รับเฉพาะไฟล์ .xlsx / .xls / .csv
    lower_name = file_name.lower()
    if not any(lower_name.endswith(ext) for ext in [".xlsx", ".xls", ".csv"]):
        logger.info(f"[FILE] ไม่ใช่ไฟล์ HumanSoft: {file_name}")
        return

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    save_path = os.path.join(UPLOAD_DIR, f"{today}_{file_name}")

    try:
        content = await _download_line_content(message_id)
        with open(save_path, "wb") as f:
            f.write(content)
    except Exception as e:
        logger.error(f"Download file error: {e}")
        return

    # Parse HumanSoft แล้วแจ้งสถานะ
    try:
        count = parse_humansoft_file(save_path, today)
        await _push_text_to_group(
            group_id,
            f"✅ รับข้อมูล HumanSoft แล้ว ({file_name})\n"
            f"📊 บันทึก {count} รายการสำเร็จ\n"
            f"จะรวมใน Report วันนี้เวลา EOD ครับ"
        )
        logger.info(f"[FILE] parse HumanSoft OK: {count} rows")
    except Exception as e:
        logger.error(f"Parse HumanSoft error: {e}")
        await _push_text_to_group(
            group_id,
            f"⚠️ เกิดข้อผิดพลาดในการอ่านไฟล์ {file_name}\n"
            f"กรุณาตรวจสอบรูปแบบไฟล์ หรือลองส่งใหม่อีกครั้ง"
        )
