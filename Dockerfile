FROM python:3.12-slim

# Install system dependencies (Pillow + wget สำหรับดาวน์โหลดฟอนต์)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype6-dev \
    libwebp-dev \
    libtiff-dev \
    libopenjp2-7-dev \
    wget \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# สร้าง directories และดาวน์โหลดฟอนต์ภาษาไทย Sarabun (= TH Sarabun New รุ่นใหม่)
RUN mkdir -p uploads images data fonts && \
    wget -q -O fonts/THSarabunNew.ttf \
        "https://github.com/google/fonts/raw/main/ofl/sarabun/Sarabun-Regular.ttf" && \
    wget -q -O "fonts/THSarabunNew Bold.ttf" \
        "https://github.com/google/fonts/raw/main/ofl/sarabun/Sarabun-Bold.ttf" && \
    echo "Font downloaded: $(ls -lh fonts/)"

EXPOSE 8080

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
