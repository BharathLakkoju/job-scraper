FROM python:3.11-slim

# ── System deps required by Chrome ───────────────────────────────────────────
RUN apt-get update && apt-get install -y \
  wget curl gnupg ca-certificates \
  libglib2.0-0 libnss3 libfontconfig1 \
  libx11-6 libxcb1 libxext6 libxfixes3 \
  libxi6 libxrandr2 libxrender1 libxtst6 \
  fonts-liberation libasound2 \
  libatk-bridge2.0-0 libatk1.0-0 \
  libcups2 libdbus-1-3 libgtk-3-0 \
  libnspr4 libpango-1.0-0 libpangocairo-1.0-0 \
  libdrm2 libgbm1 libxss1 \
  --no-install-recommends \
  && rm -rf /var/lib/apt/lists/*

# ── Google Chrome (supports both amd64 and arm64) ────────────────────────────
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub \
  | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/google-chrome.gpg] \
  http://dl.google.com/linux/chrome/deb/ stable main" \
  > /etc/apt/sources.list.d/google-chrome.list \
  && apt-get update \
  && apt-get install -y google-chrome-stable \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# ── App source ────────────────────────────────────────────────────────────────
COPY . .

# Runtime dirs (real data comes in via docker-compose volumes)
RUN mkdir -p session_cookies chrome_profile

EXPOSE 5000

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "--timeout", "120", "scraper:app"]
