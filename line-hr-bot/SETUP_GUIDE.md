# 🤖 LINE HR Bot — คู่มือติดตั้ง

## ภาพรวมระบบ

```
พนักงานส่งข้อความ/รูป               HR ส่งไฟล์ HumanSoft
        │                                      │
        ▼                                      ▼
┌──────────────────────────────────────────────────┐
│           LINE Messaging API (Webhook)           │
└─────────────────────┬────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────┐
│   FastAPI Server (Railway)                       │
│   ├── message_handler.py  ← รับ text/image/file  │
│   ├── claude_processor.py ← วิเคราะห์ AI         │
│   ├── humansoft_parser.py ← parse Excel          │
│   └── SQLite DB           ← เก็บข้อมูลทั้งวัน    │
└─────────────────────┬────────────────────────────┘
                      │ ทุกวัน 18:00
                      ▼
┌──────────────────────────────────────────────────┐
│   Report Generator                               │
│   ├── summarize_daily() → Claude API             │
│   └── build_report_image() → PNG                 │
└─────────────────────┬────────────────────────────┘
                      │
                      ▼
             ส่งรูป Report → LINE กลุ่ม
```

---

## ขั้นตอนที่ 1 — สร้าง LINE Official Account

1. ไปที่ https://developers.line.biz/console/
2. สร้าง **Provider** ใหม่ (ชื่อบริษัท)
3. สร้าง **Channel** ชนิด **Messaging API**
4. ใน tab **Messaging API** → เปิด **Allow bot to join group chats**
5. คัดลอก **Channel Secret** และ **Channel Access Token**
6. ในส่วน **Webhook** จะกรอก URL ในขั้นตอนถัดไป

---

## ขั้นตอนที่ 2 — Deploy บน Railway (ฟรี)

1. สมัคร https://railway.app (ใช้ GitHub account)
2. กด **New Project** → **Deploy from GitHub repo**
3. Upload โฟลเดอร์ `line-hr-bot` ขึ้น GitHub ก่อน แล้ว link
4. Railway จะ build อัตโนมัติ
5. ไปที่ **Settings** → **Networking** → สร้าง Public Domain
6. URL จะมีรูปแบบ: `https://xxxx.up.railway.app`

---

## ขั้นตอนที่ 3 — ตั้งค่า Environment Variables บน Railway

ใน Railway → Project → Variables → เพิ่มตามนี้:

| Variable                    | ค่า                                    |
|-----------------------------|----------------------------------------|
| `LINE_CHANNEL_SECRET`       | จาก LINE Console                       |
| `LINE_CHANNEL_ACCESS_TOKEN` | จาก LINE Console                       |
| `LINE_GROUP_ID`             | ดูขั้นตอนด้านล่าง ↓                    |
| `ANTHROPIC_API_KEY`         | จาก https://console.anthropic.com      |
| `REPORT_TIME`               | `18:00` (หรือเวลาที่ต้องการ)           |
| `BASE_URL`                  | `https://xxxx.up.railway.app`          |

---

## ขั้นตอนที่ 4 — ตั้งค่า LINE Webhook

1. กลับไป LINE Console → Channel → Messaging API
2. ใส่ Webhook URL: `https://xxxx.up.railway.app/webhook`
3. กด **Verify** — ต้องได้ ✅ Success
4. เปิด **Use webhook**

---

## ขั้นตอนที่ 5 — หา LINE Group ID

1. เพิ่ม LINE Bot เข้ากลุ่ม
2. ใครก็ได้ส่งข้อความใดก็ได้ในกลุ่ม
3. ดู Railway Logs จะเห็น:
   ```
   INFO: group_id = Cxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
4. Copy ไปใส่ `LINE_GROUP_ID` ใน Railway Variables

---

## ขั้นตอนที่ 6 — ติดตั้งฟอนต์ภาษาไทย

ดาวน์โหลดฟอนต์ TH Sarabun New:
- https://www.f0nt.com/release/th-sarabun-new/

วางไฟล์ในโฟลเดอร์ `fonts/`:
```
line-hr-bot/
└── fonts/
    ├── THSarabunNew.ttf
    └── THSarabunNew Bold.ttf
```

---

## การใช้งานในกลุ่ม LINE

| พนักงาน/HR พิมพ์            | ผลลัพธ์                                          |
|-----------------------------|--------------------------------------------------|
| ข้อความแผนงานทั่วไป          | Bot จัดเก็บอัตโนมัติ ไม่ response               |
| "ขอลาวันพรุ่งนี้..."         | Bot จัดเก็บเป็นหมวด leave อัตโนมัติ             |
| ส่งรูปแผนงาน / ใบลา          | Claude Vision อ่านและสรุป                        |
| ส่งไฟล์ Excel HumanSoft      | Bot parse และตอบยืนยัน                           |
| พิมพ์ `/report` หรือ `รายงาน`| Bot สร้างและส่ง report ทันที                     |
| ทุกวันเวลา 18:00              | Bot ส่ง report อัตโนมัติ                         |

---

## ตัวอย่าง Column HumanSoft ที่รองรับ

ระบบรองรับ column ทั้งภาษาไทยและอังกฤษ เช่น:

**แบบไทย:**
```
รหัส | ชื่อ | แผนก | วันที่ | เวลาเข้า | เวลาออก | สถานะ | หมายเหตุ
```

**แบบอังกฤษ:**
```
EmpCode | EmpName | Dept | Date | TimeIn | TimeOut | Status | Remark
```

---

## Troubleshooting

**Bot ไม่ตอบสนอง:**
- ตรวจสอบ Webhook URL และ Verify ใน LINE Console
- ดู Railway Logs หา error

**ไม่มีรูปภาพใน report:**
- ตรวจสอบว่าตั้ง `BASE_URL` ใน Railway Variables แล้ว
- ถ้ายังไม่มี จะ fallback เป็น text report แทน

**parse Excel ไม่ได้:**
- ตรวจสอบชื่อ column ว่าตรงกับที่รองรับ
- ลอง Export เป็น CSV แล้วส่งแทน

**รูปแตก / อ่านไม่ออก:**
- ตรวจสอบว่าติดตั้งฟอนต์ใน `fonts/` แล้ว
- ถ้าไม่มีฟอนต์ไทย จะใช้ฟอนต์ default (ภาษาไทยอาจแสดงเป็น ?)
