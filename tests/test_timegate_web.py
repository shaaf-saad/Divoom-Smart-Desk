import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from timegate_web import start_timegate_web_server


def test_read_only_preview_serves_cached_screens(tmp_path):
    (tmp_path / "screen-1.jpg").write_bytes(b"screen-one")
    server, thread = start_timegate_web_server(tmp_path, port=0)
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(f"{base_url}/api/status", timeout=2) as response:
            status = json.load(response)
        with urlopen(f"{base_url}/screens/1.jpg", timeout=2) as response:
            image = response.read()

        assert status["screens"][0]["available"]
        assert status["screens"][1]["available"] is False
        assert image == b"screen-one"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_read_only_preview_rejects_post(tmp_path):
    server, thread = start_timegate_web_server(tmp_path, port=0)
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        request = Request(f"{base_url}/api/status", data=b"{}", method="POST")
        try:
            urlopen(request, timeout=2)
            raise AssertionError("POST unexpectedly succeeded")
        except HTTPError as exc:
            assert exc.code == 405
    finally:
        server.shutdown()
        thread.join(timeout=2)
