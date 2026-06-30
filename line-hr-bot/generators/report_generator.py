"""
report_generator.py (v2)
สร้างรูปภาพ Daily HR Report แบ่งตามห้อง / แผนก
Layout: Header → Summary Cards → ตารางแต่ละแผนก → Work Plan Bar → Footer
"""
import os
import logging
from collections import defaultdict
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from config import LINE_CHANNEL_ACCESS_TOKEN, IMAGE_DIR, THAI_FONT_PATH, THAI_FONT_BOLD_PATH
from database import get_messages_by_date, get_attendance_by_date, log_report
from processors.claude_processor import summarize_daily

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
CW_NO     = 40
CW_NAME   = 256
CW_IN     = 88
CW_STATUS = 106
CW_WP     = 100
CW_RMK    = INNER - CW_NO - CW_NAME - CW_IN - CW_STATUS - CW_WP  # ≈ 462

CX_NO     = PAD
CX_NAME   = CX_NO + CW_NO
CX_IN     = CX_NAME + CW_NAME
CX_STATUS = CX_IN + CW_IN
CX_WP     = CX_STATUS + CW_STATUS
CX_RMK    = CX_WP + CW_WP

# ── Colors ────────────────────────────────────────────────────────────────────
C_BG       = (244, 246, 250)
C_NAVY     = (26,  58,  92)
C_DEPT     = (45,  90, 142)
C_TBL_HDR = (216, 228, 244)
C_DIVIDER  = (200, 212, 230)
C_WHITE    = (255, 255, 255)
C_TEXT     = (28,  30,  36)
C_MUTED    = (108, 116, 128)
C_HEADER_TEXT = (255, 255, 255)
C_HEADER_SUB  = (168, 196, 230)
C_DEPT_TEXT   = (255, 255, 255)
C_DEPT_BADGE  = (255, 255, 255, 50)  # rgba — drawn as translucent rect

# Row background tints
ROW_TINT = {
    "present": C_WHITE,
    "late":    (255, 252, 222),
    "absent":  (255, 236, 236),
    "leave":   (233, 243, 255),
    "other":   (250, 250, 252),
}

# Status badge (bg_rgb, text_rgb, label)
STATUS_CFG = {
    "present": ((210, 244, 222), (20, 116, 52),   "มาทำงาน"),
    "late":    ((255, 236, 152), (128, 82,   0),   "มาสาย"),
    "absent":  ((255, 198, 198), (156, 24,  24),   "ขาดงาน"),
    "leave":   ((198, 224, 255), (14,  68, 158),   "ลาหยุด"),
    "other":   ((230, 232, 236), (100, 104, 112),  "อื่นๆ"),
}

# Work-plan badge (bg, text, label)
WP_SENT    = ((210, 244, 222), (20, 116, 52),  "✓ ส่งแล้ว")
WP_MISSING = ((255, 198, 198), (156, 24,  24), "✗ ยังไม่ส่ง")
WP_NA      = ((232, 234, 238), (120, 124, 132), "–")

# Stats card accent colors
STAT_COLORS = [
    (20,  116,  52),   # present  — green
    (148,  96,   0),   # late     — amber
    (156,  24,  24),   # absent   — red
    (14,   68, 158),   # leave    — blue
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

    # Group attendance by department; sort dept names
    by_dept: dict[str, list] = defaultdict(list)
    for a in attendance:
        dept = (a.get("department") or "ไม่ระบุแผนก").strip() or "ไม่ระบุแผนก"
        by_dept[dept].append(a)
    dept_order = sorted(by_dept.keys())

    # Status counts (overall)
    cnt = {k: sum(1 for a in attendance if a["status"] == k)
           for k in ("present", "late", "absent", "leave")}
    total = len(attendance)

    # ── Height calculation ────────────────────────────────────────────────────
    dept_h_total = 0
    for dept in dept_order:
        rows = len(by_dept[dept])
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
    labels  = ["มาทำงาน", "มาสาย", "ขาดงาน", "ลาหยุด"]
    keys    = ["present", "late", "absent", "leave"]
    card_w  = (INNER - 9 * 3) // 4   # 3 gaps between 4 cards
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

    # ─── DEPT SECTIONS ────────────────────────────────────────────────────────
    row_num_global = 0   # ลำดับต่อเนื่องทั้งรายงาน

    for dept in dept_order:
        rows = by_dept[dept]

        # dept header bar
        draw.rounded_rectangle([PAD, y, W - PAD, y + DEPT_H],
                                radius=6, fill=C_DEPT)
        draw.text((PAD + 14, y + DEPT_H // 2), dept,
                  font=f18, fill=C_DEPT_TEXT, anchor="lm")
        badge_txt = f"{len(rows)} คน"
        bw = max(52, int(draw.textlength(badge_txt, font=f13)) + 20)
        bx = W - PAD - bw - 10
        draw.rounded_rectangle([bx, y + 8, bx + bw, y + DEPT_H - 8],
                                radius=10, fill=(255, 255, 255, 0))
        draw.rounded_rectangle([bx, y + 8, bx + bw, y + DEPT_H - 8],
                                radius=10, outline=(200, 220, 240), width=1)
        draw.text((bx + bw // 2, y + DEPT_H // 2), badge_txt,
                  font=f13, fill=(200, 225, 255), anchor="mm")
        y += DEPT_H

        # table header
        draw.rectangle([PAD, y, W - PAD, y + TBL_HDR_H], fill=C_TBL_HDR)
        hdrs = [
            (CX_NO + CW_NO // 2,   "#",          True),
            (CX_NAME + 10,         "ชื่อ-นามสกุล", False),
            (CX_IN + CW_IN // 2,   "เข้างาน",    True),
            (CX_STATUS + CW_STATUS // 2, "สถานะ", True),
            (CX_WP + CW_WP // 2,   "แผนงาน",     True),
            (CX_RMK + 10,          "หมายเหตุ",    False),
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
            status = emp.get("status", "other")
            bg     = ROW_TINT.get(status, C_WHITE)
            draw.rectangle([PAD, y, W - PAD, y + ROW_H], fill=bg)

            mid_y = y + ROW_H // 2

            # # (row number)
            draw.text((CX_NO + CW_NO // 2, mid_y), str(row_num_global),
                      font=f13, fill=C_MUTED, anchor="mm")

            # name (truncate if too long)
            name = _trunc(draw, emp.get("employee_name", ""), f16, CW_NAME - 16)
            draw.text((CX_NAME + 10, mid_y), name, font=f16, fill=C_TEXT, anchor="lm")

            # check-in time
            ci = emp.get("check_in") or "–"
            draw.text((CX_IN + CW_IN // 2, mid_y), ci,
                      font=f14, fill=C_TEXT, anchor="mm")

            # status badge
            s_bg, s_fg, s_lbl = STATUS_CFG.get(status, STATUS_CFG["other"])
            _badge(draw, CX_STATUS + CW_STATUS // 2, mid_y,
                   s_lbl, s_bg, s_fg, f13, w=92, h=21)

            # work plan badge
            emp_name = emp.get("employee_name", "")
            if status in ("absent", "leave"):
                wp_bg, wp_fg, wp_lbl = WP_NA
            elif emp_name in submitted:
                wp_bg, wp_fg, wp_lbl = WP_SENT
            else:
                wp_bg, wp_fg, wp_lbl = WP_MISSING
            _badge(draw, CX_WP + CW_WP // 2, mid_y,
                   wp_lbl, wp_bg, wp_fg, f13, w=88, h=21)

            # remark
            remark = emp.get("remark") or emp.get("leave_type") or ""
            if emp_name in not_sub and status not in ("absent", "leave"):
                remark = remark or "ยังไม่ส่งแผนงาน"
            if remark:
                remark_txt = _trunc(draw, remark, f13, CW_RMK - 16)
                draw.text((CX_RMK + 10, mid_y), remark_txt,
                          font=f13, fill=C_MUTED, anchor="lm")

            # bottom divider
            _hline(draw, y + ROW_H - 1, PAD, W - PAD,
                   (225, 232, 242) if i < len(rows) - 1 else C_DIVIDER)
            y += ROW_H

        y += SECT_GAP

    # ─── WORK PLAN SUMMARY BAR ───────────────────────────────────────────────
    wp_sent_n = len(submitted)
    # count staff who should have submitted (present+late)
    wp_total  = sum(1 for a in attendance if a.get("status") in ("present", "late"))
    wp_miss_n = max(0, wp_total - wp_sent_n)

    if wp_miss_n == 0 and wp_total > 0:
        bar_bg, bar_fg = (210, 244, 222), (18, 100, 44)
        bar_icon = "✓"
        bar_txt  = f"แผนงาน: ส่งครบทุกคน ({wp_sent_n}/{wp_total} คน)"
    else:
        bar_bg, bar_fg = (255, 236, 236), (140, 24, 24)
        bar_icon = "✗"
        bar_txt  = (f"แผนงาน: ส่งแล้ว {wp_sent_n}/{wp_total} คน"
                    + (f"  ·  ยังไม่ส่ง {wp_miss_n} คน" if wp_miss_n else ""))

    draw.rounded_rectangle([PAD, y, W - PAD, y + WP_BAR_H],
                            radius=8, fill=bar_bg)
    draw.text((PAD + 16, y + WP_BAR_H // 2),
              f"{bar_icon}  {bar_txt}", font=f16, fill=bar_fg, anchor="lm")
    y += WP_BAR_H + 10

    # ─── FOOTER ──────────────────────────────────────────────────────────────
    draw.rectangle([0, y, W, y + FOOTER_H], fill=C_NAVY)
    legend = [
        ("มาทำงาน", STAT_COLORS[0]),
        ("มาสาย",   STAT_COLORS[1]),
        ("ขาดงาน",  STAT_COLORS[2]),
        ("ลาหยุด",  STAT_COLORS[3]),
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
    out = os.path.join(IMAGE_DIR, f"report_{target_date}.png")
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
