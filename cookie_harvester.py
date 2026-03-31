"""
cookie_harvester.py — Run this ONCE to save your browser sessions.

How it works:
  1. Opens each selected site in a real Chrome browser window.
  2. You log in however you normally do — Google button, email+password, anything.
  3. After you press ENTER the script saves your session cookies to session_cookies/.
  4. The scraper loads those cookies automatically from that point on.

Usage:
  python cookie_harvester.py

Google auth users: this is your solution. No passwords needed in credentials.json.
"""

import os
import pickle
import time

try:
    import undetected_chromedriver as uc
except ImportError:
    print("ERROR: undetected_chromedriver not installed.")
    print("       Run: pip install undetected-chromedriver")
    raise SystemExit(1)

_DIR         = os.path.dirname(os.path.abspath(__file__))
_COOKIES_DIR = os.path.join(_DIR, "session_cookies")
os.makedirs(_COOKIES_DIR, exist_ok=True)

# ─── Sites to authenticate ────────────────────────────────────────────────────
SITES = {
    "linkedin":  "https://www.linkedin.com/login",
    "naukri":    "https://www.naukri.com/nlogin/login",
    "glassdoor": "https://www.glassdoor.co.in/profile/login_input.htm",
    "wellfound": "https://wellfound.com/login",
    "instahyre": "https://www.instahyre.com/candidate/login/",
    "hirist":    "https://www.hirist.tech/login",
    "indeed":    "https://secure.indeed.com/account/login",
}

# ─── Site status check ────────────────────────────────────────────────────────
def _cookie_status(site_name: str) -> str:
    path = os.path.join(_COOKIES_DIR, f"{site_name}.pkl")
    if not os.path.exists(path):
        return "✗ not saved"
    age_hours = (time.time() - os.path.getmtime(path)) / 3600
    return f"✓ saved  ({age_hours:.0f}h ago)"


# ─── Harvest one site ─────────────────────────────────────────────────────────
def harvest(site_name: str, login_url: str) -> bool:
    print(f"\n{'='*65}")
    print(f"  Site: {site_name.upper()}")
    print(f"  URL:  {login_url}")
    print(f"{'='*65}")
    print("  A Chrome browser window will open.")
    print("  \u2192 Log in (Google button, email+password, or any method).")
    print("  \u2192 Wait until you see your dashboard / job listings.")
    print("  \u2192 Then come back here and press ENTER.")
    print()

    options = uc.ChromeOptions()
    profile_dir = os.path.join(_DIR, "chrome_profile")
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--lang=en-US")
    # Headed mode — user must see the browser to log in
    driver = uc.Chrome(options=options, use_subprocess=True, version_main=146)

    try:
        driver.get(login_url)
        input(f"  [ {site_name} ] Press ENTER after you have fully logged in: ")
        time.sleep(2)   # Allow any post-login redirects to settle

        cookies = driver.get_cookies()
        cookie_path = os.path.join(_COOKIES_DIR, f"{site_name}.pkl")
        with open(cookie_path, "wb") as f:
            pickle.dump(cookies, f)
        print(f"  \u2713 {len(cookies)} cookies saved \u2192 {cookie_path}")
        return True
    except KeyboardInterrupt:
        print(f"  Skipped {site_name}.")
        return False
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return False
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print()
    print("  JobFlow AI \u2014 Cookie Harvester")
    print("  Run this once. The scraper will reuse saved sessions automatically.")
    print()

    sites_list = list(SITES.items())

    # Show current status
    print("  Current cookie status:")
    for i, (name, _) in enumerate(sites_list, 1):
        print(f"    {i}. {name:<12} {_cookie_status(name)}")

    print()
    print("  Enter site numbers to (re-)authenticate, separated by commas.")
    print("  Or type 'all' to do all sites. Or press ENTER to quit.")
    print()
    choice = input("  Your choice: ").strip().lower()

    if not choice:
        print("  Nothing selected. Exiting.")
        return

    if choice == "all":
        selected = sites_list
    else:
        indices = []
        for part in choice.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(sites_list):
                    indices.append(idx)
                else:
                    print(f"  Warning: {part} is out of range, skipped.")
        selected = [sites_list[i] for i in indices]

    if not selected:
        print("  No valid sites selected. Exiting.")
        return

    success = 0
    for name, url in selected:
        if harvest(name, url):
            success += 1

    print()
    print(f"  Done. {success}/{len(selected)} sites authenticated.")
    print(f"  Cookies saved to: {_COOKIES_DIR}")
    print()
    print("  You can now run:  python scraper.py")
    print()


if __name__ == "__main__":
    main()
