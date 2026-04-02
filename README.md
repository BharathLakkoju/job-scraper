# JobFlow AI

> Autonomous job application pipeline — scrape listings from LinkedIn, Indeed, and Naukri, get AI-tailored resumes per job, and track every application in Google Sheets.

---

## About

JobFlow AI is a locally-hosted, zero-cost automation system that handles the full job application workflow for a software engineer. It exposes a Python/Flask scraper service orchestrated by a self-hosted n8n workflow engine, delivering a hands-free pipeline from job discovery to application tracking.

The system runs entirely on a developer's local machine using free-tier and open-source tools. It can be migrated to an Oracle Cloud Always Free VM for 24/7 unattended operation.

## How It Works

```
User triggers Workflow in n8n
        ↓
n8n POST → Flask /scrape → JobSpy scrapes LinkedIn, Indeed, Naukri
        ↓
n8n splits job list → OpenRouter (Llama 3.3 70B free) tailors resume per JD
        ↓
Tailored resume saved to Google Drive · Row appended to Google Sheets
        ↓
User reviews Sheet → applies manually via job URL
        ↓
Gmail Watcher (hourly) → AI classifies recruiter emails → Sheet status updated
```

## Features

- **Multi-source Job Scraping** — LinkedIn, Indeed, Naukri, and Glassdoor via JobSpy with keyword, location, and date filters
- **Deduplication** — Skips jobs already present in the tracker by comparing job URLs
- **AI Resume Tailoring** — Each job description is sent to OpenRouter (Llama 3.3 70B, free tier) which rewrites your base resume to match the JD
- **Google Drive Storage** — Tailored resumes saved with structured naming (`Company_Role_Date`)
- **Google Sheets Tracker** — Full application log with Company, Role, Location, Status, URL, and Resume link columns
- **Gmail Status Sync** — Hourly workflow reads Gmail, AI classifies recruiter emails (Interview / Rejected / Offer), and auto-updates the tracker
- **Cookie Harvester** — Selenium-based session cookie extractor for authenticated scraping
- **Dockerized n8n** — n8n runs in Docker for clean, reproducible orchestration

## Tech Stack

| Category | Technology |
|----------|-----------|
| Scraping | Python-JobSpy (LinkedIn, Indeed, Naukri, Glassdoor) |
| Web Server | Flask |
| Browser Automation | Selenium, undetected-chromedriver |
| HTML Parsing | BeautifulSoup4, lxml |
| Orchestration | n8n (self-hosted, Docker) |
| AI / LLM | OpenRouter — Llama 3.3 70B Instruct (free tier) |
| Job Tracking | Google Sheets (gspread + google-auth) |
| Email Parsing | Gmail API via n8n |
| Real-time | websockets |
| Config | python-dotenv |
| Containerisation | Docker + Docker Compose |

## Setup

### Prerequisites

- Python 3.11+
- Docker Desktop
- Google Cloud project with OAuth2 credentials (Sheets + Drive + Gmail)

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the Flask scraper

```bash
python scraper.py
# Runs on http://localhost:5000
```

### 3. Start n8n in Docker

```bash
docker-compose up
# n8n UI at http://localhost:5678
```

### 4. Configure Google credentials in n8n

Connect Google Sheets, Google Drive, and Gmail OAuth2 credentials in the n8n credentials panel.

## API Reference

### `POST /scrape`

Scrape jobs from selected sources.

**Request body:**

```json
{
  "keywords": "Full Stack Developer OR AI Engineer",
  "location": "Hyderabad, India",
  "sources": ["linkedin", "indeed", "naukri"],
  "num_jobs": 20,
  "hours_old": 168
}
```

**Response:** `{ "jobs": [...], "count": 20 }`

## Google Sheets Tracker Schema

| Column | Description |
|--------|-------------|
| Company | Company name |
| Role | Job title |
| Location | City / Remote |
| Applied Date | YYYY-MM-DD |
| Status | Pending → Applied → Interview → Offer → Rejected |
| Job URL | Direct link (used for deduplication) |
| Resume Version | Google Drive filename of tailored resume |
| Salary Min / Max | When available from the listing |
| Source | LinkedIn / Indeed / Naukri |

## License

MIT
