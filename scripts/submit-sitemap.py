#!/opt/data/profiles/clickbust/scripts/.venv/bin/python3
"""Submit sitemap.xml to Google Search Console."""
import json
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/webmasters"]
SERVICE_ACCOUNT_FILE = "/opt/data/secrets/gsc-service-account.json"
SITE_URL = "sc-domain:clickbust.cybr.fi"
SITEMAP_URL = "https://clickbust.cybr.fi/sitemap.xml"

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)

service = build("searchconsole", "v1", credentials=credentials)

# Check current sitemaps
print("Checking current sitemaps...")
try:
    sitemaps = service.sitemaps().list(siteUrl=SITE_URL).execute()
    if "sitemap" in sitemaps:
        for s in sitemaps["sitemap"]:
            print(f"  {s['path']} — submitted: {s.get('lastDownloaded', 'N/A')}, errors: {s.get('errors', 0)}, warnings: {s.get('warnings', 0)}")
    else:
        print("  No sitemaps found")
except Exception as e:
    print(f"  Error listing sitemaps: {e}")

# Submit the sitemap
print(f"\nSubmitting sitemap: {SITEMAP_URL}...")
try:
    result = service.sitemaps().submit(
        siteUrl=SITE_URL,
        feedpath=SITEMAP_URL,
    ).execute()
    print(f"  Submitted successfully: {result}")
except Exception as e:
    print(f"  Error submitting: {e}")

print("\nDone!")