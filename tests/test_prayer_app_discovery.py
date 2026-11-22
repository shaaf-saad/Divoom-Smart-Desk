import socket

from timegate_prayer_app import PrayerApp, is_timegate_response


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
