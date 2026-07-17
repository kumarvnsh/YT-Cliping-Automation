"""One-time local run: prints the YouTube refresh token for GH secrets.

Usage: YT_CLIENT_ID=... YT_CLIENT_SECRET=... python get_token.py
Prereq: OAuth client of type 'Desktop app' in Google Cloud Console,
YouTube Data API v3 enabled.
"""
import os
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_config(
    {"installed": {
        "client_id": os.environ["YT_CLIENT_ID"],
        "client_secret": os.environ["YT_CLIENT_SECRET"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"}},
    scopes=["https://www.googleapis.com/auth/youtube.upload"])
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print("\nYT_REFRESH_TOKEN =", creds.refresh_token)
