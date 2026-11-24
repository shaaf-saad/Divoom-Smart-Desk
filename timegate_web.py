import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_CACHE_DIR = Path(
    os.environ.get(
        "DIVOOM_SCREEN_CACHE",
        os.path.join(
            os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
            "divoom-timegate",
            "screens",
        ),
    )
)

SCREEN_LABELS = {
    1: "Clock & Weather",
    2: "Salah",
    3: "Hadith",
    4: "Pi-hole",
    5: "System",
}

PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Divoom TimeGate</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body {
      min-height: 100vh; margin: 0; color: #efffff; background:
        radial-gradient(circle at 15% 15%, rgba(0, 255, 235, .10), transparent 30rem),
        radial-gradient(circle at 85% 80%, rgba(36, 126, 255, .12), transparent 28rem),
        #040a12;
    }
    header { padding: 28px 20px 12px; text-align: center; }
    h1 { margin: 0; color: #efffff; font-size: clamp(24px, 5vw, 42px); }
    header p { margin: 8px 0 0; color: #9fb9c8; }
    main {
      width: min(1180px, calc(100% - 32px)); margin: 22px auto 40px;
      display: grid; grid-template-columns: repeat(5, minmax(150px, 1fr)); gap: 18px;
    }
    figure {
      margin: 0; padding: 12px; border: 1px solid rgba(0, 255, 235, .7);
      border-radius: 14px; background: rgba(7, 18, 29, .92);
      box-shadow: 0 0 22px rgba(36, 126, 255, .12);
    }
    figure img { display: block; width: 100%; aspect-ratio: 1; image-rendering: pixelated; border-radius: 6px; }
    figcaption { margin-top: 10px; color: #ffbe50; font-size: 13px; font-weight: 700; text-align: center; }
    .updated { margin-top: 5px; color: #7ea0af; font-size: 11px; text-align: center; }
    .stale { opacity: .62; }
    footer { padding: 0 20px 30px; color: #718f9d; text-align: center; font-size: 12px; }
    @media (max-width: 900px) { main { grid-template-columns: repeat(3, minmax(150px, 1fr)); } }
    @media (max-width: 540px) { main { grid-template-columns: repeat(2, minmax(130px, 1fr)); } }
  </style>
</head>
<body>
  <header><h1>Divoom TimeGate</h1><p>Read-only live screen preview</p></header>
  <main id="screens"></main>
  <footer>Refreshes automatically · Control commands are not available on this page</footer>
  <script>
    const labels = {
      1: "Clock & Weather",
      2: "Salah",
      3: "Hadith",
      4: "Pi-hole",
      5: "System"
    };
    const root = document.getElementById("screens");
    for (let panel = 1; panel <= 5; panel++) {
      const figure = document.createElement("figure");
      figure.innerHTML = `<img id="screen-${panel}" alt="${labels[panel]}"><figcaption>${labels[panel]}</figcaption><div class="updated" id="updated-${panel}">Loading…</div>`;
      root.appendChild(figure);
    }
    async function refresh() {
      try {
        const response = await fetch("api/status", {cache: "no-store"});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const status = await response.json();
        for (const screen of status.screens) {
          const image = document.getElementById(`screen-${screen.panel}`);
          const label = document.getElementById(`updated-${screen.panel}`);
          if (screen.available) {
            image.src = `screens/${screen.panel}.jpg?v=${screen.updatedAt}`;
            image.classList.toggle("stale", Date.now() / 1000 - screen.updatedAt > 300);
            label.textContent = `Updated ${new Date(screen.updatedAt * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"})}`;
          } else {
            label.textContent = "Not available yet";
          }
        }
      } catch (error) {
        for (let panel = 1; panel <= 5; panel++) {
          document.getElementById(`updated-${panel}`).textContent = "Connection unavailable";
        }
      }
    }
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


def make_handler(cache_dir):
    cache_dir = Path(cache_dir)

    class TimeGatePreviewHandler(BaseHTTPRequestHandler):
        def send_bytes(self, content, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                self.send_bytes(PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/api/status":
                screens = []
                for panel, label in SCREEN_LABELS.items():
                    screen_path = cache_dir / f"screen-{panel}.jpg"
                    try:
                        updated_at = int(screen_path.stat().st_mtime)
                        available = screen_path.is_file()
                    except OSError:
                        updated_at = 0
                        available = False
                    screens.append({
                        "panel": panel,
                        "label": label,
                        "available": available,
                        "updatedAt": updated_at,
                    })
                payload = json.dumps({
                    "generatedAt": int(time.time()),
                    "screens": screens,
                }).encode("utf-8")
                self.send_bytes(payload, "application/json; charset=utf-8")
                return
            if path.startswith("/screens/") and path.endswith(".jpg"):
                panel = path.removeprefix("/screens/").removesuffix(".jpg")
                if not panel.isdigit() or int(panel) not in SCREEN_LABELS:
                    self.send_error(404)
                    return
                screen_path = (cache_dir / f"screen-{int(panel)}.jpg").resolve()
                if screen_path.parent != cache_dir.resolve() or not screen_path.is_file():
                    self.send_error(404)
                    return
                self.send_bytes(screen_path.read_bytes(), "image/jpeg")
                return
            self.send_error(404)

        def do_POST(self):
            self.send_error(405, "Read-only preview")

        def log_message(self, message, *args):
            print(f"TimeGate web: {message % args}")

    return TimeGatePreviewHandler


def start_timegate_web_server(cache_dir=DEFAULT_CACHE_DIR, port=None):
    port = int(os.environ.get("TIMEGATE_WEB_PORT", "8090")) if port is None else int(port)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(cache_dir))
    thread = threading.Thread(target=server.serve_forever, name="timegate-web", daemon=True)
    thread.start()
    print(f"TimeGate read-only preview listening on http://127.0.0.1:{port}")
    return server, thread
