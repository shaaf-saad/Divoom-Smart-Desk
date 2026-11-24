# Divoom Smart Desk

A Windows desktop dashboard for the Divoom TimeGate with five independently updated screens.

## Five-screen layout

| Screen | Content | Refresh |
|---|---|---|
| 1 — Clock & Weather | Large clock, temperature and sunny/cloudy/rainy artwork | Every minute |
| 2 — Salah | Fajr, Dhuhr, Asr, Maghrib and Isha times | Every 12 hours |
| 3 — Hadith | Rotating Hadith cards | Changes every 5 minutes |
| 4 — Pi-hole | 24-hour blocked percentage, ads blocked, queries and clients | Every minute |
| 5 — Server Monitor | Large CPU/GPU usage and temperature, RAM and disk usage | Every 5 seconds |

Custom Screens 1–4 share a high-contrast neon theme with cyan borders, blue separators, white primary text, teal values and yellow accents.

## Prayer alerts

At each prayer time the app:

- Shows the prayer name on Screen 2 for 5 minutes.
- Returns to the Salah timetable automatically.
- Plays a 2-second Windows beep.

Prayer times are calculated for **Tarneit, Victoria** using the **Muslim World League** calculation with the **Shafi** Asr method.

## Requirements

- Windows 10/11 or Linux with a graphical desktop session
- Python 3.10+
- A Divoom TimeGate on the same Wi-Fi/LAN network
- Internet access for prayer times and weather
- Pi-hole FTL running locally for Screen 4 (or set `PIHOLE_FTL_DB` to its read-only database path)

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

The read-only five-screen preview is served on `127.0.0.1:8090` for authenticated reverse-proxy access. It exposes cached JPEG previews only; it does not proxy the Divoom API or accept control commands. Install the Open WebUI-authenticated `https://shaafsaad.com/timegate/` route with `sudo ./deploy/install-timegate-nginx.sh`.

Device refreshes are serialized so the clock update, Salah timetable, and Hadith display cannot overwrite one another during morning startup.

## Windows startup

`start_timegate_prayer.bat` starts the app in the background. It can also be placed in the Windows Startup folder or registered with Task Scheduler.

## Linux startup

Run `./start_timegate_prayer.sh` manually, or use the GNOME desktop entry installed at `~/.config/autostart/divoom-timegate.desktop`. The launcher prevents duplicate instances, logs to `~/.local/state/divoom-timegate/dashboard.log`, and restarts the dashboard after a crash.

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
