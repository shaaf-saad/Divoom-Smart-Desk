# Tarneit Prayer TimeGate App

A Windows desktop app that:

- Loads daily prayer times for Tarneit, Melbourne from Aladhan.
- Uses the Australian-friendly calculation method (MWL; school: Shafi by default).
- Automatically discovers the Divoom TimeGate on your Wi-Fi network.
- Displays the prayer name on the center panel for 5 minutes.
- Restores the clock panel after the alert.
- Plays a two-tone Windows chime.

## Run on Windows

```powershell
py -m pip install -r timegate_prayer_requirements.txt
py timegate_prayer_app.py
```

Keep the app open while Windows is running. Prayer times are refreshed once per day and at midnight.

## TimeGate setup

1. Connect the TimeGate and Windows PC to the same Wi-Fi network.
2. Open the app.
3. Click **Discover**.
4. If discovery cannot find it, enter the TimeGate IP address manually.

The app stores its settings in `timegate_prayer_config.json` beside the script.

## API compatibility note

The TimeGate community HTTP API is unofficial and custom text support varies by firmware. The app uses the `/post` text and clock commands documented for the TimeGate. If text does not appear after a firmware update, the app status will still show the alert, and device firmware/app-channel settings may need to be reset through the Divoom app.
