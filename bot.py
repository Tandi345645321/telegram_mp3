#!/usr/bin/env python3
import threading
import http.server
import socketserver
import json
import time
import logging

from music_bot import run_bot

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        response = json.dumps({"status": "alive", "service": "music bot"}).encode()
        self.wfile.write(response)
    def log_message(self, format, *args):
        pass

def run_http_server():
    with socketserver.TCPServer(("0.0.0.0", 8080), HealthHandler) as httpd:
        logger.info("✅ HTTP-сервер для health checks запущен на порту 8080")
        httpd.serve_forever()

def main():
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    time.sleep(2)  # Даём серверу время запуститься
    logger.info("🚀 Запуск музыкального бота...")
    run_bot()

if __name__ == "__main__":
    main()