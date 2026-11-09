import json
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()
BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
KEY = os.environ["API_KEY"]


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, method=method, data=data,
        headers={"X-API-Key": KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, None
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Retry-After")


call("POST", "/reset", {"slots": [
    {"id": "s1", "provider": "Dr Ade",
     "start": "2026-12-10T09:00:00Z", "end": "2026-12-10T09:30:00Z"}
]})

print("Six attempts for spam@example.com (slot 'nope' does not exist):")
for i in range(1, 7):
    status, retry = call("POST", "/bookings",
                         {"slot_id": "nope", "name": "Spam", "email": "spam@example.com"})
    print(f"  attempt {i}: {status}", f"Retry-After={retry}" if retry else "")

status, _ = call("POST", "/bookings",
                 {"slot_id": "s1", "name": "Other", "email": "other@example.com"})
print("other@example.com:", status)

status, retry = call("POST", "/bookings",
                     {"slot_id": "s1", "name": "Spam", "email": "  SPAM@Example.com "})
print("spam with different casing:", status, f"Retry-After={retry}")