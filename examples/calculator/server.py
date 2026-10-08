"""Small standalone calculator service. Python standard library only."""
import argparse
from decimal import Decimal, InvalidOperation, localcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?\Z")
OPERATIONS = {"add": "+", "subtract": "-", "multiply": "*", "divide": "/"}

def parse_number(value):
    if not isinstance(value, str) or len(value) > 64 or not NUMBER.fullmatch(value.strip()):
        raise ValueError("Enter two valid numbers.")
    number = Decimal(value.strip())
    if not number.is_finite() or abs(number) > Decimal("1e50"):
        raise ValueError("Use numbers between -1e50 and 1e50.")
    return number

def calculate(payload):
    if not isinstance(payload, dict) or set(payload) != {"a", "b", "operation"}:
        raise ValueError("Provide two numbers and an operation.")
    operation = payload["operation"]
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise ValueError("Choose addition, subtraction, multiplication or division.")
    a, b = parse_number(payload["a"]), parse_number(payload["b"])
    with localcontext() as context:
        context.prec = 28
        if operation == "add": result = a + b
        elif operation == "subtract": result = a - b
        elif operation == "multiply": result = a * b
        else:
            if b == 0:
                raise ValueError("Cannot divide by zero. Try another number.")
            result = a / b
        if result == 0:
            text = "0"
        elif Decimal("1e-12") <= abs(result) <= Decimal("1e18"):
            text = format(result, "f")
            if "." in text:
                text = text.rstrip("0").rstrip(".")
        else:
            text = str(result.normalize())
        return {"result": text, "precision": 28}

class Handler(BaseHTTPRequestHandler):
    server_version = "Calculator"
    sys_version = ""

    def log_message(self, *args):
        pass

    def send(self, status, body, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
        self.end_headers()
        self.wfile.write(body)

    def json(self, status, payload):
        self.send(status, json.dumps(payload).encode())

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            return self.json(200, {"status": "healthy", "service": "calculator",
                                  "region": os.environ.get("DEPLOYMENT_REGION", "local")})
        files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                 "/style.css": ("style.css", "text/css"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}
        if path not in files:
            return self.json(404, {"error": "Not found"})
        filename, kind = files[path]
        self.send(200, (ROOT / filename).read_bytes(), kind + "; charset=utf-8")

    def do_POST(self):
        if urlsplit(self.path).path != "/api/calculate":
            return self.json(404, {"error": "Not found"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 4096:
                return self.json(413, {"error": "Use a small calculation request."})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self.json(415, {"error": "Send JSON."})
            self.json(200, calculate(json.loads(self.rfile.read(size))))
        except (ValueError, InvalidOperation, UnicodeDecodeError):
            # Validation messages are fixed strings; arbitrary request content is not echoed.
            try:
                self.json(400, {"error": "Enter valid numbers and an operation. Division by zero is not allowed."})
            except (BrokenPipeError, ConnectionResetError):
                pass

def create_server(host="0.0.0.0", port=8080):
    return ThreadingHTTPServer((host, port), Handler)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    server = create_server(args.host, args.port)
    print("Calculator listening on port", server.server_port, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
