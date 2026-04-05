
# TALLK - THE NEXT GEN CHATTING APP

> [!IMPORTANT]
> Install Python first, then run `py -3.14 -m pip install paho-mqtt sounddevice` before using voice calls.

Tallk is a simple chat and voice-call app.

## Run

```powershell
py -3.14 tallk.py
```

## How It Works

1. Open Tallk.
2. Enter your display name in the login popup
3. Click someone's name to voice call them.

## Notes

- `paho-mqtt` is required for chat and presence.
- `sounddevice` is required for live voice calls.
- `sounds/call.wav` should be included when you package or share the app so the ringtone works as expected.

