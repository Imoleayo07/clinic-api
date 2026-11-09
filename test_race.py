import json
import os
import threading
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()
BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
KEY = os.environ["API_KEY"]


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=data,
        headers={"X-API-Key": KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


call("POST", "/reset", {"slots": [
    {"id": "s1", "provider": "Dr Ade",
     "start": "2026-12-10T09:00:00Z", "end": "2026-12-10T09:30:00Z"}
]})

results = []


def go(i):
    results.append(call("POST", "/bookings",
                        {"slot_id": "s1", "name": "Racer", "email": f"racer{i}@example.com"}))


threads = [threading.Thread(target=go, args=(i,)) for i in range(20)]
for t in threads:
    t.start()
for t in threads:
    t.join()

print(sorted(results))