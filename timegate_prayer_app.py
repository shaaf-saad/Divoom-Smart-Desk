import base64
import ipaddress
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import time
import sys
import threading
import psutil
import subprocess
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk, messagebox
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont

try:
    import winsound
except ImportError:
    winsound = None

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "timegate_prayer_config.json")
DEFAULT_CONFIG = {
    "device_ip": "",
    "local_token": "",
    "city": "Tarneit",
    "country": "Australia",
    "latitude": -37.8368,
    "longitude": 144.6596,
    "timezone": "Australia/Melbourne",
    "calculation_method": 3,
    "asr_method": 0,
    "alert_seconds": 300,
}

PRAYERS = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")
PIHOLE_DB = os.environ.get("PIHOLE_FTL_DB", "/opt/pihole/etc-pihole/pihole-FTL.db")
PIHOLE_BLOCKED_STATUSES = (1, 4, 5, 6, 7, 8, 9, 10, 11, 18)
DEVICE_API_LOCK = threading.Lock()
DEVICE_REFRESH_LOCK = threading.Lock()

HADITH_CARDS = (
    ("INTENTION", "Actions are judged by intentions."),
    ("GOODNESS", "Whoever believes in Allah and the Last Day should speak good or remain silent."),
    ("CHARITY", "Charity does not decrease wealth."),
    ("COURTESY", "The best among you are those with the best character."),
    ("BROTHERHOOD", "Do not envy, hate, or turn away from one another."),
    ("NEIGHBOR", "Do not forget the rights of your neighbor."),
    ("SMILE", "Your smile for your brother is charity."),
    ("REMEMBER", "Remember Allah often."),
    ("EASE", "Make things easy and do not make them difficult."),
    ("THANKFUL", "A grateful person is pleased with little."),
    ("MERCY", "Those who show mercy are shown mercy."),
    ("WORK", "The best provision is righteous conduct."),
)

NEON_BACKGROUND = (4, 10, 18)
NEON_CYAN = (0, 255, 235)
NEON_BLUE = (36, 126, 255)
NEON_TEAL = (0, 255, 170)
NEON_YELLOW = (255, 190, 80)
NEON_WHITE = (235, 255, 255)


def is_timegate_response(data):
    return (
        isinstance(data, dict)
        and data.get("error_code") == 0
        and ("UTCTime" in data or "LocalTime" in data)
    )


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as file:
                return {**DEFAULT_CONFIG, **json.load(file)}
        except (OSError, json.JSONDecodeError):
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=2)


def load_bold_fonts(*sizes):
    for font_path in (
        "DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "arialbd.ttf",
    ):
        try:
            return [ImageFont.truetype(font_path, size) for size in sizes]
        except OSError:
            continue
    return [ImageFont.load_default()] * len(sizes)


def draw_neon_frame(draw, title, title_font):
    draw.rectangle((2, 2, 125, 125), outline=(8, 54, 72), width=2)
    draw.rectangle((5, 5, 122, 122), outline=NEON_CYAN, width=2)
    for x, y, x_end, y_end in (
        (6, 6, 22, 6),
        (106, 6, 121, 6),
        (6, 6, 6, 22),
        (121, 6, 121, 22),
        (6, 121, 22, 121),
        (106, 121, 121, 121),
        (6, 106, 6, 121),
        (121, 106, 121, 121),
    ):
        draw.line((x, y, x_end, y_end), fill=NEON_YELLOW, width=2)
    draw.line((10, 29, 118, 29), fill=NEON_BLUE, width=1)
    draw.text(
        (64, 8),
        title,
        fill=NEON_WHITE,
        font=title_font,
        anchor="mt",
    )


class TimeGateClient:
    def __init__(self, ip):
        self.ip = ip
        self.config = load_config()
        self.local_token = self.config.get("local_token", "")

    @property
    def url(self):
        return f"http://{self.ip}:80/post"

    def send(self, payload, timeout=10):
        if not self.ip:
            return False
        local_token = getattr(self, "local_token", None)
        if local_token:
            payload = {**payload, "LocalToken": int(local_token)}
        with DEVICE_API_LOCK:
            response = requests.post(self.url, json=payload, timeout=timeout)
        response.raise_for_status()
        try:
            result = response.json()
        except ValueError as exc:
            raise RuntimeError("TimeGate returned a non-JSON response") from exc
        if not isinstance(result, dict) or result.get("error_code") != 0:
            raise RuntimeError(f"TimeGate rejected {payload['Command']}: {result}")
        return True

    def set_brightness(self, brightness):
        return self.send({"Command": "Channel/SetBrightness", "Brightness": brightness})

    def screen_on(self):
        return self.send({"Command": "Channel/OnOffScreen", "OnOff": 1})

    @staticmethod
    def get_amd_gpu_utilization(drm_path="/sys/class/drm"):
        for utilization_file in Path(drm_path).glob("card*/device/gpu_busy_percent"):
            try:
                return float(utilization_file.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                continue
        return None

    def show_clock(self, panel=3, clock_id=50):
        self.send({"Command": "Draw/ResetHttpGifId"})
        return self.send({
            "Command": "Channel/SetClockSelectId",
            "ClockId": clock_id,
            "LcdIndex": panel,
            "LcdIndependence": [0, 0, 1, 0, 0],
        })

    def show_image(self, image, panel, tag="", reset_cache=True):
        if reset_cache:
            self.screen_on()
        if reset_cache:
            self.send({"Command": "Draw/ResetHttpGifId"})
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        pic_data = base64.b64encode(buffer.getvalue()).decode("ascii")
        lcd_array = [1 if index == panel - 1 else 0 for index in range(5)]
        return self.send({
            "Command": "Draw/SendHttpGif",
            "LcdArray": lcd_array,
            "PicNum": 1,
            "PicWidth": 128,
            "PicOffset": 0,
            "PicID": int(time.time() * 1000) + panel,
            "PicSpeed": 100,
            "PicData": pic_data,
        })

    def show_clock_weather(self, panel=1):
        now = datetime.now(ZoneInfo("Australia/Melbourne"))
        temperature = "--"
        condition = "WEATHER"
        try:
            response = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": self.config["latitude"],
                    "longitude": self.config["longitude"],
                    "current": "temperature_2m,weather_code",
                    "timezone": "Australia/Melbourne",
                },
                timeout=8,
            )
            response.raise_for_status()
            current = response.json()["current"]
            temperature = f"{round(current['temperature_2m'])}"
            code = int(current.get("weather_code", 0))
            condition = {
                0: "CLEAR",
                1: "MAINLY CLEAR",
                2: "PARTLY CLOUDY",
                3: "CLOUDY",
                45: "FOG",
                51: "DRIZZLE",
                61: "RAIN",
                71: "SNOW",
                95: "STORM",
            }.get(code, "WEATHER")
        except Exception:
            pass

        image = Image.new("RGB", (128, 128), NEON_BACKGROUND)
        draw = ImageDraw.Draw(image)
        time_font, temperature_font, condition_font, detail_font, title_font = load_bold_fonts(
            38, 24, 14, 10, 10
        )

        if condition in ("CLEAR", "MAINLY CLEAR", "PARTLY CLOUDY"):
            draw.ellipse((79, 36, 133, 90), fill=(73, 57, 8))
            draw.ellipse((88, 45, 124, 81), fill=(255, 188, 38))
        if condition not in ("CLEAR", "MAINLY CLEAR"):
            draw.ellipse((81, 61, 112, 89), fill=(48, 65, 79))
            draw.ellipse((99, 49, 132, 86), fill=(59, 79, 94))
            draw.rectangle((89, 63, 119, 86), fill=(59, 79, 94))
        if condition in ("DRIZZLE", "RAIN", "STORM"):
            for x in (87, 100, 113):
                draw.line((x, 87, x - 5, 100), fill=(42, 170, 255), width=3)
        if condition in ("FOG", "SNOW"):
            for y in (73, 83, 93):
                draw.line((78, y, 126, y), fill=(170, 190, 200), width=2)

        draw_neon_frame(draw, "CLOCK / WEATHER", title_font)
        draw.text((64, 22), now.strftime("%I:%M"), font=time_font, fill=NEON_WHITE, anchor="mt", stroke_width=2, stroke_fill=NEON_BLUE)
        draw.text((64, 59), now.strftime("%a %d %b").upper(), font=detail_font, fill=(177, 220, 224), anchor="mt")
        draw.text((10, 77), condition, font=condition_font, fill=NEON_YELLOW)
        draw.text((10, 96), f"{temperature}°C", font=temperature_font, fill=NEON_WHITE, stroke_width=1, stroke_fill=NEON_BLUE)
        return self.show_image(image, panel)

    def show_gif(self, panel=3):
        gif_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "timegate_gifs")
        gif_files = []
        if os.path.isdir(gif_folder):
            gif_files = [
                os.path.join(gif_folder, name)
                for name in os.listdir(gif_folder)
                if name.lower().endswith(".gif")
            ]

        if not gif_files:
            image = Image.new("RGB", (128, 128), (18, 8, 28))
            draw = ImageDraw.Draw(image)
            try:
                title = ImageFont.truetype("arial.ttf", 17)
                text = ImageFont.truetype("arial.ttf", 12)
            except OSError:
                title = ImageFont.load_default(); text = ImageFont.load_default()
            draw.text((20, 24), "GIF SLOT", fill=(255,255,255), font=title)
            draw.text((12, 58), "Add GIF files to", fill=(0,255,170), font=text)
            draw.text((12, 76), "timegate_gifs", fill=(255,190,80), font=text)
            return self.show_image(image, panel)

        gif_path = gif_files[(int(time.time()) // 300) % len(gif_files)]
        try:
            with Image.open(gif_path) as source:
                source.seek(0)
                frames = []
                for frame_index in range(min(getattr(source, "n_frames", 1), 40)):
                    source.seek(frame_index)
                    frame = source.convert("RGB").resize((128, 128), Image.Resampling.NEAREST)
                    buffer = io.BytesIO()
                    frame.save(buffer, format="JPEG", quality=85)
                    frames.append(base64.b64encode(buffer.getvalue()).decode("ascii"))

            self.screen_on()
            self.send({"Command": "Draw/ResetHttpGifId"})
            lcd_array = [1 if index == panel - 1 else 0 for index in range(5)]
            pic_id = int(time.time()) + panel
            for frame_index, pic_data in enumerate(frames):
                self.send({
                    "Command": "Draw/SendHttpGif",
                    "LcdArray": lcd_array,
                    "PicNum": len(frames),
                    "PicWidth": 128,
                    "PicOffset": frame_index,
                    "PicID": pic_id,
                    "PicSpeed": 200,
                    "PicData": pic_data,
                }, timeout=10)
            return True
        except Exception:
            return False

    def show_hadith(self, panel=3):
        card_index = (int(time.time()) // 300) % len(HADITH_CARDS)
        title, text = HADITH_CARDS[card_index]

        image = Image.new("RGB", (128, 128), NEON_BACKGROUND)
        draw = ImageDraw.Draw(image)
        title_font, text_font, footer_font = load_bold_fonts(13, 11, 9)
        draw_neon_frame(draw, title, title_font)

        words = text.split()
        lines = []
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if draw.textbbox((0, 0), candidate, font=text_font)[2] > 104:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)

        y = 38
        for line in lines[:5]:
            draw.text((12, y), line, fill=NEON_WHITE, font=text_font)
            y += 15

        minutes = (300 - (int(time.time()) % 300)) // 60
        draw.text((64, 111), f"NEW IN {minutes} MIN", fill=NEON_YELLOW, font=footer_font, anchor="mt")
        return self.show_image(image, panel)

    @staticmethod
    def load_pihole_stats(db_path=PIHOLE_DB, now=None):
        since = int(time.time() if now is None else now) - 24 * 60 * 60
        placeholders = ",".join("?" for _ in PIHOLE_BLOCKED_STATUSES)
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3) as connection:
            total, blocked, clients = connection.execute(
                f"""
                SELECT
                    COUNT(*),
                    COALESCE(SUM(CASE WHEN status IN ({placeholders}) THEN 1 ELSE 0 END), 0),
                    COUNT(DISTINCT client)
                FROM query_storage
                WHERE timestamp >= ?
                """,
                (*PIHOLE_BLOCKED_STATUSES, since),
            ).fetchone()
        return {
            "total": total,
            "blocked": blocked,
            "clients": clients,
            "percentage": (blocked / total * 100) if total else 0,
        }

    @staticmethod
    def format_count(value):
        if value < 1000:
            return str(value)
        if value < 1_000_000:
            return f"{value / 1000:.1f}K"
        return f"{value / 1_000_000:.1f}M"

    def show_pihole(self, panel=4):
        offline = False
        try:
            stats = self.load_pihole_stats()
        except (OSError, sqlite3.Error) as exc:
            stats = {"total": 0, "blocked": 0, "clients": 0, "percentage": 0}
            offline_cause = exc
            offline = True

        image = Image.new("RGB", (128, 128), NEON_BACKGROUND)
        draw = ImageDraw.Draw(image)
        title, percent, text, detail = load_bold_fonts(16, 31, 12, 10)
        draw_neon_frame(draw, "PI-HOLE", title)
        if offline:
            draw.text((64, 48), "OFFLINE", fill=(255, 105, 105), font=percent, anchor="mt")
            draw.text((64, 96), "FTL UNAVAILABLE", fill=(220, 235, 240), font=detail, anchor="mt")
        else:
            draw.text(
                (64, 35),
                f"{stats['percentage']:.1f}%",
                fill=NEON_YELLOW,
                font=percent,
                anchor="mt",
                stroke_width=1,
                stroke_fill=NEON_BLUE,
            )
            draw.text((64, 72), "BLOCKED", fill=NEON_YELLOW, font=text, anchor="mt")
            draw.text(
                (8, 91),
                f"{self.format_count(stats['blocked'])} ADS",
                fill=NEON_TEAL,
                font=text,
            )
            draw.text(
                (8, 106),
                f"{self.format_count(stats['total'])} QUERIES",
                fill=(220, 235, 240),
                font=detail,
            )
            draw.text(
                (8, 113),
                f"{stats['clients']} CLIENTS / 24H",
                fill=(220, 235, 240),
                font=detail,
            )
        self.show_image(image, panel)
        if offline:
            raise RuntimeError("Pi-hole FTL database unavailable") from offline_cause
        return True

    def show_server_monitor(self, panel=5):
        psutil.cpu_percent(interval=None)
        cpu = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        gpu_percent = None
        cpu_temperature = None
        gpu_temperature = None
        try:
            temperatures = psutil.sensors_temperatures()
            for sensor_name in ("coretemp", "k10temp", "cpu_thermal"):
                readings = temperatures.get(sensor_name)
                valid_readings = [
                    reading.current for reading in readings or [] if reading.current is not None
                ]
                if valid_readings:
                    cpu_temperature = max(valid_readings)
                    break
            for sensor_name in ("amdgpu", "nvidia"):
                readings = temperatures.get(sensor_name)
                valid_readings = [
                    reading.current for reading in readings or [] if reading.current is not None
                ]
                if valid_readings:
                    gpu_temperature = max(valid_readings)
                    break
        except (AttributeError, NotImplementedError, OSError):
            pass
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0 and result.stdout.strip():
                utilization, temperature = (
                    result.stdout.strip().splitlines()[0].split(",", maxsplit=1)
                )
                gpu_percent = float(utilization.strip())
                gpu_temperature = float(temperature.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass
        if gpu_percent is None:
            gpu_percent = self.get_amd_gpu_utilization()

        image = Image.new("RGB", (128, 128), NEON_BACKGROUND)
        draw = ImageDraw.Draw(image)
        title_font, label_font, value_font, temperature_font = load_bold_fonts(14, 10, 18, 12)
        draw_neon_frame(draw, "SYSTEM", title_font)

        columns = (
            (
                34,
                "CPU",
                f"{cpu:.0f}%",
                f"{cpu_temperature:.0f}°C" if cpu_temperature is not None else "--",
            ),
            (
                94,
                "GPU",
                f"{gpu_percent:.0f}%" if gpu_percent is not None else "--",
                f"{gpu_temperature:.0f}°C" if gpu_temperature is not None else "--",
            ),
        )
        for x, label, value, temperature in columns:
            draw.text((x, 38), label, fill=NEON_YELLOW, font=label_font, anchor="mt")
            draw.text(
                (x, 52),
                value,
                fill=NEON_WHITE,
                font=value_font,
                anchor="mt",
                stroke_width=1,
                stroke_fill=NEON_BLUE,
            )
            draw.text((x, 75), temperature, fill=NEON_TEAL, font=temperature_font, anchor="mt")

        draw.line((10, 91, 118, 91), fill=NEON_BLUE, width=1)
        draw.text((34, 96), "RAM", fill=NEON_YELLOW, font=label_font, anchor="mt")
        draw.text((34, 108), f"{memory.percent:.0f}%", fill=NEON_WHITE, font=value_font, anchor="mt")
        draw.text((94, 96), "DISK", fill=NEON_YELLOW, font=label_font, anchor="mt")
        draw.text((94, 108), f"{disk.percent:.0f}%", fill=NEON_WHITE, font=value_font, anchor="mt")

        return self.show_image(image, panel, reset_cache=False)

    def show_salah_board(self, times, panel=2):
        image = Image.new("RGB", (128, 128), NEON_BACKGROUND)
        draw = ImageDraw.Draw(image)
        title_font, row_font = load_bold_fonts(14, 12)
        draw_neon_frame(draw, "SALAH", title_font)
        y = 34
        for prayer in PRAYERS:
            prayer_time = times.get(prayer)
            time_text = prayer_time.strftime("%I:%M") if prayer_time else "--:--"
            draw.text((10, y), prayer.upper(), fill=NEON_WHITE, font=row_font)
            draw.text((82, y), time_text, fill=NEON_TEAL, font=row_font)
            y += 17

        return self.show_image(image, panel)

    def show_prayer(self, prayer_name, panel=2):
        image = Image.new("RGB", (128, 128), NEON_BACKGROUND)
        draw = ImageDraw.Draw(image)
        title_font, label_font = load_bold_fonts(20, 20)
        draw_neon_frame(draw, "PRAYER", title_font)
        name = prayer_name.upper()
        name_box = draw.textbbox((0, 0), name, font=title_font)
        draw.text(((128 - (name_box[2] - name_box[0])) // 2, 54), name, fill=NEON_YELLOW, font=label_font, stroke_width=1, stroke_fill=NEON_BLUE)
        draw.text((64, 94), "TIME TO PRAY", fill=NEON_CYAN, font=load_bold_fonts(10)[0], anchor="mt")

        return self.show_image(image, panel)


class PrayerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Tarneit Prayer TimeGate")
        self.root.geometry("620x430")
        self.root.resizable(False, False)

        self.config = load_config()
        self.prayer_times = {}
        self.alert_triggered = set()
        self.active_alert_prayer = None
        self.running = True
        self.discovery_lock = threading.Lock()
        self.device_available = threading.Event()
        self.device_available.set()

        self.build_ui()
        self.load_prayer_times()
        self.refresh_salah()
        self.refresh_pihole()
        self.refresh_server()
        self.refresh_dashboard()
        self.auto_discover_if_needed()
        self.root.after(1000, self.clock_tick)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def build_ui(self):
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Tarneit Prayer TimeGate", font=("Segoe UI", 18, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")

        ttk.Label(frame, text="TimeGate IP:").grid(row=1, column=0, sticky="w", pady=(18, 4))
        self.ip_var = tk.StringVar(value=self.config["device_ip"])
        ttk.Entry(frame, textvariable=self.ip_var, width=22).grid(row=1, column=1, sticky="w", pady=(18, 4))
        ttk.Button(frame, text="Discover", command=self.discover_device).grid(row=1, column=2, padx=(8, 0), pady=(18, 4))

        ttk.Label(frame, text="Current time:").grid(row=2, column=0, sticky="w", pady=4)
        self.clock_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.clock_var, font=("Consolas", 13, "bold")).grid(row=2, column=1, columnspan=2, sticky="w")

        ttk.Separator(frame, orient="horizontal").grid(row=3, column=0, columnspan=3, sticky="ew", pady=12)

        ttk.Label(frame, text="Prayer", width=14).grid(row=4, column=0, sticky="w")
        ttk.Label(frame, text="Time", width=12).grid(row=4, column=1, sticky="w")
        ttk.Label(frame, text="Status").grid(row=4, column=2, sticky="w")

        self.prayer_vars = {}
        for index, prayer in enumerate(PRAYERS, start=5):
            ttk.Label(frame, text=prayer).grid(row=index, column=0, sticky="w", pady=5)
            time_var = tk.StringVar(value="--:--")
            status_var = tk.StringVar(value="Waiting")
            self.prayer_vars[prayer] = (time_var, status_var)
            ttk.Label(frame, textvariable=time_var, font=("Consolas", 11)).grid(row=index, column=1, sticky="w")
            ttk.Label(frame, textvariable=status_var).grid(row=index, column=2, sticky="w")

        ttk.Separator(frame, orient="horizontal").grid(row=10, column=0, columnspan=3, sticky="ew", pady=10)
        self.status_var = tk.StringVar(value="Starting…")
        ttk.Label(frame, textvariable=self.status_var).grid(row=11, column=0, columnspan=3, sticky="w")

    def discover_device(self):
        self.status_var.set("Scanning the local network…")
        threading.Thread(target=self._discover_worker, kwargs={"manual": True}, daemon=True).start()

    def auto_discover_if_needed(self):
        if not self.running:
            return
        ip = self.ip_var.get().strip()
        self.root.after(5 * 60 * 1000, self.auto_discover_if_needed)
        threading.Thread(target=self._check_or_discover_worker, args=(ip,), daemon=True).start()

    def _check_or_discover_worker(self, ip):
        if self.is_timegate_online(ip):
            self.device_available.set()
            return
        self.device_available.clear()
        self.root.after(0, lambda: self.status_var.set("TimeGate unavailable; scanning the local network…"))
        self._discover_worker()

    def is_timegate_online(self, ip):
        if not ip:
            return False
        payload = {"Command": "Device/GetDeviceTime"}
        if self.config.get("local_token"):
            payload["LocalToken"] = int(self.config["local_token"])
        try:
            with DEVICE_API_LOCK:
                response = requests.post(f"http://{ip}:80/post", json=payload, timeout=1)
            response.raise_for_status()
            return is_timegate_response(response.json())
        except (requests.RequestException, ValueError):
            return False

    def _discover_worker(self, manual=False):
        if not self.discovery_lock.acquire(blocking=False):
            return
        try:
            self._scan_local_network(manual)
        finally:
            self.discovery_lock.release()

    def _scan_local_network(self, manual):
        configured_ip = self.ip_var.get().strip()
        local_ip = self.get_local_ip(configured_ip or "1.1.1.1")
        network = self.get_local_network(local_ip)
        hosts = list(network.hosts())
        if len(hosts) > 4096:
            network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
            hosts = list(network.hosts())
        found = []

        def probe(address):
            ip = str(address)
            if ip == local_ip:
                return None
            try:
                with socket.create_connection((ip, 80), timeout=0.15):
                    return ip
            except OSError:
                return None

        with ThreadPoolExecutor(max_workers=128) as executor:
            futures = [executor.submit(probe, address) for address in hosts]
            candidates = [future.result() for future in as_completed(futures)]
        found = [ip for ip in candidates if ip and self.is_timegate_online(ip)]

        self.root.after(0, lambda: self._finish_discovery(found, manual))

    @staticmethod
    def get_local_network(local_ip):
        for addresses in psutil.net_if_addrs().values():
            for address in addresses:
                if address.family == socket.AF_INET and address.address == local_ip and address.netmask:
                    return ipaddress.ip_network(f"{local_ip}/{address.netmask}", strict=False)
        return ipaddress.ip_network(f"{local_ip}/24", strict=False)

    def _finish_discovery(self, found, manual=False):
        if not found:
            self.status_var.set("No TimeGate found. Check Wi-Fi and enter its IP manually.")
            if manual:
                messagebox.showwarning("TimeGate not found", "No TimeGate answered on the local network.")
            return
        ip = found[0]
        self.ip_var.set(ip)
        self.save_ip()
        self.device_available.set()
        self.status_var.set(f"Connected to TimeGate at {ip}")
        threading.Thread(target=self._refresh_all_panels_worker, args=(ip,), daemon=True).start()

    def _refresh_all_panels_worker(self, ip):
        if not DEVICE_REFRESH_LOCK.acquire(blocking=False):
            return
        try:
            client = TimeGateClient(ip)
            client.show_clock_weather(1)
            if self.prayer_times:
                self._show_screen_two(client)
            client.show_hadith(3)
            client.show_pihole(4)
            client.show_server_monitor(5)
        except (requests.RequestException, RuntimeError) as exc:
            self.root.after(0, lambda error=str(exc): self.status_var.set(f"TimeGate refresh failed: {error}"))
        finally:
            DEVICE_REFRESH_LOCK.release()

    @staticmethod
    def get_local_ip(target="1.1.1.1"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((target, 80))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            sock.close()

    def save_ip(self):
        self.config["device_ip"] = self.ip_var.get().strip()
        save_config(self.config)

    def load_prayer_times(self):
        threading.Thread(target=self._load_prayer_worker, daemon=True).start()

    def _load_prayer_worker(self):
        try:
            today = datetime.now(ZoneInfo("Australia/Melbourne")).strftime("%d-%m-%Y")
            response = requests.get("https://api.aladhan.com/v1/timings", params={
                "date": today,
                "latitude": self.config["latitude"],
                "longitude": self.config["longitude"],
                "method": self.config["calculation_method"],
                "school": self.config["asr_method"],
                "timezonestring": self.config["timezone"],
                "iso8601": "true",
            }, timeout=15)
            response.raise_for_status()
            data = response.json()["data"]["timings"]
            times = {}
            for prayer in PRAYERS:
                clean = data[prayer].strip()
                if "T" in clean:
                    times[prayer] = datetime.fromisoformat(clean).time()
                else:
                    times[prayer] = datetime.strptime(clean.split()[0], "%H:%M").time()
            self.root.after(0, lambda: self._finish_prayer_load(times))
        except Exception as exc:
            self.root.after(
                0,
                lambda error=str(exc): self.status_var.set(f"Prayer-time update failed: {error}"),
            )
            self.root.after(15 * 60 * 1000, self.load_prayer_times)

    def _finish_prayer_load(self, times):
        self.prayer_times = times
        now = datetime.now(ZoneInfo("Australia/Melbourne"))
        for prayer in PRAYERS:
            time_var, _ = self.prayer_vars[prayer]
            time_var.set(times[prayer].strftime("%I:%M %p"))
        self.status_var.set(f"Prayer times loaded for {now.strftime('%d %b %Y')}")
        self._start_device_update("Salah", lambda client: self._show_screen_two(client))
        self.root.after(12 * 60 * 60 * 1000, self.load_prayer_times)

    def _start_device_update(self, label, update):
        if not self.device_available.is_set():
            return
        ip = self.ip_var.get().strip()
        if ip:
            threading.Thread(
                target=self._run_device_update,
                args=(ip, label, update),
                daemon=True,
            ).start()

    def _run_device_update(self, ip, label, update):
        if not DEVICE_REFRESH_LOCK.acquire(blocking=False):
            return
        try:
            update(TimeGateClient(ip))
        except (requests.RequestException, RuntimeError) as exc:
            self.root.after(
                0,
                lambda error=str(exc): self.status_var.set(f"{label} update failed: {error}"),
            )
        finally:
            DEVICE_REFRESH_LOCK.release()

    def refresh_salah(self):
        if not self.running:
            return
        if self.prayer_times:
            self._start_device_update("Salah", lambda client: self._show_screen_two(client))
        self.root.after(12 * 60 * 60 * 1000, self.refresh_salah)

    def refresh_pihole(self):
        if not self.running:
            return
        self._start_device_update("Pi-hole", lambda client: client.show_pihole(4))
        self.root.after(60 * 1000, self.refresh_pihole)

    def refresh_server(self):
        if not self.running:
            return
        self._start_device_update("Server monitor", lambda client: client.show_server_monitor(5))
        self.root.after(5000, self.refresh_server)

    def refresh_dashboard(self):
        if not self.running:
            return
        if not self.device_available.is_set():
            self.root.after(60000, self.refresh_dashboard)
            return
        ip = self.ip_var.get().strip()
        if ip:
            threading.Thread(target=self._refresh_dashboard_worker, args=(ip,), daemon=True).start()
        self.root.after(60000, self.refresh_dashboard)

    def _refresh_dashboard_worker(self, ip):
        if not DEVICE_REFRESH_LOCK.acquire(blocking=False):
            return
        try:
            client = TimeGateClient(ip)
            client.show_clock_weather(1)
            if self.prayer_times:
                self._show_screen_two(client)
            client.show_hadith(3)
            self.root.after(0, lambda: self.status_var.set("Clock, Salah and Hadith refreshed"))
        except Exception as exc:
            self.root.after(
                0,
                lambda error=str(exc): self.status_var.set(f"Clock/GIF update failed: {error}"),
            )
        finally:
            DEVICE_REFRESH_LOCK.release()

    def clock_tick(self):
        if not self.running:
            return
        now = datetime.now(ZoneInfo("Australia/Melbourne"))
        self.clock_var.set(now.strftime("%a %d %b %Y  %I:%M:%S %p"))

        today_key = now.strftime("%Y-%m-%d")
        for prayer in PRAYERS:
            if not self.prayer_times:
                break
            prayer_time = self.prayer_times[prayer]
            prayer_dt = datetime.combine(now.date(), prayer_time, tzinfo=now.tzinfo)
            _, status_var = self.prayer_vars[prayer]
            alert_key = f"{today_key}:{prayer}"

            if now >= prayer_dt:
                status_var.set("Passed")

            if now >= prayer_dt and now < prayer_dt + timedelta(seconds=10) and alert_key not in self.alert_triggered:
                self.alert_triggered.add(alert_key)
                threading.Thread(target=self.trigger_alert, args=(prayer,), daemon=True).start()

        self.root.after(1000, self.clock_tick)

    def trigger_alert(self, prayer):
        ip = self.ip_var.get().strip()
        self.save_ip()
        if ip:
            with DEVICE_REFRESH_LOCK:
                try:
                    client = TimeGateClient(ip)
                    client.show_prayer(prayer, panel=2)
                    self.active_alert_prayer = prayer
                    client.send({"Command": "Device/PlayBuzzer", "ActiveTimeInCycle": 100, "OffTimeInCycle": 100, "PlayTotalTime": 2000})
                    threading.Timer(
                        self.config["alert_seconds"],
                        lambda: self.restore_salah(ip),
                    ).start()
                except Exception as exc:
                    self.root.after(
                        0,
                        lambda error=str(exc): self.status_var.set(f"TimeGate alert failed: {error}"),
                    )

        if winsound:
            try:
                winsound.Beep(880, 1000)
                winsound.Beep(660, 1000)
            except RuntimeError:
                pass

        self.root.after(0, lambda: self.status_var.set(f"{prayer} alert active for 5 minutes"))

    def restore_salah(self, ip):
        with DEVICE_REFRESH_LOCK:
            try:
                self.active_alert_prayer = None
                TimeGateClient(ip).show_salah_board(self.prayer_times, panel=2)
                self.root.after(0, lambda: self.status_var.set("Returned to Salah timetable"))
            except Exception as exc:
                self.root.after(
                    0,
                    lambda error=str(exc): self.status_var.set(f"Could not restore Salah timetable: {error}"),
                )

    def _show_screen_two(self, client):
        if self.active_alert_prayer:
            return client.show_prayer(self.active_alert_prayer, panel=2)
        return client.show_salah_board(self.prayer_times, panel=2)

    def close(self):
        self.running = False
        self.save_ip()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = PrayerApp(root)
    root.mainloop()
