import base64
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

    @property
    def url(self):
        return f"http://{self.ip}:80/post"

    def send(self, payload, timeout=10):
        if not self.ip:
            return False
        response = requests.post(self.url, json=payload, timeout=timeout)
        response.raise_for_status()
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

    def show_image(self, image, panel, tag=""):
        self.screen_on()
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
            "PicID": int(time.time()) + panel,
            "PicSpeed": 1000,
            "PicData": pic_data,
        })

    def show_clock_weather(self, panel=1):
        now = datetime.now(ZoneInfo("Australia/Melbourne"))
        temperature = "--°"
        try:
            response = requests.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": self.config["latitude"], "longitude": self.config["longitude"],
                "current": "temperature_2m", "timezone": "Australia/Melbourne",
            }, timeout=8)
            temperature = f"{round(response.json()['current']['temperature_2m'])}°C"
        except Exception:
            pass

        image = Image.new("RGB", (128, 128), (8, 14, 28))
        draw = ImageDraw.Draw(image)
        try:
            big = ImageFont.truetype("arial.ttf", 32)
            small = ImageFont.truetype("arial.ttf", 14)
        except OSError:
            big = ImageFont.load_default(); small = ImageFont.load_default()
        draw.text((10, 18), now.strftime("%I:%M"), fill=(255, 255, 255), font=big)
        draw.text((12, 60), now.strftime("%a %d %b").upper(), fill=(0, 255, 170), font=small)
        draw.text((12, 82), "TARNEIT", fill=(180, 220, 220), font=small)
        draw.text((12, 102), temperature, fill=(255, 190, 80), font=small)
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
                    frame = Image.fromarray(
                        __import__("numpy").asarray(source.convert("RGB"), dtype="uint8")
                    ).resize((128, 128), Image.Resampling.NEAREST)
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

        image = Image.new("RGB", (128, 128), (10, 18, 32))
        draw = ImageDraw.Draw(image)
        try:
            title = ImageFont.truetype("arial.ttf", 16)
            text = ImageFont.truetype("arial.ttf", 12)
        except OSError:
            title = ImageFont.load_default()
            text = ImageFont.load_default()
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
        cpu = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        image = Image.new("RGB", (128, 128), (12, 12, 16))
        draw = ImageDraw.Draw(image)
        try:
            title = ImageFont.truetype("arial.ttf", 14)
            value = ImageFont.truetype("arial.ttf", 12)
        except OSError:
            title = ImageFont.load_default(); value = ImageFont.load_default()
        draw.text((8, 8), "SERVER", fill=(255,255,255), font=title)
        rows = (("CPU", cpu), ("RAM", memory.percent), ("DISK", disk.percent))
        y = 38
        for label, percent in rows:
            draw.text((8, y), label, fill=(180,220,220), font=value)
            draw.rectangle((43, y+2, 118, y+10), fill=(40,45,48))
            draw.rectangle((43, y+2, 43 + int(75*percent/100), y+10), fill=(0,255,170))
            draw.text((43, y+12), f"{percent:.0f}%", fill=(255,255,255), font=value)
            y += 28
        return self.show_image(image, panel)

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

        draw.rectangle((5, 5, 122, 122), outline=(0, 255, 170), width=2)
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
        threading.Thread(target=self._discover_worker, daemon=True).start()

    def auto_discover_if_needed(self):
        if not self.running:
            return
        ip = self.ip_var.get().strip()
        try:
            response = requests.post(
                f"http://{ip}:80/post",
                json={"Command": "Device/GetDeviceTime"},
                timeout=1,
            )
            reachable = response.ok
        except requests.RequestException:
            reachable = False

        if not reachable:
            threading.Thread(target=self._discover_worker, daemon=True).start()
            self.status_var.set("TimeGate IP changed or unavailable; rediscovering…")
        self.root.after(5 * 60 * 1000, self.auto_discover_if_needed)

    def _discover_worker(self):
        local_ip = self.get_local_ip()
        prefix = ".".join(local_ip.split(".")[:3])
        found = []

        def probe(last_octet):
            ip = f"{prefix}.{last_octet}"
            try:
                response = requests.post(
                    f"http://{ip}:80/post",
                    json={"Command": "Device/GetDeviceTime"},
                    timeout=0.12,
                )
                if response.ok:
                    return ip
            except requests.RequestException:
                pass
            return None

        with ThreadPoolExecutor(max_workers=64) as executor:
            futures = [executor.submit(probe, n) for n in range(1, 255)]
            for future in as_completed(futures):
                ip = future.result()
                if ip:
                    found.append(ip)

        self.root.after(0, lambda: self._finish_discovery(found))

    def _finish_discovery(self, found):
        if not found:
            self.status_var.set("No TimeGate found. Check Wi-Fi and enter its IP manually.")
            messagebox.showwarning("TimeGate not found", "No TimeGate answered on the local network.")
            return
        ip = found[0]
        self.ip_var.set(ip)
        self.save_ip()
        self.status_var.set(f"Connected to TimeGate at {ip}")

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
            self.root.after(0, lambda: self.status_var.set(f"Prayer-time update failed: {exc}"))

    def _finish_prayer_load(self, times):
        self.prayer_times = times
        now = datetime.now(ZoneInfo("Australia/Melbourne"))
        for prayer in PRAYERS:
            time_var, _ = self.prayer_vars[prayer]
            time_var.set(times[prayer].strftime("%I:%M %p"))
        self.status_var.set(f"Prayer times loaded for {now.strftime('%d %b %Y')}")
        ip = self.ip_var.get().strip()
        if ip:
            threading.Thread(
                target=lambda: TimeGateClient(ip).show_salah_board(times, 2),
                daemon=True,
            ).start()
        self.root.after(12 * 60 * 60 * 1000, self.load_prayer_times)

    def refresh_salah(self):
        if not self.running:
            return
        ip = self.ip_var.get().strip()
        if ip:
            threading.Thread(target=lambda: TimeGateClient(ip).show_salah_board(self.prayer_times, 2), daemon=True).start()
        self.root.after(12 * 60 * 60 * 1000, self.refresh_salah)

    def refresh_knowledge(self):
        if not self.running:
            return
        ip = self.ip_var.get().strip()
        if ip:
            threading.Thread(target=lambda: TimeGateClient(ip).show_knowledge(4), daemon=True).start()
        self.root.after(60 * 1000, self.refresh_knowledge)

    def refresh_server(self):
        if not self.running:
            return
        ip = self.ip_var.get().strip()
        if ip:
            threading.Thread(target=lambda: TimeGateClient(ip).show_server_monitor(5), daemon=True).start()
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
            client.show_gif(3)
            self.root.after(0, lambda: self.status_var.set("Clock and GIF refreshed"))
        except Exception as exc:
            self.root.after(0, lambda: self.status_var.set(f"Clock/GIF update failed: {exc}"))

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
                threading.Timer(
                    self.config["alert_seconds"],
                    lambda: self.restore_salah(ip),
                ).start()
            except Exception as exc:
                self.root.after(0, lambda: self.status_var.set(f"TimeGate alert failed: {exc}"))

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
            self.root.after(0, lambda: self.status_var.set(f"Could not restore Salah timetable: {exc}"))

    def close(self):
        self.running = False
        self.save_ip()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = PrayerApp(root)
    root.mainloop()
