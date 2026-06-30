import os
from dotenv import load_dotenv

load_dotenv()

# LINE Messaging API
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_GROUP_ID = os.getenv("LINE_GROUP_ID", "")  # ID ของกลุ่ม LINE เป้าหมาย

# Anthropic Claude API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# การตั้งค่าระบบ
REPORT_TIME     = os.getenv("REPORT_TIME",     "10:45")   # เวลาส่ง daily report (HH:MM, Asia/Bangkok)
WORKPLAN_DEADLINE = os.getenv("WORKPLAN_DEADLINE", "10:30") # deadline ส่งแผนงาน
TIMEZONE = "Asia/Bangkok"

# โฟลเดอร์บันทึกไฟล์ลงเครื่อง (Windows path)
WORKPLAN_SAVE_PATH = os.getenv("WORKPLAN_SAVE_PATH", r"D:\Watercourse\Department\HR\WorkPlan")

# โฟลเดอร์เก็บไฟล์
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")       # รูปภาพและไฟล์จาก LINE
IMAGE_DIR = os.getenv("IMAGE_DIR", "images")          # report images ที่สร้าง

# ฟอนต์ภาษาไทย (ใส่ path ของฟอนต์ที่รองรับภาษาไทย)
THAI_FONT_PATH = os.getenv("THAI_FONT_PATH", "fonts/THSarabunNew.ttf")
THAI_FONT_BOLD_PATH = os.getenv("THAI_FONT_BOLD_PATH", "fonts/THSarabunNew Bold.ttf")
