"""Public-client OAuth; tokens encrypted by Windows DPAPI, never logged."""
import json
from pathlib import Path
import sys
import time
import requests

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,'D:/Clips-Kitty')
from core import secrets


def credentials():
    token=secrets.load(ROOT/'work/twitch','twitch')
    if not token:
        return None
    if token.get('expires_at',0)<time.time()+60:
        response=requests.post('https://id.twitch.tv/oauth2/token',data={
            'grant_type':'refresh_token','refresh_token':token['refresh_token'],
            'client_id':token['client_id']},timeout=20)
        if response.status_code!=200:
            raise RuntimeError('Twitch connection needs renewal')
        token.update(response.json()); token['expires_at']=time.time()+token['expires_in']
        secrets.save(ROOT/'work/twitch','twitch',token)
    if time.time()-token.get('validated_at',0)>3600:
        response=requests.get('https://id.twitch.tv/oauth2/validate',headers={
            'Authorization':'OAuth '+token['access_token']},timeout=20)
        if response.status_code!=200:
            raise RuntimeError('Twitch connection needs renewal')
        identity=response.json()
        if identity.get('client_id')!=token['client_id'] or identity.get('user_id')!=token['user_id']:
            raise RuntimeError('Twitch account identity changed')
        token['validated_at']=time.time()
        secrets.save(ROOT/'work/twitch','twitch',token)
    return token


def connect(client_id):
    response=requests.post('https://id.twitch.tv/oauth2/device',data={'client_id':client_id,'scopes':'chat:read'},timeout=20)
    response.raise_for_status()
    device=response.json()
    print('Open '+device['verification_uri']+' and enter '+device['user_code'],flush=True)
    deadline=time.time()+device['expires_in']
    while time.time()<deadline:
        time.sleep(max(5,device.get('interval',5)))
        response=requests.post('https://id.twitch.tv/oauth2/token',data={
            'client_id':client_id,'device_code':device['device_code'],
            'grant_type':'urn:ietf:params:oauth:grant-type:device_code'},timeout=20)
        token=response.json()
        if response.status_code==200:
            valid=requests.get('https://id.twitch.tv/oauth2/validate',headers={
                'Authorization':'OAuth '+token['access_token']},timeout=20)
            valid.raise_for_status(); identity=valid.json()
            if identity.get('client_id')!=client_id or 'chat:read' not in identity.get('scopes',[]):
                raise RuntimeError('Twitch returned an unexpected account permission')
            token.update(client_id=client_id,login=identity['login'],user_id=identity['user_id'],
                         expires_at=time.time()+token['expires_in'])
            secrets.save(ROOT/'work/twitch','twitch',token)
            status={'status':'connected','login':identity['login'],'scope':'chat:read',
                    'checked_at':time.time()}
            folder=ROOT/'work/dashboard'; folder.mkdir(parents=True,exist_ok=True)
            (folder/'twitch-identity.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
            print('Twitch connected: '+identity['login']+' (read-only chat)',flush=True)
            return status
        message=token.get('message',token.get('error',''))
        if message not in ('authorization_pending','slow_down'):
            raise RuntimeError('Twitch sign-in was refused or expired')
    raise TimeoutError('Twitch sign-in expired')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-id',required=True)
    connect(parser.parse_args().client_id)
