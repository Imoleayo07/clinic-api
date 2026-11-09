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
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


slots = [
    {"id": f"x{i}", "provider": "Dr Ade",
     "start": f"2026-12-11T0{i}:00:00Z", "end": f"2026-12-11T0{i}:30:00Z"}
    for i in range(1, 6)
]
call("POST", "/reset", {"slots": slots})
for i in range(1, 6):
    call("POST", "/bookings", {"slot_id": f"x{i}", "name": "Pat", "email": "Pat@Example.com"})

s, page1 = call("GET", "/bookings?email=PAT@example.com&limit=2")
s, page2 = call("GET", "/bookings?email=pat@example.com&limit=2&cursor=" + page1["next_cursor"])
s, page3 = call("GET", "/bookings?email=pat@example.com&limit=2&cursor=" + page2["next_cursor"])

print("page sizes:", len(page1["bookings"]), len(page2["bookings"]), len(page3["bookings"]))
print("last next_cursor:", page3["next_cursor"])
print("oldest first:", [b["slot_id"] for b in page1["bookings"] + page2["bookings"] + page3["bookings"]])
print("limit=0:", call("GET", "/bookings?email=pat@example.com&limit=0")[0])
print("limit=51:", call("GET", "/bookings?email=pat@example.com&limit=51")[0])
print("no email:", call("GET", "/bookings")[0])