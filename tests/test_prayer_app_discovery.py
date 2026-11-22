import socket
from datetime import datetime, time
from zoneinfo import ZoneInfo

from timegate_prayer_app import PRAYERS, PrayerApp, TimeGateClient, is_timegate_response


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


def test_dashboard_refresh_restores_screen_two(monkeypatch):
    calls = []

    class Client:
        def __init__(self, ip):
            calls.append(("client", ip))

        def show_native_clock_weather(self, panel):
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

        def show_native_clock_weather(self, panel):
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

        def show_native_clock_weather(self, panel):
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


def test_native_clock_weather_targets_screen_one_and_uses_configured_timezone(monkeypatch):
    payloads = []
    client = TimeGateClient.__new__(TimeGateClient)
    client.config = {
        "latitude": -37.8368,
        "longitude": 144.6596,
        "timezone": "Australia/Melbourne",
    }
    monkeypatch.setattr(client, "send", payloads.append)

    client.show_native_clock_weather(panel=1)

    offset_minutes = int(
        datetime.now(ZoneInfo("Australia/Melbourne")).utcoffset().total_seconds() // 60
    )
    expected_timezone = (
        f"GMT{'+' if offset_minutes >= 0 else '-'}"
        f"{abs(offset_minutes) // 60}:{abs(offset_minutes) % 60:02d}"
    )
    assert payloads[0]["Command"] == "Sys/LogAndLat"
    assert payloads[1] == {
        "Command": "Sys/TimeZone",
        "TimeZoneValue": expected_timezone,
    }
    assert payloads[2] == {"Command": "Draw/ResetHttpGifId"}
    assert payloads[3] == {
        "Command": "Channel/SetClockSelectId",
        "ClockId": 50,
        "LcdIndex": 0,
        "LcdIndependence": [1, 0, 0, 0, 0],
    }
