# Divoom Smart Desk

A Windows desktop dashboard for the Divoom TimeGate with five independently updated screens.

## Five-screen layout

| Screen | Content | Refresh |
|---|---|---|
| 1 — Clock & Weather | Tarneit time, date and live temperature | Every minute |
| 2 — Salah | Fajr, Dhuhr, Asr, Maghrib and Isha times | Every 12 hours |
| 3 — GIFs | Rotating local animated GIF playlist | GIF changes every 5 minutes |
| 4 — Knowledge | Rotating AI, technology and general facts | Every minute |
| 5 — Server Monitor | Live CPU, RAM and disk usage | Every 5 seconds |

## Prayer alerts

At each prayer time the app:

- Shows the prayer name on Screen 2 for 5 minutes.
- Returns to the Salah timetable automatically.
- Plays a 2-second Windows beep.

Prayer times are calculated for **Tarneit, Victoria** using the **Muslim World League** calculation with the **Shafi** Asr method.

## Requirements

- Windows 10 or Windows 11
- Python 3.10+
- A Divoom TimeGate on the same Wi-Fi/LAN network
- Internet access for prayer times and weather

## Installation

```powershell
cd d:\commission-quote-service
python -m pip install -r timegate_prayer_requirements.txt
python timegate_prayer_app.py
```

## First-time setup

1. Connect the PC and TimeGate to the same network.
2. Start the app.
3. Click **Discover** to find the TimeGate automatically.
4. If discovery fails, enter the device IP manually.

The app stores settings in `timegate_prayer_config.json`. It checks the configured address every 5 minutes and rediscovers the device when the address stops responding.

## Windows startup

`start_timegate_prayer.bat` starts the app in the background. It can also be placed in the Windows Startup folder or registered with Task Scheduler.

## GIF playlist

Add `.gif` files to `timegate_gifs/`.

- GIFs are resized to 128×128 for the TimeGate.
- The playlist rotates every 5 minutes.
- Animated frames are sent to Screen 3.
- Four sample animations are included.

## Main files

- `timegate_prayer_app.py` — desktop app and five-screen dashboard
- `timegate_prayer_config.json` — location and TimeGate settings
- `timegate_prayer_requirements.txt` — Python dependencies
- `start_timegate_prayer.bat` — Windows startup launcher
- `timegate_gifs/` — animated GIF playlist

## API compatibility

The TimeGate is controlled through its unofficial local HTTP API. Custom displays use 128×128 JPEG frames and the `Draw/SendHttpGif` command. Keep the PC and TimeGate on the same LAN, and do not expose port 80 publicly.
