"""Authorize the local uploader without printing credentials."""
from pathlib import Path
import json
from datetime import datetime, timezone
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parent
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]

def main():
    flow = InstalledAppFlow.from_client_secrets_file(str(ROOT / "client_secret.json"), SCOPES)
    creds = flow.run_local_server(
        host="localhost", port=0, access_type="offline", prompt="consent",
        timeout_seconds=600,
        authorization_prompt_message="Open this Google sign-in link if the browser did not open: {url}",
        success_message="YouTube authorization complete. You can close this tab.",
    )
    if not creds.refresh_token:
        raise RuntimeError("No offline token returned. Please authorize again.")
    expected = json.loads((ROOT / 'channel.json').read_text(encoding='utf-8'))
    channels = build('youtube','v3',credentials=creds,cache_discovery=False).channels().list(part='id,snippet',mine=True).execute().get('items',[])
    if len(channels) != 1 or channels[0]['id'] != expected['channel_id']:
        raise RuntimeError('Wrong upload channel. Credentials were not saved; choose Shotsnip.')
    (ROOT / "token.json").write_text(creds.to_json(), encoding="utf-8")
    folder = ROOT/'work/dashboard'
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'upload-identity.json').write_text(json.dumps({'status':'verified','channel_id':channels[0]['id'],
        'name':channels[0]['snippet']['title'],'checked_at':datetime.now(timezone.utc).isoformat()},indent=2),encoding='utf-8')
    print("Authorization saved locally to token.json. No video was uploaded.")

if __name__ == "__main__":
    main()
