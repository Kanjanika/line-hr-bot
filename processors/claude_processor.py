"""
claude_processor.py
ใช้ Anthropic Claude API เพื่อ:
  1. classify_text()   - จัดหมวดข้อความ (work_plan / leave / other)
  2. analyze_image()   - อ่านรูปภาพ หาข้อมูลแผนงาน/ใบลา
  3. summarize_daily() - สรุปข้อมูลทั้งวันสำหรับ report
"""
import base64
import json
import logging
from typing import Tuple

import anthropic

from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """คุณเป็น HR Assistant สำหรับบริษัทไทย
หน้าที่ของคุณคือวิเคราะห์ข้อความและรูปภาพจากกลุ่ม LINE ของพนักงาน
แล้วจัดหมวดหมู่และสรุปข้อมูลด้านการทำงานและการลางาน
ตอบเป็นภาษาไทยเสมอ ให้กระชับและตรงประเด็น"""


async def classify_text(sender_name: str, text: str) -> tuple[str, str]:
    """
    จัดหมวดหมู่ข้อความ
    Return: (category, summary)
      category: 'work_plan' | 'leave' | 'other'
      summary: สรุปสั้นๆ ของเนื้อหา
    """
    prompt = f"""พนักงานชื่อ "{sender_name}" ส่งข้อความว่า:
"{text}"

จงวิเคราะห์และตอบในรูปแบบ JSON เท่านั้น:
{{
  "category": "work_plan" หรือ "leave" หรือ "other",
  "summary": "สรุปเนื้อหาสั้นๆ ไม่เกิน 2 บรรทัด"
}}

- work_plan = แผนงาน, งานที่จะทำ, ความคืบหน้างาน, OT
- leave = แจ้งลา, ขอลา, ลาป่วย, ลากิจ, วันหยุด
- other = ข้อความทั่วไปที่ไม่เกี่ยวกับงาน"""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        # ดึง JSON จาก response
        start = raw.find("{")
        end = raw.rfind("}") + 1
        data = json.loads(raw[start:end])
        return data.get("category", "other"), data.get("summary", "")
    except Exception as e:
        logger.error(f"classify_text error: {e}")
        return "other", ""


async def analyze_image(sender_name: str, image_path: str) -> tuple[str, str]:
    """
    วิเคราะห์รูปภาพจาก LINE
    Return: (category, summary)
    """
    try:
        with open(image_path, "rb") as f:
            img_data = base64.standard_b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logger.error(f"Read image error: {e}")
        return "other", ""

    prompt = f"""พนักงานชื่อ "{sender_name}" ส่งรูปภาพนี้มาในกลุ่ม LINE

จงวิเคราะห์รูปภาพและตอบในรูปแบบ JSON เท่านั้น:
{{
  "category": "work_plan" หรือ "leave" หรือ "other",
  "summary": "อธิบายเนื้อหาในรูปสั้นๆ ไม่เกิน 3 บรรทัด"
}}

- work_plan = รูปแผนงาน, ตารางงาน, หน้างาน, ผลงาน, OT
- leave = ใบลา, เอกสารการลา
- other = รูปทั่วไป"""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_data,
                        },
                    },
                    {"type": "text", "text": prompt}
                ]
            }]
        )
        raw = resp.content[0].text.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        data = json.loads(raw[start:end])
        return data.get("category", "other"), data.get("summary", "")
    except Exception as e:
        logger.error(f"analyze_image error: {e}")
        return "other", ""


async def summarize_daily(messages: list[dict], attendance: list[dict],
                          target_date: str, not_submitted: list[str] | None = None) -> dict:
    """
    สรุปข้อมูลทั้งวันสำหรับสร้าง report
    Return: {
        "work_plan_summary": str,
        "leave_summary": str,
        "attendance_summary": str,
        "highlights": str,
        "not_submitted": list[str]   # รายชื่อที่ยังไม่ส่งแผนงาน
    }
    """
    # เตรียมข้อมูลข้อความ
    msg_lines = []
    for m in messages:
        if m["category"] in ("work_plan", "leave"):
            msg_lines.append(f"  [{m['category']}] {m['sender_name']}: {m['summary'] or m['content'][:80]}")

    # เตรียมข้อมูล attendance
    att_lines = []
    for a in attendance:
        att_lines.append(
            f"  {a['employee_name']} ({a['department'] or '-'}): "
            f"เข้า {a['check_in'] or '-'} ออก {a['check_out'] or '-'} "
            f"สถานะ: {a['status']}"
        )

    prompt = f"""ข้อมูลวันที่ {target_date}

== ข้อความจากกลุ่ม LINE ==
{chr(10).join(msg_lines) if msg_lines else "ไม่มีข้อความที่เกี่ยวข้อง"}

== ข้อมูลสแกนนิ้วมือ HumanSoft ==
{chr(10).join(att_lines) if att_lines else "ยังไม่มีข้อมูล HumanSoft วันนี้"}

จงสรุปและตอบในรูปแบบ JSON:
{{
  "work_plan_summary": "สรุปแผนงานและ OT ทั้งหมดวันนี้",
  "leave_summary": "สรุปการลาทั้งหมดวันนี้",
  "attendance_summary": "สรุปภาพรวมการเข้างาน เช่น มาทำงาน X คน ขาด Y คน สาย Z คน",
  "highlights": "ประเด็นสำคัญหรือสิ่งที่ต้องติดตาม (ถ้ามี)"
}}"""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        result = json.loads(raw[start:end])
    except Exception as e:
        logger.error(f"summarize_daily error: {e}")
        result = {
            "work_plan_summary": "ไม่สามารถสรุปได้",
            "leave_summary":     "ไม่สามารถสรุปได้",
            "attendance_summary":"ไม่สามารถสรุปได้",
            "highlights":        ""
        }

    # แนบรายชื่อที่ยังไม่ส่ง (คำนวณจากข้อมูล ไม่ใช่ Claude)
    if not_submitted is None:
        submitted = {m["sender_name"] for m in messages if m["category"] == "work_plan"}
        all_staff = [a["employee_name"] for a in attendance if a["status"] in ("present", "late")]
        not_submitted = [n for n in all_staff if n not in submitted]
    result["not_submitted"] = not_submitted
    return result
