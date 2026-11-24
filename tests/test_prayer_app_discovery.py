import sqlite3
import socket
import threading
from datetime import time
from types import SimpleNamespace
from unittest.mock import Mock

from timegate_prayer_app import (
    DEVICE_REFRESH_LOCK,
    PRAYERS,
    PrayerApp,
    TimeGateClient,
    is_timegate_response,
)


def test_timegate_response_requires_device_time_data():
    assert is_timegate_response({
        "error_code": 0,
        "UTCTime": 1795316480,
        "LocalTime": "2026-11-22 14:01:20",
    })


def test_timegate_response_rejects_authentication_error():
    assert not is_timegate_response({"error_code": "DeviceToken is err"})


def test_timegate_response_rejects_unrelated_http_server():
    assert not is_timegate_response({"error_code": 0, "status": "ok"})


def test_local_network_uses_interface_netmask(monkeypatch):
    class Address:
        family = socket.AF_INET
        address = "192.168.68.59"
        netmask = "255.255.252.0"

    monkeypatch.setattr("timegate_prayer_app.psutil.net_if_addrs", lambda: {"wlan0": [Address()]})

    assert str(PrayerApp.get_local_network("192.168.68.59")) == "192.168.68.0/22"


def test_local_ip_routes_discovery_to_configured_device(monkeypatch):
    class Socket:
        def __init__(self, *_args):
            self.target = None

        def connect(self, target):
            self.target = target

        def getsockname(self):
            assert self.target == ("192.168.68.55", 80)
            return "192.168.68.59", 12345

        def close(self):
            pass

    monkeypatch.setattr("timegate_prayer_app.socket.socket", Socket)

    assert PrayerApp.get_local_ip("192.168.68.55") == "192.168.68.59"


def test_device_update_skips_while_refresh_is_active(monkeypatch):
    calls = []
    app = PrayerApp.__new__(PrayerApp)
    app.device_available = threading.Event()
    app.device_available.set()
    app.root = type(
        "Root",
        (),
        {"after": lambda self, _delay, callback: callback()},
    )()
    app.status_var = type("Status", (), {"set": lambda self, _value: None})()
    assert DEVICE_REFRESH_LOCK.acquire(blocking=False)
    try:
        app._run_device_update(
            "192.168.68.55",
            "Server monitor",
            lambda _client: calls.append(True),
        )
    finally:
        DEVICE_REFRESH_LOCK.release()

    assert calls == []


def test_device_update_skips_while_timegate_is_offline():
    app = PrayerApp.__new__(PrayerApp)
    app.device_available = threading.Event()
    app.ip_var = type("IP", (), {"get": lambda self: "192.168.68.55"})()
    started = []
    app._run_device_update = lambda *args: started.append(args)

    app._start_device_update("test", lambda _client: None)

    assert started == []


def test_timegate_recovery_refreshes_all_panels(monkeypatch):
    app = PrayerApp.__new__(PrayerApp)
    app.device_available = threading.Event()
    app.status_var = type("Status", (), {"set": lambda self, _value: None})()
    app.root = type(
        "Root",
        (),
        {"after": lambda self, _delay, callback: callback()},
    )()
    refreshed = []
    monkeypatch.setattr(app, "is_timegate_online", lambda _ip: True)
    monkeypatch.setattr(
        "timegate_prayer_app.threading.Thread",
        lambda *args, **kwargs: type(
            "Thread",
            (),
            {"start": lambda self: refreshed.append(kwargs["args"][0])},
        )(),
    )

    app._check_or_discover_worker("192.168.68.55")

    assert app.device_available.is_set()
    assert refreshed == ["192.168.68.55"]


def test_dashboard_refresh_restores_screen_two(monkeypatch):
    calls = []

    class Client:
        def __init__(self, ip):
            calls.append(("client", ip))

        def show_clock_weather(self, panel):
            calls.append(("clock", panel))

        def show_salah_board(self, times, panel):
            calls.append(("salah", times, panel))

        def show_hadith(self, panel):
            calls.append(("hadith", panel))

    class Root:
        def after(self, _delay, callback):
            callback()

    app = PrayerApp.__new__(PrayerApp)
    app.root = Root()
    app.status_var = type("Status", (), {"set": lambda self, value: calls.append(("status", value))})()
    app.prayer_times = {prayer: time(5, index) for index, prayer in enumerate(PRAYERS)}
    app.active_alert_prayer = None
    monkeypatch.setattr("timegate_prayer_app.TimeGateClient", Client)

    app._refresh_dashboard_worker("192.168.68.55")

    assert calls[1:] == [
        ("clock", 1),
        ("salah", app.prayer_times, 2),
        ("hadith", 3),
        ("status", "Clock, Salah and Hadith refreshed"),
    ]


def test_dashboard_refresh_waits_for_prayer_times(monkeypatch):
    calls = []

    class Client:
        def __init__(self, _ip):
            pass

        def show_clock_weather(self, panel):
            calls.append(("clock", panel))

        def show_salah_board(self, _times, panel):
            calls.append(("salah", panel))

        def show_hadith(self, panel):
            calls.append(("hadith", panel))

    class Root:
        def after(self, _delay, callback):
            callback()

    app = PrayerApp.__new__(PrayerApp)
    app.root = Root()
    app.status_var = type("Status", (), {"set": lambda self, _value: None})()
    app.prayer_times = {}
    app.active_alert_prayer = None
    monkeypatch.setattr("timegate_prayer_app.TimeGateClient", Client)

    app._refresh_dashboard_worker("192.168.68.55")

    assert calls == [("clock", 1), ("hadith", 3)]


def test_dashboard_refresh_preserves_active_prayer_alert(monkeypatch):
    calls = []

    class Client:
        def __init__(self, _ip):
            pass

        def show_clock_weather(self, panel):
            calls.append(("clock", panel))

        def show_salah_board(self, _times, panel):
            calls.append(("salah", panel))

        def show_prayer(self, prayer, panel):
            calls.append(("prayer", prayer, panel))

        def show_hadith(self, panel):
            calls.append(("hadith", panel))

    class Root:
        def after(self, _delay, callback):
            callback()

    app = PrayerApp.__new__(PrayerApp)
    app.root = Root()
    app.status_var = type("Status", (), {"set": lambda self, _value: None})()
    app.prayer_times = {"Fajr": time(5, 0)}
    app.active_alert_prayer = "Fajr"
    monkeypatch.setattr("timegate_prayer_app.TimeGateClient", Client)

    app._refresh_dashboard_worker("192.168.68.55")

    assert calls == [("clock", 1), ("prayer", "Fajr", 2), ("hadith", 3)]


def test_server_monitor_includes_temperatures(monkeypatch):
    client = TimeGateClient.__new__(TimeGateClient)
    rendered = []
    monkeypatch.setattr(
        client,
        "show_image",
        lambda image, panel, reset_cache=True: rendered.append(
            (image, panel, reset_cache)
        ),
    )
    monkeypatch.setattr("timegate_prayer_app.psutil.cpu_percent", lambda interval=None: 42)
    monkeypatch.setattr(
        "timegate_prayer_app.psutil.virtual_memory",
        lambda: SimpleNamespace(percent=61),
    )
    monkeypatch.setattr(
        "timegate_prayer_app.psutil.disk_usage",
        lambda _path: SimpleNamespace(percent=73),
    )
    monkeypatch.setattr(
        "timegate_prayer_app.psutil.sensors_temperatures",
        lambda: {
            "k10temp": [SimpleNamespace(current=67.4)],
            "amdgpu": [SimpleNamespace(current=59.6)],
        },
    )
    monkeypatch.setattr(
        "timegate_prayer_app.subprocess.run",
        lambda *args, **kwargs: Mock(returncode=1, stdout=""),
    )

    client.show_server_monitor(panel=5)

    assert len(rendered) == 1
    image, panel, reset_cache = rendered[0]
    assert image.size == (128, 128)
    assert panel == 5
    assert reset_cache is False


def test_amd_gpu_utilization_uses_sysfs(tmp_path):
    utilization_file = tmp_path / "card1" / "device" / "gpu_busy_percent"
    utilization_file.parent.mkdir(parents=True)
    utilization_file.write_text("27\n", encoding="ascii")

    assert TimeGateClient.get_amd_gpu_utilization(tmp_path) == 27.0


def test_pihole_stats_count_only_blocked_queries(tmp_path):
    database = tmp_path / "pihole.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE query_storage (timestamp INTEGER, status INTEGER, client INTEGER)"
        )
        connection.executemany(
            "INSERT INTO query_storage VALUES (?, ?, ?)",
            [
                (100, 1, 1),
                (100, 2, 1),
                (100, 4, 2),
                (100, 12, 2),
                (1, 1, 3),
            ],
        )

    stats = TimeGateClient.load_pihole_stats(database, now=100 + 24 * 60 * 60 - 1)

    assert stats == {
        "total": 4,
        "blocked": 2,
        "clients": 2,
        "percentage": 50.0,
    }
