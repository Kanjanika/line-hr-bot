"""
report_generator.py (v2)
สร้างรูปภาพ Daily HR Report แบ่งตามห้อง / แผนก
Layout: Header → Summary Cards → ตารางแต่ละแผนก → Work Plan Bar → Footer
"""
import os
import re
import logging
from collections import defaultdict
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from config import LINE_CHANNEL_ACCESS_TOKEN, IMAGE_DIR, THAI_FONT_PATH, THAI_FONT_BOLD_PATH
from database import get_messages_by_date, get_attendance_by_date, log_report
from processors.claude_processor import summarize_daily
from processors.staff_mapper import get_room_by_name, get_sort_order, get_all_staff_names

logger = logging.getLogger(__name__)

# ── Canvas ────────────────────────────────────────────────────────────────────
W         = 1100
PAD       = 24
INNER     = W - PAD * 2     # 1052 px usable width

# ── Row / section heights ─────────────────────────────────────────────────────
HEADER_H  = 78
STATS_H   = 86
DEPT_H    = 40
TBL_HDR_H = 31
ROW_H     = 32
SECT_GAP  = 10
WP_BAR_H  = 40
FOOTER_H  = 46

# ── Column positions (X) and widths ──────────────────────────────────────────
CW_NO     = 38
CW_NAME   = 210
CW_ROOM   = 90    # ห้อง (Room)
CW_IN     = 80
CW_STATUS = 100
CW_WP     = 92
CW_RMK    = INNER - CW_NO - CW_NAME - CW_ROOM - CW_IN - CW_STATUS - CW_WP  # ≈ 442

CX_NO     = PAD
CX_NAME   = CX_NO   + CW_NO
CX_ROOM   = CX_NAME + CW_NAME
CX_IN     = CX_ROOM + CW_ROOM
CX_STATUS = CX_IN   + CW_IN
CX_WP     = CX_STATUS + CW_STATUS
CX_RMK    = CX_WP   + CW_WP

# ── Colors — Cost Plan brand theme ────────────────────────────────────────────
C_BG       = (237, 235, 227)   # #EDEBE3 warm cream background
C_NAVY     = (17,  17,  17)    # #111111 near-black (header / footer)
C_DEPT     = (37, 102,  40)    # #256628 brand green (section headers)
C_TBL_HDR = (245, 243, 236)    # #F5F3EC light cream (table header bg)
C_DIVIDER  = (220, 218, 210)   # #DCDAD2 warm divider
C_WHITE    = (255, 255, 255)
C_TEXT     = (17,  17,  17)    # #111111
C_MUTED    = (136, 136, 136)   # #888888
C_HEADER_TEXT = (255, 255, 255)
C_HEADER_SUB  = (136, 136, 136)  # #888888
C_DEPT_TEXT   = (255, 255, 255)
C_DEPT_BADGE  = (255, 255, 255, 50)  # rgba — drawn as translucent rect

# Row background tints
ROW_TINT = {
    "present": C_WHITE,
    "meeting": (251, 247, 238),   # #FBF7EE warm cream tint
    "late":    (255, 252, 222),
    "absent":  (255, 242, 242),   # #FFF2F2
    "leave":   (239, 245, 255),   # #EFF5FF
    "other":   (248, 247, 243),
}

# Status badge (bg_rgb, text_rgb, label)
STATUS_CFG = {
    "present": ((224, 240, 228), (26,  82,  32),  "มาทำงาน"),  # #E0F0E4 / #1A5220
    "meeting": ((253, 232, 187), (122, 74,   0),  "ประชุม"),   # #FDE8BB / #7A4A00
    "late":    ((255, 236, 152), (128, 82,   0),  "มาสาย"),
    "absent":  ((255, 217, 217), (139, 26,  26),  "ขาดงาน"),  # #FFD9D9 / #8B1A1A
    "leave":   ((217, 233, 255), (26,  58, 122),  "ลาหยุด"),  # #D9E9FF / #1A3A7A
    "other":   ((232, 230, 224), (100, 100, 100),  "อื่นๆ"),
}

# Work-plan badge (bg, text, label)
WP_SENT    = ((224, 240, 228), (26,  82,  32),  "ส่งแล้ว")
WP_MISSING = ((255, 217, 217), (139, 26,  26),  "ยังไม่ส่ง")
WP_NA      = ((232, 230, 224), (120, 120, 120),  "-")

# Stats card accent colors
STAT_COLORS = [
    (37, 102,  40),   # present (รวม late)  — #256628 brand green
    (139,  26,  26),  # absent              — #8B1A1A
    (26,   58, 122),  # leave               — #1A3A7A
]


# ── Font helpers ──────────────────────────────────────────────────────────────
def _fonts():
    """Load Thai-capable fonts; graceful fallback to PIL default."""
    sizes = [11, 13, 14, 16, 18, 20, 22, 26, 32]
    out = {}
    for s in sizes:
        try:
            path = THAI_FONT_BOLD_PATH if s >= 20 else THAI_FONT_PATH
            if os.path.exists(path):
                out[s] = ImageFont.truetype(path, s)
            else:
                out[s] = ImageFont.load_default()
        except Exception:
            out[s] = ImageFont.load_default()
    return out


def _trunc(draw, text: str, font, max_px: int) -> str:
    """Truncate text with '…' to fit max_px width."""
    if draw.textlength(text, font=font) <= max_px:
        return text
    while text and draw.textlength(text + "…", font=font) > max_px:
        text = text[:-1]
    return text + "…"


# ── Drawing primitives ────────────────────────────────────────────────────────
def _badge(draw, cx: int, cy: int, text: str,
           bg: tuple, fg: tuple, font, w: int = 88, h: int = 20):
    """Draw a pill badge centered at (cx, cy)."""
    x0, y0 = cx - w // 2, cy - h // 2
    draw.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=h // 2, fill=bg)
    draw.text((cx, cy), text, font=font, fill=fg, anchor="mm")


def _hline(draw, y: int, x0: int = 0, x1: int = W, color=None):
    draw.line([(x0, y), (x1, y)], fill=color or C_DIVIDER, width=1)


# ── Location extractor ────────────────────────────────────────────────────────
_MAPS_RE = re.compile(
    r'https?://(?:maps\.app\.goo\.gl/\S+|(?:www\.)?google\.com/maps\S*|goo\.gl/maps/\S+)',
    re.IGNORECASE
)

def _location_for(emp_name: str, messages: list[dict]) -> str:
    """
    ดึง Google Maps URL หรือสถานที่ประชุมจาก work plan message ของพนักงาน
    ใช้สำหรับ column สถานที่ใน report
    """
    key = emp_name.strip().lower()
    for msg in messages:
        if (msg.get("sender_name", "").strip().lower() == key
                and msg.get("category") == "work_plan"):
            content = msg.get("content", "")
            m = _MAPS_RE.search(content)
            if m:
                # ตัด https:// ออกเพื่อให้สั้นลง เช่น maps.app.goo.gl/xxxxx
                return m.group(0).replace("https://", "").replace("http://", "")
            # ถ้าไม่มี URL แต่มีคำ "ประชุม" → ดึงชื่อสถานที่
            pm = re.search(r'ประชุม(?:ที่|ณ| at|:)?\s*(.{3,35}?)(?:\s|$)', content)
            if pm:
                return pm.group(1).strip()
    return ""


# ── Image builder ─────────────────────────────────────────────────────────────
def build_report_image(target_date: str, summary: dict,
                       messages: list[dict], attendance: list[dict]) -> str:
    f = _fonts()
    f11, f13, f14 = f[11], f[13], f[14]
    f16, f18, f20 = f[16], f[18], f[20]
    f22, f26, f32 = f[22], f[26], f[32]

    # ── Derived data ──────────────────────────────────────────────────────────
    submitted  = {m["sender_name"] for m in messages if m["category"] == "work_plan"}
    not_sub    = set(summary.get("not_submitted", []))

    # ชื่อคนที่ส่ง "ลา" มาใน LINE (ใช้ override กรณี HumanSoft mark เป็น absent)
    line_leave = {
        m["sender_name"]
        for m in messages
        if m["category"] == "leave"
    }

    # ── เติมพนักงานที่ไม่มีใน Excel เป็น "ขาดงาน" (ดึงรายชื่อจาก staff_mapping.csv) ──
    att_names = {a["employee_name"] for a in attendance}
    attendance = list(attendance)   # mutable copy
    for emp_name, room, dept in get_all_staff_names():
        if emp_name not in att_names:
            st = "leave" if emp_name in line_leave else "absent"
            attendance.append({
                "employee_name": emp_name,
                "department":    dept,
                "check_in":      "",
                "check_out":     "",
                "status":        st,
                "leave_type":    "",
                "remark":        "",
                "att_date":      target_date,
                "employee_id":   "",
            })

    # Group attendance by room (from staff_mapping); fallback to "ไม่ระบุห้อง"
    # พร้อม override status ด้วยข้อมูล LINE
    by_room: dict[str, list] = defaultdict(list)
    for a in attendance:
        a = dict(a)  # copy เพื่อไม่แก้ original
        # ถ้า HumanSoft ยังไม่มีข้อมูลลา แต่คนส่ง LINE แจ้งลาแล้ว → override
        if a.get("status") == "absent" and a.get("employee_name") in line_leave:
            a["status"] = "leave"
        room = get_room_by_name(a.get("employee_name", ""))
        if not room:
            room = (a.get("department") or "ไม่ระบุห้อง").strip() or "ไม่ระบุห้อง"
        by_room[room].append(a)

    def _room_sort_key(r: str):
        """เรียงห้อง: ตัวเลข (101→301) ก่อน จากนั้น BOQ, BOH, อื่นๆ"""
        if r.isdigit():
            return (0, int(r), r)
        if r[:3] == "BOQ":
            return (1, 0, r)
        if r[:3] == "BOH":
            return (2, 0, r)
        return (3, 0, r)

    room_order = sorted(by_room.keys(), key=_room_sort_key)
    # เรียงพนักงานในแต่ละห้องตาม staff_mapping.csv order
    for room in room_order:
        by_room[room].sort(key=lambda a: get_sort_order(a.get("employee_name", "")))

    # Status counts — late รวมเป็น present (ไม่แสดงแยก)
    cnt = {
        "present": sum(1 for a in attendance if a["status"] in ("present", "late", "meeting")),
        "absent":  sum(1 for a in attendance if a["status"] == "absent"),
        "leave":   sum(1 for a in attendance if a["status"] == "leave"),
    }
    total = len(attendance)

    # ── Height calculation ────────────────────────────────────────────────────
    dept_h_total = 0
    for room in room_order:
        rows = len(by_room[room])
        dept_h_total += DEPT_H + TBL_HDR_H + rows * ROW_H + SECT_GAP

    H = (HEADER_H + 12 + STATS_H + 14
         + dept_h_total
         + WP_BAR_H + 10
         + FOOTER_H + 10)

    # ── Canvas ────────────────────────────────────────────────────────────────
    img  = Image.new("RGB", (W, H), C_BG)
    draw = ImageDraw.Draw(img)
    y    = 0

    # ─── HEADER ──────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, W, HEADER_H], fill=C_NAVY)
    draw.text((PAD, 18), "รายงานการเข้างานพนักงาน", font=f26, fill=C_HEADER_TEXT)

    th = datetime.strptime(target_date, "%Y-%m-%d")
    th_m = ["","ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.",
            "ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]
    date_str = f"{th.day} {th_m[th.month]} {th.year + 543}"
    time_str = datetime.now().strftime("%H:%M น.")
    draw.text((W - PAD, 16), date_str, font=f18, fill=C_HEADER_SUB, anchor="ra")
    draw.text((W - PAD, 42), time_str, font=f14, fill=C_HEADER_SUB, anchor="ra")
    draw.text((PAD, 50), f"รวม {total} คน", font=f14, fill=C_HEADER_SUB)
    y = HEADER_H + 12

    # ─── SUMMARY CARDS ────────────────────────────────────────────────────────
    labels  = ["มาทำงาน", "ขาดงาน", "ลาหยุด"]
    keys    = ["present", "absent", "leave"]
    card_w  = (INNER - 9 * 2) // 3   # 2 gaps between 3 cards
    cx_card = PAD
    for i, (lbl, key) in enumerate(zip(labels, keys)):
        n   = cnt.get(key, 0)
        col = STAT_COLORS[i]
        draw.rounded_rectangle([cx_card, y, cx_card + card_w, y + STATS_H],
                                radius=10, fill=C_WHITE)
        # left accent bar
        draw.rounded_rectangle([cx_card, y, cx_card + 5, y + STATS_H],
                                radius=3, fill=col)
        pct = f"{n/total*100:.0f}%" if total else "–"
        draw.text((cx_card + card_w // 2, y + 22), str(n),
                  font=f32, fill=col, anchor="ma")
        draw.text((cx_card + card_w // 2, y + 56), lbl,
                  font=f16, fill=C_TEXT, anchor="ma")
        draw.text((cx_card + card_w // 2, y + 74), pct,
                  font=f13, fill=C_MUTED, anchor="ma")
        cx_card += card_w + 9
    y += STATS_H + 14

    # ─── ROOM SECTIONS ────────────────────────────────────────────────────────
    row_num_global = 0   # ลำดับต่อเนื่องทั้งรายงาน

    for room in room_order:
        rows = by_room[room]

        # room header bar
        draw.rounded_rectangle([PAD, y, W - PAD, y + DEPT_H],
                                radius=6, fill=C_DEPT)
        room_label = f"ห้อง {room}" if not room.startswith("B") else room
        draw.text((PAD + 14, y + DEPT_H // 2), room_label,
                  font=f18, fill=C_DEPT_TEXT, anchor="lm")
        badge_txt = f"{len(rows)} คน"
        bw = max(52, int(draw.textlength(badge_txt, font=f13)) + 20)
        bx = W - PAD - bw - 10
        draw.rounded_rectangle([bx, y + 8, bx + bw, y + DEPT_H - 8],
                                radius=10, outline=(180, 220, 180), width=1)
        draw.text((bx + bw // 2, y + DEPT_H // 2), badge_txt,
                  font=f13, fill=(200, 235, 200), anchor="mm")
        y += DEPT_H

        # table header
        draw.rectangle([PAD, y, W - PAD, y + TBL_HDR_H], fill=C_TBL_HDR)
        hdrs = [
            (CX_NO   + CW_NO   // 2,   "#",           True),
            (CX_NAME + 10,             "ชื่อ-นามสกุล", False),
            (CX_ROOM + CW_ROOM // 2,   "ห้อง",         True),
            (CX_IN   + CW_IN   // 2,   "เข้างาน",      True),
            (CX_STATUS + CW_STATUS // 2, "สถานะ",      True),
            (CX_WP   + CW_WP   // 2,   "แผนงาน",       True),
            (CX_RMK  + 10,             "หมายเหตุ",      False),
        ]
        for hx, ht, center in hdrs:
            anchor = "mm" if center else "lm"
            draw.text((hx, y + TBL_HDR_H // 2), ht,
                      font=f14, fill=C_DEPT, anchor=anchor)
        _hline(draw, y + TBL_HDR_H, PAD, W - PAD, C_DIVIDER)
        y += TBL_HDR_H

        # data rows
        for i, emp in enumerate(rows):
            row_num_global += 1
            emp_name = emp.get("employee_name", "")
            status   = emp.get("status", "other")
            disp_st  = "present" if status == "late" else status  # late → มาทำงาน
            # ถ้า work plan มี Google Maps link → badge = ประชุม
            if disp_st == "present" and _location_for(emp_name, messages):
                disp_st = "meeting"
            bg = ROW_TINT.get(disp_st, C_WHITE)
            draw.rectangle([PAD, y, W - PAD, y + ROW_H], fill=bg)

            mid_y = y + ROW_H // 2

            # # (row number)
            draw.text((CX_NO + CW_NO // 2, mid_y), str(row_num_global),
                      font=f13, fill=C_MUTED, anchor="mm")

            # name (emp_name already set above)
            name = _trunc(draw, emp_name, f16, CW_NAME - 16)
            draw.text((CX_NAME + 10, mid_y), name, font=f16, fill=C_TEXT, anchor="lm")

            # room code (short)
            emp_room = get_room_by_name(emp_name) or room
            room_short = emp_room.replace(" / Corridoor", "").replace(" / Corridor", "")
            room_txt = _trunc(draw, room_short, f13, CW_ROOM - 10)
            draw.text((CX_ROOM + CW_ROOM // 2, mid_y), room_txt,
                      font=f13, fill=C_MUTED, anchor="mm")

            # check-in time
            ci = emp.get("check_in") or "–"
            draw.text((CX_IN + CW_IN // 2, mid_y), ci,
                      font=f14, fill=C_TEXT, anchor="mm")

            # status badge (late แสดงเป็น มาทำงาน)
            s_bg, s_fg, s_lbl = STATUS_CFG.get(disp_st, STATUS_CFG["other"])
            _badge(draw, CX_STATUS + CW_STATUS // 2, mid_y,
                   s_lbl, s_bg, s_fg, f13, w=88, h=21)

            # work plan badge
            if disp_st in ("absent", "leave"):
                wp_bg, wp_fg, wp_lbl = WP_NA
            elif emp_name in submitted:
                wp_bg, wp_fg, wp_lbl = WP_SENT
            else:
                wp_bg, wp_fg, wp_lbl = WP_MISSING
            _badge(draw, CX_WP + CW_WP // 2, mid_y,
                   wp_lbl, wp_bg, wp_fg, f13, w=84, h=21)

            # remark
            remark = emp.get("remark") or emp.get("leave_type") or ""
            if emp_name in not_sub and disp_st not in ("absent", "leave"):
                remark = remark or "ยังไม่ส่งแผนงาน"
            if remark:
                remark_txt = _trunc(draw, remark, f13, CW_RMK - 16)
                draw.text((CX_RMK + 10, mid_y), remark_txt,
                          font=f13, fill=C_MUTED, anchor="lm")

            # bottom divider
            _hline(draw, y + ROW_H - 1, PAD, W - PAD,
                   (230, 228, 220) if i < len(rows) - 1 else C_DIVIDER)
            y += ROW_H

        y += SECT_GAP

    # ─── WORK PLAN SUMMARY BAR ───────────────────────────────────────────────
    wp_sent_n = len(submitted)
    # count staff who should have submitted (present+late)
    wp_total  = sum(1 for a in attendance if a.get("status") in ("present", "late"))
    wp_miss_n = max(0, wp_total - wp_sent_n)

    if wp_miss_n == 0 and wp_total > 0:
        bar_bg, bar_fg = (37, 102, 40), (255, 255, 255)   # brand green / white
        bar_txt  = f"แผนงาน: ส่งครบทุกคน ({wp_sent_n}/{wp_total} คน)"
    else:
        bar_bg, bar_fg = (17, 17, 17), (255, 255, 255)    # near-black / white
        bar_txt  = (f"แผนงาน: ส่งแล้ว {wp_sent_n}/{wp_total} คน"
                    + (f"  |  ยังไม่ส่ง {wp_miss_n} คน" if wp_miss_n else ""))

    draw.rounded_rectangle([PAD, y, W - PAD, y + WP_BAR_H],
                            radius=8, fill=bar_bg)
    draw.text((PAD + 16, y + WP_BAR_H // 2),
              bar_txt, font=f16, fill=bar_fg, anchor="lm")
    y += WP_BAR_H + 10

    # ─── FOOTER ──────────────────────────────────────────────────────────────
    draw.rectangle([0, y, W, y + FOOTER_H], fill=C_NAVY)
    legend = [
        ("มาทำงาน", STAT_COLORS[0]),
        ("ขาดงาน",  STAT_COLORS[1]),
        ("ลาหยุด",  STAT_COLORS[2]),
    ]
    lx = PAD + 10
    for lbl, col in legend:
        draw.ellipse([lx, y + 18, lx + 10, y + 28], fill=col)
        draw.text((lx + 14, y + FOOTER_H // 2), lbl,
                  font=f13, fill=C_HEADER_SUB, anchor="lm")
        lx += int(draw.textlength(lbl, font=f13)) + 32

    draw.text((W - PAD, y + FOOTER_H // 2),
              f"สร้างโดย HR Bot · {datetime.now().strftime('%H:%M น.')}",
              font=f13, fill=C_HEADER_SUB, anchor="rm")

    # ─── Save ─────────────────────────────────────────────────────────────────
    os.makedirs(IMAGE_DIR, exist_ok=True)
    # ใช้ timestamp เพื่อ bust cache (LINE cache รูปที่ URL เดิม)
    ts  = datetime.now().strftime("%H%M%S")
    out = os.path.join(IMAGE_DIR, f"report_{target_date}_{ts}.png")
    img.save(out, "PNG", optimize=True)
    logger.info(f"[REPORT] saved: {out}  size={W}×{H}")
    return out


# ── Main flow ─────────────────────────────────────────────────────────────────
async def generate_and_send_report(group_id: str, target_date: str):
    """ดึงข้อมูล → summarize → build image → push to LINE group."""
    messages   = get_messages_by_date(target_date)
    attendance = get_attendance_by_date(target_date)

    logger.info(f"[REPORT] {target_date}: {len(messages)} msgs, "
                f"{len(attendance)} attendance records")

    summary  = await summarize_daily(messages, attendance, target_date)
    img_path = build_report_image(target_date, summary, messages, attendance)

    from linebot.v3.messaging import (
        ApiClient, Configuration, MessagingApi,
        PushMessageRequest, ImageMessage, TextMessage
    )
    line_config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

    with ApiClient(line_config) as api_client:
        api = MessagingApi(api_client)

        base_url = os.getenv("BASE_URL", "").rstrip("/")
        if base_url:
            img_url = f"{base_url}/images/{os.path.basename(img_path)}"
            api.push_message(PushMessageRequest(
                to=group_id,
                messages=[ImageMessage(
                    type="image",
                    original_content_url=img_url,
                    preview_image_url=img_url,
                )]
            ))
            logger.info(f"[REPORT] sent image: {img_url}")
        else:
            # Fallback: text summary
            att  = get_attendance_by_date(target_date)
            s    = summary
            present  = sum(1 for a in att if a["status"] == "present")
            absent   = sum(1 for a in att if a["status"] == "absent")
            late     = sum(1 for a in att if a["status"] == "late")
            on_leave = sum(1 for a in att if a["status"] == "leave")
            th = datetime.strptime(target_date, "%Y-%m-%d")
            th_m = ["","ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.",
                    "ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]
            d_lbl = f"{th.day} {th_m[th.month]} {th.year+543}"
            txt = (
                f"📊 HR Report — {d_lbl}\n{'─'*28}\n"
                f"✅ มาทำงาน {present}  ⏰ สาย {late}  "
                f"❌ ขาด {absent}  🌴 ลา {on_leave}\n"
                f"{'─'*28}\n"
                f"📌 แผนงาน\n{s.get('work_plan_summary','–')}\n"
                f"{'─'*28}\n"
                f"🌴 การลา\n{s.get('leave_summary','–')}\n"
            )
            not_sub = s.get("not_submitted", [])
            if not_sub:
                txt += f"{'─'*28}\n❌ ยังไม่ส่งแผนงาน\n"
                txt += "\n".join(f"  • {n}" for n in not_sub)
            api.push_message(PushMessageRequest(
                to=group_id,
                messages=[TextMessage(type="text", text=txt)]
            ))
            logger.info("[REPORT] sent text fallback")

    log_report(target_date, img_path)
