
# TALLK - THE NEXT GEN CHATTING APP

> [!IMPORTANT]
> Install Python first, then run `py -3.14 -m pip install paho-mqtt sounddevice` before using voice calls.

Tallk is a simple chat and voice-call app.

## Run

```powershell
py -3.14 tallk.py
```

## Run The Web App

Serve the repo over HTTP:

```powershell
py -3.14 -m http.server 8000
```

Then open `http://localhost:8000/web/`

The web app uses the same Tallk broker host, room, and MQTT topics as the Python app so both versions can chat and call each other. In the browser, that broker connection uses MQTT over WebSockets.

## How It Works

1. Open Tallk.
2. Enter your display name in the login popup
3. Click someone's name to voice call them.

## Notes

- `paho-mqtt` is required for chat and presence.
- `sounddevice` is required for live voice calls.





Also, it was mostly created by ChatGPT Codex, so give Codex a round of applause.
