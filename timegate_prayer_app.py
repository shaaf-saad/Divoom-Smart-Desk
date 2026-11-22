import base64
import ipaddress
import io
import json
import os
import socket
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
DEVICE_API_LOCK = threading.Lock()

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

    def show_native_clock_weather(self, panel=1):
        self.send({
            "Command": "Sys/LogAndLat",
            "Latitude": str(self.config["latitude"]),
            "Longitude": str(self.config["longitude"]),
        })
        self.send({
            "Command": "Sys/TimeZone",
            "TimeZoneValue": "GMT+11:00",
        })
        return self.send({
            "Command": "Draw/SendHttpItemList",
            "LcdIndex": panel,
            "NewFlag": 1,
            "BackgroudGif": "",
            "ItemList": [
                {
                    "TextId": 1,
                    "type": 6,
                    "x": 64,
                    "y": 24,
                    "dir": 0,
                    "font": 2,
                    "TextWidth": 120,
                    "Textheight": 16,
                    "speed": 0,
                    "align": 2,
                    "color": "#00FFAA",
                },
                {
                    "TextId": 2,
                    "type": 14,
                    "x": 64,
                    "y": 58,
                    "dir": 0,
                    "font": 4,
                    "TextWidth": 120,
                    "Textheight": 24,
                    "speed": 0,
                    "align": 2,
                    "color": "#FFC050",
                },
                {
                    "TextId": 3,
                    "type": 14,
                    "x": 64,
                    "y": 94,
                    "dir": 0,
                    "font": 2,
                    "TextWidth": 120,
                    "Textheight": 16,
                    "speed": 0,
                    "align": 2,
                    "color": "#FFFFFF",
                },
            ],
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

        image = Image.new("RGB", (128, 128), (5, 12, 18))
        draw = ImageDraw.Draw(image)
        try:
            title = ImageFont.truetype("arial.ttf", 14)
            time_font = ImageFont.truetype("arial.ttf", 30)
            value = ImageFont.truetype("arial.ttf", 13)
        except OSError:
            title = ImageFont.load_default()
            time_font = ImageFont.load_default()
            value = ImageFont.load_default()
        draw.rectangle((4, 4, 123, 123), outline=(0, 255, 255), width=2)
        draw.line((8, 28, 120, 28), fill=(255, 190, 80), width=1)
        draw.text((8, 8), "CLOCK / WEATHER", fill=(255,255,255), font=title)
        draw.text((8, 34), now.strftime("%H:%M"), fill=(0,255,170), font=time_font)
        draw.text((8, 66), "TEMP", fill=(180,220,220), font=value)
        draw.text((8, 82), f"{temperature}°C", fill=(255,190,80), font=time_font)
        draw.text((8, 104), now.strftime("%a %d %b").upper(), fill=(255,255,255), font=value)
        draw.text((8, 121), condition, fill=(180,220,220), font=value)
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

        image = Image.new("RGB", (128, 128), (10, 18, 22))
        draw = ImageDraw.Draw(image)
        try:
            title_font = ImageFont.truetype("arial.ttf", 15)
            text_font = ImageFont.truetype("arial.ttf", 12)
            footer_font = ImageFont.truetype("arial.ttf", 10)
        except OSError:
            title_font = ImageFont.load_default()
            text_font = ImageFont.load_default()
            footer_font = ImageFont.load_default()

        draw.rectangle((5, 5, 122, 122), outline=(0, 255, 255), width=2)
        draw.line((9, 29, 118, 29), fill=(255, 190, 80), width=1)
        draw.text((12, 12), title, fill=(255, 255, 255), font=title_font)

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
            draw.text((12, y), line, fill=(0, 255, 170), font=text_font)
            y += 15

        minutes = (300 - (int(time.time()) % 300)) // 60
        draw.text((12, 108), f"NEW IN {minutes} MIN", fill=(180, 220, 220), font=footer_font)
        return self.show_image(image, panel)

    def show_knowledge(self, panel=4):
        cards = (
            ("AI", "Models learn patterns from examples."),
            ("TECH", "A GPU runs many small tasks in parallel."),
            ("NETWORK", "Latency is the delay before data arrives."),
            ("KNOWLEDGE", "Local AI keeps data inside your network."),
            ("SYSTEM", "RAM is fast, while storage keeps data."),
            ("SPACE", "A day on Mars is 24 hours and 37 minutes."),
        )
        category, fact = cards[(int(time.time()) // 300) % len(cards)]

        image = Image.new("RGB", (128, 128), (5, 12, 18))
        draw = ImageDraw.Draw(image)
        try:
            title = ImageFont.truetype("arial.ttf", 16)
            text = ImageFont.truetype("arial.ttf", 12)
        except OSError:
            title = ImageFont.load_default()
            text = ImageFont.load_default()
        draw.rectangle((4, 4, 123, 123), outline=(0, 255, 255), width=2)
        draw.line((8, 28, 120, 28), fill=(255, 190, 80), width=1)
        draw.text((8, 8), "KNOWLEDGE", fill=(255,255,255), font=title)
        draw.text((8, 32), category, fill=(0,255,170), font=text)

        words = fact.split()
        lines, line = [], ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if draw.textlength(candidate, font=text) > 112:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)

        y = 54
        for item in lines:
            draw.text((8, y), item, fill=(255,255,255), font=text)
            y += 18
        draw.text((8, 116), "UPDATED LIVE", fill=(255,190,80), font=text)
        return self.show_image(image, panel)

    def show_server_monitor(self, panel=5):
        psutil.cpu_percent(interval=None)
        cpu = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        gpu_percent = None
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0 and result.stdout.strip():
                gpu_percent = float(result.stdout.strip().splitlines()[0].strip())
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass

        if not getattr(self, "_native_pc_face_ready", False):
            self.send({
                "Command": "Channel/SetClockSelectId",
                "ClockId": 625,
                "LcdIndex": panel - 1,
                "LcdIndependence": [0, 0, 0, 0, 1],
            })
            self._native_pc_face_ready = True
            time.sleep(0.5)

        return self.send({
            "Command": "Device/UpdatePCParaInfo",
            "ScreenList": [{
                "LcdId": panel - 1,
                "DispData": [
                    f"{cpu:.0f}%",
                    f"{gpu_percent:.0f}%" if gpu_percent is not None else "--",
                    "--",
                    "--",
                    f"{memory.percent:.0f}%",
                    "--",
                ],
            }],
        })

    def show_salah_board(self, times, panel=2):
        self.screen_on()
        self.send({"Command": "Draw/ResetHttpGifId"})

        image = Image.new("RGB", (128, 128), (5, 12, 18))
        draw = ImageDraw.Draw(image)
        try:
            title_font = ImageFont.truetype("arial.ttf", 16)
            row_font = ImageFont.truetype("arial.ttf", 13)
        except OSError:
            title_font = ImageFont.load_default()
            row_font = ImageFont.load_default()

        draw.rectangle((5, 5, 122, 122), outline=(0, 255, 255), width=2)
        draw.line((9, 29, 118, 29), fill=(255, 190, 80), width=1)
        draw.text((12, 10), "SALAH", fill=(255, 255, 255), font=title_font)
        y = 34
        for prayer in PRAYERS:
            prayer_time = times.get(prayer)
            time_text = prayer_time.strftime("%I:%M") if prayer_time else "--:--"
            draw.text((10, y), prayer.upper(), fill=(180, 220, 220), font=row_font)
            draw.text((82, y), time_text, fill=(0, 255, 170), font=row_font)
            y += 17

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
            "PicID": int(time.time()),
            "PicSpeed": 1000,
            "PicData": pic_data,
        })

    def show_prayer(self, prayer_name, panel=2):
        self.screen_on()
        self.send({"Command": "Draw/ResetHttpGifId"})

        image = Image.new("RGB", (128, 128), (5, 12, 18))
        draw = ImageDraw.Draw(image)
        try:
            title_font = ImageFont.truetype("arial.ttf", 24)
            label_font = ImageFont.truetype("arial.ttf", 18)
        except OSError:
            title_font = ImageFont.load_default()
            label_font = ImageFont.load_default()

        draw.rectangle((8, 8, 119, 119), outline=(0, 255, 170), width=3)
        title = "PRAYER"
        name = prayer_name.upper()
        title_box = draw.textbbox((0, 0), title, font=title_font)
        name_box = draw.textbbox((0, 0), name, font=title_font)
        draw.text(((128 - (title_box[2] - title_box[0])) // 2, 30), title, fill=(255, 255, 255), font=title_font)
        draw.text(((128 - (name_box[2] - name_box[0])) // 2, 68), name, fill=(0, 255, 170), font=title_font)

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
            "PicID": int(time.time()),
            "PicSpeed": 1000,
            "PicData": pic_data,
        })


class PrayerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Tarneit Prayer TimeGate")
        self.root.geometry("620x430")
        self.root.resizable(False, False)

        self.config = load_config()
        self.prayer_times = {}
        self.alert_triggered = set()
        self.running = True
        self.discovery_lock = threading.Lock()

        self.build_ui()
        self.load_prayer_times()
        self.refresh_salah()
        self.refresh_knowledge()
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
            return
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
        local_ip = self.get_local_ip()
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
        self.status_var.set(f"Connected to TimeGate at {ip}")
        threading.Thread(target=self._refresh_all_panels_worker, args=(ip,), daemon=True).start()

    def _refresh_all_panels_worker(self, ip):
        client = TimeGateClient(ip)
        try:
            client.show_native_clock_weather(1)
            client.show_salah_board(self.prayer_times, 2)
            client.show_hadith(3)
            client.show_knowledge(4)
            client.show_server_monitor(5)
        except (requests.RequestException, RuntimeError) as exc:
            self.root.after(0, lambda error=str(exc): self.status_var.set(f"TimeGate refresh failed: {error}"))

    @staticmethod
    def get_local_ip():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("1.1.1.1", 80))
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
        self._start_device_update("Salah", lambda client: client.show_salah_board(times, 2))
        self.root.after(12 * 60 * 60 * 1000, self.load_prayer_times)

    def _start_device_update(self, label, update):
        ip = self.ip_var.get().strip()
        if ip:
            threading.Thread(
                target=self._run_device_update,
                args=(ip, label, update),
                daemon=True,
            ).start()

    def _run_device_update(self, ip, label, update):
        try:
            update(TimeGateClient(ip))
        except (requests.RequestException, RuntimeError) as exc:
            self.root.after(
                0,
                lambda error=str(exc): self.status_var.set(f"{label} update failed: {error}"),
            )

    def refresh_salah(self):
        if not self.running:
            return
        if self.prayer_times:
            self._start_device_update("Salah", lambda client: client.show_salah_board(self.prayer_times, 2))
        self.root.after(12 * 60 * 60 * 1000, self.refresh_salah)

    def refresh_knowledge(self):
        if not self.running:
            return
        self._start_device_update("Knowledge", lambda client: client.show_knowledge(4))
        self.root.after(60 * 1000, self.refresh_knowledge)

    def refresh_server(self):
        if not self.running:
            return
        self._start_device_update("Server monitor", lambda client: client.show_server_monitor(5))
        self.root.after(5000, self.refresh_server)

    def refresh_dashboard(self):
        if not self.running:
            return
        ip = self.ip_var.get().strip()
        if ip:
            threading.Thread(target=self._refresh_dashboard_worker, args=(ip,), daemon=True).start()
        self.root.after(60000, self.refresh_dashboard)

    def _refresh_dashboard_worker(self, ip):
        try:
            client = TimeGateClient(ip)
            client.show_clock_weather(1)
            client.show_hadith(3)
            self.root.after(0, lambda: self.status_var.set("Clock and Hadith refreshed"))
        except Exception as exc:
            self.root.after(
                0,
                lambda error=str(exc): self.status_var.set(f"Clock/GIF update failed: {error}"),
            )

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
            try:
                client = TimeGateClient(ip)
                client.show_prayer(prayer, panel=2)
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
        try:
            TimeGateClient(ip).show_salah_board(self.prayer_times, panel=2)
            self.root.after(0, lambda: self.status_var.set("Returned to Salah timetable"))
        except Exception as exc:
            self.root.after(
                0,
                lambda error=str(exc): self.status_var.set(f"Could not restore Salah timetable: {error}"),
            )

    def close(self):
        self.running = False
        self.save_ip()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = PrayerApp(root)
    root.mainloop()
