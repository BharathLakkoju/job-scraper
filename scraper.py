import hashlib
import json
import os
import pickle
import random
import time
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional; env vars can be set directly

import pandas as pd
import requests
from flask import Flask, jsonify, request, render_template
from jobspy import scrape_jobs

try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    _SELENIUM_OK = True
    print("[Init] Selenium / undetected_chromedriver loaded OK")
except Exception as _selenium_import_err:
    _SELENIUM_OK = False
    print(f"[Init] Selenium import failed: {type(_selenium_import_err).__name__}: {_selenium_import_err}")
    print("       Selenium scrapers (Naukri, Wellfound, Hirist, InstaHyre) will be disabled.")

app = Flask(__name__)

# ─── Config paths ─────────────────────────────────────────────────────────────
_DIR          = os.path.dirname(os.path.abspath(__file__))
_PROFILE_PATH = os.path.join(_DIR, "user_profile.json")
_CREDS_PATH   = os.path.join(_DIR, "credentials.json")
_COOKIES_DIR  = os.path.join(_DIR, "session_cookies")
os.makedirs(_COOKIES_DIR, exist_ok=True)

with open(_PROFILE_PATH, "r", encoding="utf-8") as _f:
    USER_PROFILE = json.load(_f)

# Optional: credentials for authenticated scraping — fill credentials.json
_CREDS: dict = {}
if os.path.exists(_CREDS_PATH):
    with open(_CREDS_PATH, "r", encoding="utf-8") as _f:
        _CREDS = json.load(_f)
    print("[Init] credentials.json loaded")

# Load resume text for match scoring
_resume_filename = USER_PROFILE.get("resume_file", "")
_RESUME_TEXT: str = ""
if _resume_filename:
    _rp = os.path.join(_DIR, _resume_filename)
    if os.path.exists(_rp):
        with open(_rp, "r", encoding="utf-8") as _f:
            _RESUME_TEXT = _f.read()
        print(f"[Init] Resume loaded: {_resume_filename}")
    else:
        print(f"[Init] Resume file not found: {_resume_filename}")

# Pre-computed lowercase lists for fast per-job scoring
_SKILLS:    list = [s.lower() for s in USER_PROFILE.get("skills", [])]
_TITLES:    list = [t.lower() for t in USER_PROFILE.get("preferred_titles", [])]
_EXCLUDE:   list = [k.lower() for k in USER_PROFILE.get("exclude_keywords", [])]
_LOC_PREFS: list = [p.lower() for p in USER_PROFILE.get("preferred_locations", [])]

# ─── Constants ────────────────────────────────────────────────────────────────
# Sources handled natively by python-jobspy
# naukri  → removed: JobSpy hits Naukri's API and gets 406/recaptcha; use custom Selenium scraper
# google  → removed: unreliable for India region
JOBSPY_SOURCES = {"linkedin", "indeed", "glassdoor", "zip_recruiter"}

# Default source list for every /scrape call
DEFAULT_SOURCES = [
    "linkedin", "indeed", "naukri", "glassdoor",
    "wellfound", "hirist", "instahyre",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _get(url: str) -> "requests.Response | None":
    """HTTP GET with a polite random delay to avoid rate-limiting."""
    try:
        time.sleep(random.uniform(1.0, 2.5))
        return requests.get(url, headers=_HEADERS, timeout=15)
    except Exception as exc:
        print(f"[HTTP] GET failed ({url}): {exc}")
        return None


def _text(el) -> str:
    return el.get_text(strip=True) if el else ""


def _href(el, base: str = "") -> str:
    href = el.get("href", "") if el else ""
    return (base + href) if href.startswith("/") else href


# ─── Selenium helpers ─────────────────────────────────────────────────────────
# Set to False to show the browser window (useful when debugging selectors)
_SELENIUM_HEADLESS = True


def _make_driver():
    """Create an undetected Chrome WebDriver. Much harder to detect than vanilla Selenium."""
    if not _SELENIUM_OK:
        raise RuntimeError(
            "undetected_chromedriver not installed. Run: pip install undetected-chromedriver"
        )
    options = uc.ChromeOptions()
    profile_dir = os.path.join(_DIR, "chrome_profile")
    options.add_argument(f"--user-data-dir={profile_dir}")
    if _SELENIUM_HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--lang=en-US")
    options.add_argument("--disable-blink-features=AutomationControlled")
    return uc.Chrome(options=options, use_subprocess=True, version_main=146)


def _load_session_cookies(driver, site_name: str, base_url: str) -> bool:
    """
    Inject browser cookies saved by cookie_harvester.py into a Selenium driver.
    This is how Google-auth sessions are reused without a password.
    Returns True if a cookie file was found and loaded.
    """
    cookie_path = os.path.join(_COOKIES_DIR, f"{site_name}.pkl")
    if not os.path.exists(cookie_path):
        return False
    driver.get(base_url)
    time.sleep(1)
    with open(cookie_path, "rb") as f:
        cookies = pickle.load(f)
    for c in cookies:
        c.pop("sameSite", None)   # Selenium rejects invalid sameSite values
        try:
            driver.add_cookie(c)
        except Exception:
            pass
    driver.refresh()
    time.sleep(2)
    print(f"[Cookies] Session loaded for {site_name}")
    return True


def _sel_first(parent, selectors: list):
    """Try CSS selectors in order, return first matching WebElement or None."""
    for sel in selectors:
        try:
            els = parent.find_elements(By.CSS_SELECTOR, sel)
            if els:
                return els[0]
        except Exception:
            pass
    return None


def _sel_text(parent, selectors: list, default: str = "") -> str:
    el = _sel_first(parent, selectors)
    return el.text.strip() if el else default


def _sel_href(parent, selectors: list, base: str = "") -> str:
    el = _sel_first(parent, selectors)
    if not el:
        return ""
    href = el.get_attribute("href") or ""
    return (base + href) if href.startswith("/") else href


def compute_resume_match(job: dict) -> float:
    """
    Score 0–100: how well this job matches the user's profile and resume.

    Breakdown:
      Skill coverage (60 pts) — % of profile skills that appear in the JD (capped at 60)
      Title match    (20 pts) — at least one preferred title present in job title
      Location match (10 pts) — preferred location or is_remote flag
      Exclude penalty         — -10 per seniority/exclusion keyword found

    Even jobs with no description score on title + location, so they are
    not silently dropped when custom scrapers can't fetch descriptions.
    """
    text       = f"{job.get('title', '')} {job.get('description', '')}".lower()
    title_text = job.get("title", "").lower()

    # Skill coverage
    skill_hits  = sum(1 for s in _SKILLS if s in text)
    skill_score = (skill_hits / max(len(_SKILLS), 1)) * 60

    # Title match (check job title specifically for stronger signal)
    title_score = 20 if any(t in title_text for t in _TITLES) else 0

    # Location / remote match
    loc       = job.get("location", "").lower()
    loc_match = any(p in loc for p in _LOC_PREFS) or job.get("is_remote", False)
    loc_score = 10 if loc_match else 0

    # Exclusion penalty
    penalty = sum(10 for kw in _EXCLUDE if kw in text)

    return round(max(min(skill_score + title_score + loc_score - penalty, 100), 0), 1)


# ─── Custom scraper: Wellfound (Selenium) ────────────────────────────────────
def _scrape_wellfound(keywords: str, location: str, num_jobs: int) -> list:
    """Wellfound.com — React SPA, Selenium required for JS-rendered job cards."""
    if not _SELENIUM_OK:
        print("[Wellfound] Selenium unavailable. Run: pip install undetected-chromedriver")
        return []

    jobs   = []
    kw     = keywords.replace(" OR ", " ").replace(" ", "%20")
    loc    = location.split(",")[0].strip().replace(" ", "%20")
    driver = None
    try:
        driver = _make_driver()
        _load_session_cookies(driver, "wellfound", "https://wellfound.com")
        driver.get(f"https://wellfound.com/jobs?q={kw}&l={loc}")

        wait = WebDriverWait(driver, 15)
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR,
                "[data-test='StartupResult'], div[class*='JobListing'], "
                "div[class*='job-list'], ul[class*='jobs']")))
        except Exception:
            print("[Wellfound] Timed out waiting for job listings.")
            return []

        # Scroll to load more jobs
        for _ in range(4):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)

        cards = (
            driver.find_elements(By.CSS_SELECTOR, "[data-test='StartupResult']") or
            driver.find_elements(By.CSS_SELECTOR, "div[class*='JobListing']") or
            driver.find_elements(By.CSS_SELECTOR, "li[class*='job']") or
            driver.find_elements(By.CSS_SELECTOR, "article")
        )[:num_jobs]

        for card in cards:
            try:
                title_el = _sel_first(card, [
                    "a[data-test='job-title']", "h2 a", "h3 a",
                    "a[href*='/jobs/']", "a[href*='/role/']",
                ])
                if not title_el:
                    continue

                apply_url = title_el.get_attribute("href") or ""
                if apply_url.startswith("/"):
                    apply_url = "https://wellfound.com" + apply_url

                loc_text = _sel_text(card, [
                    "[data-test='location']", "span[class*='location']",
                    "span[class*='remote']", "[class*='Location']",
                ])
                jobs.append({
                    "source":      "wellfound",
                    "title":       title_el.text.strip(),
                    "company":     _sel_text(card, ["[data-test='company-name']", "span[class*='company']", "h2"]),
                    "location":    loc_text or location,
                    "url":         apply_url,
                    "description": _sel_text(card, ["[class*='description']", "p"]),
                    "job_type":    "",
                    "date_posted": "",
                    "is_remote":   "remote" in loc_text.lower(),
                    "salary_min":  _sel_text(card, ["[class*='salary']", "[class*='compensation']"]),
                    "salary_max":  "",
                })
            except Exception as exc:
                print(f"[Wellfound] Card error: {exc}")
    except Exception as exc:
        print(f"[Wellfound/Selenium] {exc}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    print(f"[Wellfound] {len(jobs)} jobs found")
    return jobs


# ─── Custom scraper: Hirist (Selenium) ───────────────────────────────────────
def _scrape_hirist(keywords: str, num_jobs: int) -> list:
    """Hirist.tech — India tech jobs. Selenium for reliable JS rendering."""
    if not _SELENIUM_OK:
        print("[Hirist] Selenium unavailable. Run: pip install undetected-chromedriver")
        return []

    jobs   = []
    query  = keywords.replace(" OR ", " ").replace(" ", "+")
    driver = None
    try:
        driver = _make_driver()
        _load_session_cookies(driver, "hirist", "https://www.hirist.tech")
        driver.get(f"https://www.hirist.tech/search?q={query}")

        wait = WebDriverWait(driver, 15)
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR,
                ".job-listing-card, .job-card, [class*='jobCard'], "
                "ul.jobs-list > li, [class*='job-item'], article")))
        except Exception:
            print("[Hirist] Timed out waiting for job listings.")
            return []

        # Scroll to load more jobs
        for _ in range(4):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)

        cards = (
            driver.find_elements(By.CSS_SELECTOR, ".job-listing-card") or
            driver.find_elements(By.CSS_SELECTOR, ".job-card") or
            driver.find_elements(By.CSS_SELECTOR, "ul.jobs-list > li") or
            driver.find_elements(By.CSS_SELECTOR, "[class*='job-item']") or
            driver.find_elements(By.CSS_SELECTOR, "article")
        )[:num_jobs]

        for card in cards:
            try:
                title_el = _sel_first(card, [
                    "h2 a", "h3 a", ".job-title a",
                    "a[href*='/it-jobs/']", "a[href*='/job/']",
                ])
                if not title_el:
                    continue

                apply_url = title_el.get_attribute("href") or ""
                if apply_url.startswith("/"):
                    apply_url = "https://www.hirist.tech" + apply_url

                loc_text = _sel_text(card, [".location", ".job-location", "[class*='location']"])
                jobs.append({
                    "source":      "hirist",
                    "title":       title_el.text.strip(),
                    "company":     _sel_text(card, [".company-name", ".employer-name", "[class*='company']"]),
                    "location":    loc_text or "India",
                    "url":         apply_url,
                    "description": _sel_text(card, [".job-desc", ".description", "p", "[class*='desc']"]),
                    "job_type":    "",
                    "date_posted": "",
                    "is_remote":   "remote" in loc_text.lower(),
                    "salary_min":  _sel_text(card, [".salary", "[class*='salary']", ".ctc"]),
                    "salary_max":  "",
                })
            except Exception as exc:
                print(f"[Hirist] Card error: {exc}")
    except Exception as exc:
        print(f"[Hirist/Selenium] {exc}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    print(f"[Hirist] {len(jobs)} jobs found")
    return jobs


# ─── Custom scraper: InstaHyre (Selenium) ────────────────────────────────────
def _scrape_instahyre(keywords: str, location: str, num_jobs: int) -> list:
    """InstaHyre.com — India startup/product jobs. Selenium for JS-rendered content."""
    if not _SELENIUM_OK:
        print("[InstaHyre] Selenium unavailable. Run: pip install undetected-chromedriver")
        return []

    jobs   = []
    kw     = keywords.replace(" OR ", " ").replace(" ", "%20")
    city   = location.split(",")[0].strip()
    driver = None
    try:
        driver = _make_driver()
        _load_session_cookies(driver, "instahyre", "https://www.instahyre.com")
        driver.get(f"https://www.instahyre.com/search-jobs/?designation={kw}&city={city}")

        wait = WebDriverWait(driver, 15)
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR,
                ".opportunity-card, .job-card, [class*='opportunity'], [class*='JobCard']")))
        except Exception:
            print("[InstaHyre] Timed out waiting for job listings.")
            return []

        # Scroll to load more jobs
        for _ in range(4):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)

        cards = (
            driver.find_elements(By.CSS_SELECTOR, ".opportunity-card") or
            driver.find_elements(By.CSS_SELECTOR, ".job-card") or
            driver.find_elements(By.CSS_SELECTOR, "[class*='opportunity']")
        )[:num_jobs]

        for card in cards:
            try:
                title_el = _sel_first(card, [
                    ".designation", "h2 a", "h3 a",
                    "[class*='title']", "[class*='designation']",
                ])
                if not title_el:
                    continue

                link_el   = _sel_first(card, ["a[href]"])
                apply_url = link_el.get_attribute("href") if link_el else ""
                if apply_url and apply_url.startswith("/"):
                    apply_url = "https://www.instahyre.com" + apply_url

                loc_text = _sel_text(card, [".location", "[class*='location']"])
                jobs.append({
                    "source":      "instahyre",
                    "title":       title_el.text.strip(),
                    "company":     _sel_text(card, [".company-name", ".employer", "[class*='company']"]),
                    "location":    loc_text or location,
                    "url":         apply_url,
                    "description": _sel_text(card, [".skills-list", ".description", "[class*='desc']"]),
                    "job_type":    "",
                    "date_posted": "",
                    "is_remote":   "remote" in loc_text.lower(),
                    "salary_min":  _sel_text(card, [".salary", "[class*='salary']", ".ctc"]),
                    "salary_max":  "",
                })
            except Exception as exc:
                print(f"[InstaHyre] Card error: {exc}")
    except Exception as exc:
        print(f"[InstaHyre/Selenium] {exc}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    print(f"[InstaHyre] {len(jobs)} jobs found")
    return jobs


# ─── Custom scraper: Naukri (Selenium) ───────────────────────────────────────
def _scrape_naukri(keywords: str, location: str, num_jobs: int) -> list:
    """
    Naukri.com Selenium scraper — replaces JobSpy's Naukri which returns 406/recaptcha.
    Loads saved session cookies from cookie_harvester.py for better results.
    freshness=1 filters Naukri to jobs posted in the last 24 hours.
    """
    if not _SELENIUM_OK:
        print("[Naukri] Selenium unavailable. Run: pip install undetected-chromedriver")
        return []

    jobs   = []
    kw     = keywords.replace(" OR ", " ").replace(" ", "+")
    loc    = location.split(",")[0].strip().replace(" ", "+")
    driver = None
    try:
        driver = _make_driver()
        _load_session_cookies(driver, "naukri", "https://www.naukri.com")
        search_url = (
            f"https://www.naukri.com/jobs?k={kw}&l={loc}"
            f"&experience=0&freshness=1&sort=1&noOfResults={num_jobs}"
        )
        driver.get(search_url)

        wait = WebDriverWait(driver, 20)
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR,
                "article.jobTuple, div[class*='JobTuple'], "
                "[class*='job-tuple'], div[class*='cust-job']")))
        except Exception:
            print("[Naukri] Timed out — site may need login or CAPTCHA. Run cookie_harvester.py.")
            return []

        # Scroll to load more jobs
        for _ in range(4):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)

        cards = (
            driver.find_elements(By.CSS_SELECTOR, "article.jobTuple") or
            driver.find_elements(By.CSS_SELECTOR, "div[class*='JobTuple']") or
            driver.find_elements(By.CSS_SELECTOR, "[class*='job-tuple']") or
            driver.find_elements(By.CSS_SELECTOR, "div[class*='cust-job-tuple']")
        )[:num_jobs]

        for card in cards:
            try:
                title_el = _sel_first(card, [
                    "a.title", ".info a.title", "a[class*='title']",
                    "h2 a", "h3 a",
                ])
                if not title_el:
                    continue

                apply_url = title_el.get_attribute("href") or ""
                loc_text  = _sel_text(card, [
                    "li[class*='location']", ".ni-job-tuple-icon-srp-location",
                    ".loc-wrap", "[class*='location']",
                ])
                jobs.append({
                    "source":      "naukri",
                    "title":       title_el.text.strip(),
                    "company":     _sel_text(card, ["a.comp-name", ".comp-name", "[class*='comp-name']", "[class*='company-name']"]),
                    "location":    loc_text or location,
                    "url":         apply_url,
                    "description": _sel_text(card, [".job-description", ".jd-desc", "ul.tags-gt", "[class*='desc']"]),
                    "job_type":    _sel_text(card, [".job-type", "[class*='workType']", "[class*='employmentType']"]),
                    "date_posted": _sel_text(card, [".job-post-day", "[class*='freshness']", "[class*='date']", "time"]),
                    "is_remote":   "remote" in loc_text.lower(),
                    "salary_min":  _sel_text(card, [".salary .ni-job-tuple-icon-srp-rupee", ".salary", "[class*='salary']", "[class*='rupee']"]),
                    "salary_max":  "",
                })
            except Exception as exc:
                print(f"[Naukri] Card error: {exc}")
    except Exception as exc:
        print(f"[Naukri/Selenium] {exc}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    print(f"[Naukri] {len(jobs)} jobs found")
    return jobs


# ─── Frontend sync config ────────────────────────────────────────────────────
_FRONTEND_URL    = os.getenv("FRONTEND_URL", "").rstrip("/")
_SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "")
_SCRAPER_USER_ID = os.getenv("SCRAPER_USER_ID", "")


def _job_hash(job: dict) -> str:
    """SHA-256 of title+company+url for stable cross-cycle deduplication."""
    raw = f"{job.get('title', '').strip().lower()}|{job.get('company', '').strip().lower()}|{job.get('url', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _extract_skills(job: dict) -> list:
    """Return profile skills that appear in the job title or description."""
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    return [s for s in _SKILLS if s in text]


def _sync_to_frontend(jobs_list: list, cycle_id: str) -> dict:
    """
    POST scraped jobs to the Vercel frontend's /api/jobs/ingest endpoint.
    Requires FRONTEND_URL, SCRAPER_API_KEY, and SCRAPER_USER_ID in .env.
    """
    if not _FRONTEND_URL or not _SCRAPER_API_KEY or not _SCRAPER_USER_ID:
        print("[Sync] Skipped — FRONTEND_URL / SCRAPER_API_KEY / SCRAPER_USER_ID not set in .env")
        return {"skipped": True}

    payload = [
        {
            "title":       j.get("title", ""),
            "company":     j.get("company", ""),
            "location":    j.get("location", ""),
            "is_remote":   j.get("is_remote", False),
            "url":         j.get("url", ""),
            "source":      j.get("source", "unknown"),
            "description": j.get("description", ""),
            "date_posted": j.get("date_posted") or None,
            "skills":      _extract_skills(j),
            "match_score": j.get("resume_match_percent", 0),
            "hash":        _job_hash(j),
        }
        for j in jobs_list
        if j.get("title")  # skip empty entries
    ]

    if not payload:
        return {"skipped": True, "reason": "no valid jobs"}

    try:
        resp = requests.post(
            f"{_FRONTEND_URL}/api/jobs/ingest",
            json={"jobs": payload, "userId": _SCRAPER_USER_ID, "cycleId": cycle_id},
            headers={
                "Authorization": f"Bearer {_SCRAPER_API_KEY}",
                "Content-Type":  "application/json",
            },
            timeout=30,
        )
        result = resp.json() if resp.ok else {"error": resp.text[:200]}
        print(f"[Sync] {'OK' if resp.ok else 'FAILED'} — {result}")
        return result
    except Exception as exc:
        print(f"[Sync] Exception: {exc}")
        return {"error": str(exc)}


# ─── Custom scraper registry ──────────────────────────────────────────────────
_CUSTOM_SCRAPERS = {
    "wellfound": _scrape_wellfound,   # (keywords, location, num_jobs)
    "hirist":    _scrape_hirist,      # (keywords, num_jobs)
    "instahyre": _scrape_instahyre,   # (keywords, location, num_jobs)
    "naukri":    _scrape_naukri,      # (keywords, location, num_jobs) — Selenium, bypasses 406
}

ALL_SOURCES = JOBSPY_SOURCES | set(_CUSTOM_SCRAPERS)


# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/scrape", methods=["POST"])
def scrape():
    data = request.json or {}
    keywords          = data.get("keywords", "Full Stack Developer OR AI Engineer OR SDE")
    location          = data.get("location", "Hyderabad, India")
    num_jobs          = int(data.get("num_jobs", 100))
    hours_old         = int(data.get("hours_old", 24))        # DEFAULT: last 24 hours
    sources           = data.get("sources", DEFAULT_SOURCES)
    filter_by_profile = bool(data.get("filter_by_profile", True))
    min_score         = float(data.get("min_match_percent",
                              USER_PROFILE.get("min_match_percent", 8)))

    all_jobs: list       = []
    sources_status: dict = {}

    # ── JobSpy-native sources — called ONE AT A TIME so one failure doesn't kill others ──
    for source in sources:
        if source not in JOBSPY_SOURCES:
            continue
        kwargs: dict = {
            "site_name":      [source],
            "search_term":    keywords,
            "location":       location,
            "results_wanted": num_jobs,
            "hours_old":      hours_old,
            "verbose":        0,
        }
        if source == "indeed":
            kwargs["country_indeed"] = "India"
        if source == "linkedin":
            kwargs["linkedin_fetch_description"] = True
            li = _CREDS.get("linkedin", {})
            if li.get("username") and li.get("password"):
                kwargs["linkedin_username"] = li["username"]
                kwargs["linkedin_password"] = li["password"]
                print("[LinkedIn] Using saved credentials")
        try:
            df = scrape_jobs(**kwargs)
            if df is None or df.empty:
                sources_status[source] = "ok (0 found)"
                continue
            df = df.where(pd.notnull(df), None)
            batch = []
            for _, row in df.iterrows():
                batch.append({
                    "source":      str(row.get("site", source)),
                    "title":       str(row.get("title", "") or ""),
                    "company":     str(row.get("company", "") or ""),
                    "location":    str(row.get("location", "") or ""),
                    "url":         str(row.get("job_url", "")) if pd.notna(row.get("job_url")) else "",
                    "description": str(row.get("description", "") or "")[:3000],
                    "job_type":    str(row.get("job_type", "") or ""),
                    "date_posted": str(row.get("date_posted", "") or ""),
                    "is_remote":   bool(row.get("is_remote", False)),
                    "salary_min":  str(row.get("min_amount", "") or ""),
                    "salary_max":  str(row.get("max_amount", "") or ""),
                })
            all_jobs.extend(batch)
            sources_status[source] = f"ok ({len(batch)} found)"
        except Exception as exc:
            sources_status[source] = f"error: {exc}"
            print(f"[JobSpy/{source}] {exc}")

    # ── Custom scrapers ────────────────────────────────────────────────────────
    for name, fn in _CUSTOM_SCRAPERS.items():
        if name not in sources:
            continue
        try:
            results = fn(keywords, num_jobs) if name == "hirist" else fn(keywords, location, num_jobs)
            all_jobs.extend(results)
            sources_status[name] = f"ok ({len(results)} found)"
        except Exception as exc:
            sources_status[name] = f"error: {exc}"
            print(f"[{name}] {exc}")

    raw_count = len(all_jobs)

    # ── Deduplicate by URL ─────────────────────────────────────────────────────
    seen: set         = set()
    unique_jobs: list = []
    for job in all_jobs:
        url = job.get("url", "")
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        unique_jobs.append(job)

    dedup_count = len(unique_jobs)

    # ── Score each job against resume / profile ────────────────────────────────
    for job in unique_jobs:
        job["resume_match_percent"] = compute_resume_match(job)
        job["has_description"]      = len(job.get("description", "")) > 80

    # ── Filter by minimum match threshold ─────────────────────────────────────
    if filter_by_profile and min_score > 0:
        filtered = [j for j in unique_jobs if j["resume_match_percent"] >= min_score]
    else:
        filtered = unique_jobs

    # ── Sort best match first ──────────────────────────────────────────────────
    filtered.sort(key=lambda j: j["resume_match_percent"], reverse=True)

    # ── Sync to Vercel frontend ────────────────────────────────────────────────
    cycle_id    = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sync_result = _sync_to_frontend(filtered, cycle_id)

    return jsonify({
        "jobs":              filtered,
        "count":             len(filtered),
        "raw_count":         raw_count,
        "dedup_count":       dedup_count,
        "filtered_count":    len(filtered),
        "sources_status":    sources_status,
        "filter_applied":    filter_by_profile,
        "min_match_percent": min_score,
        "hours_old":         hours_old,
        "sync":              sync_result,
    })


@app.route("/sources", methods=["GET"])
def list_sources():
    return jsonify({
        "jobspy_native": sorted(JOBSPY_SOURCES),
        "custom":        sorted(_CUSTOM_SCRAPERS.keys()),
        "all":           sorted(ALL_SOURCES),
        "default":       DEFAULT_SOURCES,
    })


@app.route("/profile", methods=["GET"])
def get_profile():
    """Return the loaded profile + system state for debugging."""
    return jsonify({
        "profile":                USER_PROFILE,
        "skills_count":           len(_SKILLS),
        "titles_count":           len(_TITLES),
        "resume_loaded":          bool(_RESUME_TEXT),
        "resume_length_chars":    len(_RESUME_TEXT),
        "credentials_configured": {
            k: bool(v.get("username")) for k, v in _CREDS.items() if isinstance(v, dict)
        },
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "timestamp": datetime.utcnow().isoformat()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
