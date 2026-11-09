import hmac
import os
import re
import uuid
import base64
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from psycopg import errors
from psycopg_pool import ConnectionPool
from starlette.exceptions import HTTPException as StarletteHTTPException

load_dotenv()

API_KEY = os.environ["API_KEY"]
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

pool = ConnectionPool(
    os.environ["DATABASE_URL"],
    min_size=1,
    max_size=5,
    open=True,
    kwargs={"prepare_threshold": None},
)

key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def check_key(request: Request, key: str | None = Depends(key_header)):
    if request.url.path == "/app":
        return
    if not key or not hmac.compare_digest(key.encode(), API_KEY.encode()):
        raise HTTPException(status_code=401, detail="missing or invalid API key")

app = FastAPI(dependencies=[Depends(check_key)])

@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def bad_body(request, exc):
    return JSONResponse({"error": "request body is missing or not valid JSON"}, status_code=400)



def parse_ts(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def iso(dt):
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def booking_json(row):
    return {
        "id": row[0],
        "slot_id": row[1],
        "name": row[2],
        "email": row[3],
        "status": row[4],
        "created_at": iso(row[5]),
    }


BOOKING_COLS = "id, slot_id, name, email, status, created_at"


@app.post("/reset")
def reset(body: dict = Body(...)):
    with pool.connection() as conn:
        conn.execute("DELETE FROM bookings")
        conn.execute("DELETE FROM slots")
        conn.execute("DELETE FROM idempotency")
        conn.execute("DELETE FROM attempts")
        for s in body["slots"]:
            conn.execute(
                "INSERT INTO slots (id, provider, start_raw, end_raw, start_ts) "
                "VALUES (%s, %s, %s, %s, %s)",
                (s["id"], s["provider"], s["start"], s["end"], parse_ts(s["start"])),
            )
    return {"ok": True}


@app.get("/slots")
def list_slots(provider: str | None = None):
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.provider, s.start_raw, s.end_raw,
                   NOT EXISTS (SELECT 1 FROM bookings b
                               WHERE b.slot_id = s.id AND b.status = 'confirmed')
            FROM slots s
            WHERE (%s::text IS NULL OR s.provider = %s)
            ORDER BY s.start_ts
            """,
            (provider, provider),
        ).fetchall()
    return {
        "slots": [
            {"id": r[0], "provider": r[1], "start": r[2], "end": r[3], "available": r[4]}
            for r in rows
        ]
    }


@app.post("/bookings", status_code=201)
def create_booking(
    body: dict = Body(...),
    idempotency_key: str | None = Header(default=None),
):
    slot_id = body.get("slot_id")
    slot_id = slot_id if isinstance(slot_id, str) else ""
    name = body.get("name")
    name = name.strip() if isinstance(name, str) else ""
    email = body.get("email")
    email = email.strip().lower() if isinstance(email, str) else ""
    body_hash = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    with pool.connection() as conn:
        if idempotency_key:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (idempotency_key,))
            saved = conn.execute(
                "SELECT body_hash, status, response FROM idempotency WHERE key = %s",
                (idempotency_key,),
            ).fetchone()
            if saved:
                if saved[0] != body_hash:
                    return JSONResponse(
                        {"error": "Idempotency-Key was already used with a different body"},
                        status_code=422,
                    )
                return JSONResponse(saved[2], status_code=saved[1])
                if email:
                    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("rate:" + email,))
            now = datetime.now(timezone.utc)
            conn.execute("DELETE FROM attempts WHERE at < %s", (now - timedelta(minutes=2),))
            recent = conn.execute(
                "SELECT at FROM attempts WHERE email = %s AND at > %s ORDER BY at",
                (email, now - timedelta(seconds=60)),
            ).fetchall()
            if len(recent) >= 5:
                wait = math.ceil((recent[0][0] + timedelta(seconds=60) - now).total_seconds())
                return JSONResponse(
                    {"error": "too many attempts, try again later"},
                    status_code=429,
                    headers={"Retry-After": str(max(1, wait))},
                )
            conn.execute("INSERT INTO attempts (email, at) VALUES (%s, %s)", (email, now))

        if not slot_id or not name or not email:
            return JSONResponse({"error": "slot_id, name and email are required"}, status_code=400)
        if not EMAIL_RE.match(email):
            return JSONResponse({"error": "email is not valid"}, status_code=400)

        slot = conn.execute(
            "SELECT start_ts FROM slots WHERE id = %s", (slot_id,)
        ).fetchone()
        if not slot:
            return JSONResponse({"error": "slot not found"}, status_code=404)

        now = datetime.now(timezone.utc)
        if slot[0] <= now:
            return JSONResponse({"error": "slot has already started"}, status_code=400)

        booking_id = uuid.uuid4().hex
        try:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO bookings (id, slot_id, name, email, status, created_at) "
                    "VALUES (%s, %s, %s, %s, 'confirmed', %s)",
                    (booking_id, slot_id, name, email, now),
                )
        except errors.UniqueViolation:
            return JSONResponse({"error": "slot already booked"}, status_code=409)

        result = booking_json((booking_id, slot_id, name, email, "confirmed", now))
        if idempotency_key:
            conn.execute(
                "INSERT INTO idempotency (key, body_hash, status, response) "
                "VALUES (%s, %s, 201, %s)",
                (idempotency_key, body_hash, json.dumps(result)),
            )
    return JSONResponse(result, status_code=201)

@app.get("/bookings/{booking_id}")
def get_booking(booking_id: str):
    with pool.connection() as conn:
        row = conn.execute(
            f"SELECT {BOOKING_COLS} FROM bookings WHERE id = %s", (booking_id,)
        ).fetchone()
    if not row:
        return JSONResponse({"error": "booking not found"}, status_code=404)
    return booking_json(row)
@app.get("/bookings")
def list_bookings(email: str | None = None, limit: str | None = None, cursor: str | None = None):
    email = (email or "").strip().lower()
    if not email:
        return JSONResponse({"error": "email is required"}, status_code=400)

    if limit is None:
        limit_n = 10
    else:
        try:
            limit_n = int(limit)
        except ValueError:
            return JSONResponse({"error": "limit must be a whole number from 1 to 50"}, status_code=400)
        if not 1 <= limit_n <= 50:
            return JSONResponse({"error": "limit must be a whole number from 1 to 50"}, status_code=400)

    after = 0
    if cursor:
        try:
            after = int(base64.urlsafe_b64decode(cursor.encode()).decode())
        except Exception:
            return JSONResponse({"error": "cursor is not valid"}, status_code=400)

    with pool.connection() as conn:
        rows = conn.execute(
            f"SELECT seq, {BOOKING_COLS} FROM bookings "
            "WHERE email = %s AND seq > %s ORDER BY seq LIMIT %s",
            (email, after, limit_n + 1),
        ).fetchall()

    page = rows[:limit_n]
    next_cursor = None
    if len(rows) > limit_n:
        next_cursor = base64.urlsafe_b64encode(str(page[-1][0]).encode()).decode()
    return {"bookings": [booking_json(r[1:]) for r in page], "next_cursor": next_cursor}

@app.delete("/bookings/{booking_id}")
def cancel_booking(booking_id: str):
    with pool.connection() as conn:
        row = conn.execute(
            f"SELECT {BOOKING_COLS} FROM bookings WHERE id = %s FOR UPDATE",
            (booking_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"error": "booking not found"}, status_code=404)
        if row[4] == "cancelled":
            return JSONResponse({"error": "booking is already cancelled"}, status_code=409)
        conn.execute(
            "UPDATE bookings SET status = 'cancelled' WHERE id = %s", (booking_id,)
        )
    return booking_json((row[0], row[1], row[2], row[3], "cancelled", row[5]))
@app.get("/app", include_in_schema=False)
def web_page():
    here = os.path.dirname(os.path.abspath(__file__))
    return FileResponse(os.path.join(here, "static", "index.html"))