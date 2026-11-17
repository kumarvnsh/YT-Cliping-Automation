"""Authorize analytics separately; refuse credentials for the wrong channel."""
import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parent
SCOPES = ['https://www.googleapis.com/auth/youtube.readonly',
          'https://www.googleapis.com/auth/yt-analytics.readonly']


def main():
    expected = json.loads((ROOT / 'channel.json').read_text(encoding='utf-8'))
    flow = InstalledAppFlow.from_client_secrets_file(str(ROOT / 'client_secret.json'), SCOPES)
    credentials = flow.run_local_server(port=0, access_type='offline', prompt='consent', timeout_seconds=600,
        success_message='Google sign-in received. Check the dashboard for verified channel status.')
    channel = build('youtube', 'v3', credentials=credentials).channels().list(part='id,snippet', mine=True).execute().get('items', [])
    if len(channel) != 1 or channel[0]['id'] != expected['channel_id']:
        raise ValueError('Wrong channel selected. Shotsnip authorization was not saved. Choose Shotsnip and try again.')
    if not credentials.refresh_token:
        raise ValueError('Google returned no offline token; authorize again.')
    (ROOT / 'analytics_token.json').write_text(credentials.to_json(), encoding='utf-8')
    print('Verified Shotsnip. Analytics authorization saved locally. No upload permissions changed.')
    from sync_analytics import sync
    sync()


if __name__ == '__main__':
    main()
