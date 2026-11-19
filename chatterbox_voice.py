"""Chatterbox TTS worker. Runs inside the separate Chatterbox venv (torch + chatterbox-tts).

Usage: python chatterbox_voice.py request.json
request: {"text": str, "output": wav path, "exaggeration": float, "cfg_weight": float, "seed": int}
Uses Chatterbox's built-in voice only; never clones a real person's voice.
"""
import json
import sys


def main(request_path):
    import torch
    import torchaudio
    from chatterbox.tts import ChatterboxTTS

    request = json.loads(open(request_path, encoding='utf-8').read())
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    torch.manual_seed(int(request.get('seed', 7)))
    model = ChatterboxTTS.from_pretrained(device=device)
    wav = model.generate(request['text'], exaggeration=float(request.get('exaggeration', 0.5)),
                         cfg_weight=float(request.get('cfg_weight', 0.5)))
    if wav.numel() == 0 or not torch.isfinite(wav).all():
        raise SystemExit('Chatterbox returned empty or invalid audio')
    torchaudio.save(request['output'], wav.cpu(), model.sr)
    print(json.dumps({'device': device, 'sample_rate': model.sr}))


if __name__ == '__main__':
    main(sys.argv[1])
