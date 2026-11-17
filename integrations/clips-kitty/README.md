# Clips Kitty local integration

Upstream: https://github.com/ColinGPT9/clips-studio

Tested revision: `56a571faadd244926d1abdae5843d413af8665f9`.

The engine runs separately at `D:/Clips-Kitty`, with its API on loopback port 8765. This directory preserves the local configuration and FFmpeg compatibility patch. The AGPL-3.0 license is included; the engine source remains in the upstream repository.

Copy `shotsnip.yaml` to `D:/Clips-Kitty/config/shotsnip.yaml`. From the engine checkout, apply `ffmpeg-9.patch` using `git apply`. Install the upstream requirements in its own virtual environment with `av<19`, fetch FFmpeg using `scripts/fetch_ffmpeg.py`, and provide the Whisper small weights. See `docs/scheduled-posting.md` for operation. Do not place credentials here.
