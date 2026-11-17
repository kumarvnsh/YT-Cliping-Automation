# Twitch connection

Public stream playback works without an account. Read-only chat is now connected and tested on this PC: the live IRC connection received two messages from Maxylobes with no errors. No chat messages are sent by the tool. Detection uses unique chatter counts in memory; names and message text are not stored.

`twitch_auth.py --client-id PUBLIC_ID` runs Twitch's official device authorization flow requesting only `chat:read`. It validates the account and client before saving anything. `work/twitch/credentials/twitch.bin` is Windows DPAPI encrypted; never copy or print it. Refresh and hourly validation happen locally. Public connection status is exported to the dashboard. Revoking the application from Twitch requires reconnecting.

The WebSocket transport sends PASS, NICK and JOIN in separate frames; combining them caused the previous connection failures. The account uses no chat-write permission.
