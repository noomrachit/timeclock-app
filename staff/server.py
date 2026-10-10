"""
ระบบลงเวลาพนักงาน (หลายคน) — aiohttp + PostgreSQL

- พนักงานเข้าระบบด้วย รหัสพนักงาน + PIN แล้วลงวันนี้: ทำงาน (ตามกะ 1 คลิก) / ลา / ขาด / อื่นๆ (ใส่เวลาเอง)
- พนักงานแก้ได้เฉพาะวันนี้ และแก้ไม่ได้ถ้าแอดมินแก้วันนั้นแล้ว · วันเสาร์ลงไม่ได้
- ค่าแรงรายชั่วโมง: อา–พฤ 100%×8, ศ 100%×3 แล้ว 125%×2, 150%×1, 175%×1, 200% ที่เหลือ (ปัดลงทีละ 30 นาที)
- แอดมิน (รหัสผ่านจาก ADMIN_PASSWORD) จัดการพนักงาน ดูวันนี้ ดูสรุปรอบ แก้เวลา ส่งออก CSV

Environment:
  DATABASE_URL     = postgres connection string (บังคับ)
  ADMIN_PASSWORD   = รหัสผ่านแอดมิน อย่างน้อย 10 ตัวอักษร (บังคับ)
  PORT             = พอร์ต (Railway ตั้งให้เอง, ค่าเริ่ม 8080)
  TZ_NAME          = โซนเวลา (ค่าเริ่ม Asia/Jerusalem)
  COOKIE_SECURE    = 0 เพื่อปิด Secure cookie ตอนทดสอบบน http ในเครื่อง (ค่าเริ่ม 1)
"""

import asyncio
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("staff-timeclock")

TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Jerusalem"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1") != "0"
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")

CUTOFF = 21                 # รอบเงินเดือน: วันที่ 21 ถึงวันที่ 20 ของเดือนถัดไป
ROUND_MIN = 30              # ปัดชั่วโมงทำงานลงทีละ 30 นาที
TIERS = (100, 125, 150, 175, 200)
PIN_MAX_FAILS = 5
PIN_LOCK_MIN = 15
EMP_SESSION_DAYS = 180
ADMIN_SESSION_HOURS = 12
TH_DAYS = ["จ", "อ", "พ", "พฤ", "ศ", "ส", "อา"]  # ตาม date.weekday()


# ---------- กติกากะงาน ----------
def shift_rule(d: date):
    """อา–พฤ 06:00–16:00 (ปกติ 8 + 125% 2 ชม.) · ศ 06:00–11:00 (ปกติ 3 + 125% 2 ชม.) · ส หยุด (ลงงานไม่ได้)"""
    wd = d.weekday()  # จ=0 ... อา=6
    if wd == 5:
        return {"in": None, "out": None, "base": 0}
    if wd == 4:
        return {"in": "06:00", "out": "11:00", "base": 3}
    return {"in": "06:00", "out": "16:00", "base": 8}


def is_holiday(d: date) -> bool:
    return d.weekday() == 5


def tier_hours(d: date, in_at, out_at):
    """แบ่งชั่วโมงทำงานตามอัตรา: ปกติ (8 หรือ 3) → 125% 2 ชม. → 150% 1 → 175% 1 → 200% ที่เหลือทั้งหมด
    ชั่วโมงรวมปัดลงทีละ 30 นาที"""
    out = {t: 0.0 for t in TIERS}
    if not in_at or not out_at or out_at <= in_at:
        return out
    mins = int((out_at - in_at).total_seconds() // 60)
    rem = (mins - mins % ROUND_MIN) / 60
    caps = (shift_rule(d)["base"], 2, 1, 1, float("inf"))
    for t, cap in zip(TIERS, caps):
        take = min(rem, cap)
        out[t] = take
        rem -= take
    return out


def units_of(tiers: dict) -> float:
    """ชั่วโมงเทียบเท่า (ชั่วโมง × อัตรา) ใช้คูณค่าแรงต่อชั่วโมง"""
    return sum(h * t / 100 for t, h in tiers.items())


def now_local() -> datetime:
    return datetime.now(TZ)


def period_for(today: date, offset: int = 0):
    y, m = today.year, today.month
    if today.day < CUTOFF:
        m -= 1
    m += offset
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    start = date(y, m, CUTOFF)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    end = date(ny, nm, CUTOFF - 1)
    return start, end


def daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def fmt_t(ts):
    return ts.astimezone(TZ).strftime("%H:%M") if ts else None


def at(d: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


# ---------- รหัสผ่าน / PIN ----------
def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_pin(pin: str, stored: str) -> bool:
    try:
        _, salt_hex, h_hex = stored.split("$")
        h = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(h.hex(), h_hex)
    except Exception:
        return False


def new_pin() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def token_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


# ---------- ฐานข้อมูล ----------
SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id SERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    pin_hash TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    failed_attempts INT NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS days (
    employee_id INT NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    day DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'work' CHECK (status IN ('work','absent','leave')),
    in_at TIMESTAMPTZ,
    out_at TIMESTAMPTZ,
    edited_by_admin BOOLEAN NOT NULL DEFAULT FALSE,
    note TEXT,
    PRIMARY KEY (employee_id, day)
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('emp','admin')),
    employee_id INT REFERENCES employees(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS days_day_idx ON days(day);
ALTER TABLE days ADD COLUMN IF NOT EXISTS edited_by_emp BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE days ADD COLUMN IF NOT EXISTS leave_hours NUMERIC(5,1) NOT NULL DEFAULT 0;
ALTER TABLE days DROP CONSTRAINT IF EXISTS days_status_check;
ALTER TABLE days ADD CONSTRAINT days_status_check CHECK (status IN ('work','absent','leave','holiday'));
"""


def _db_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


async def init_db(app):
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("ต้องตั้งค่า DATABASE_URL")
    if len(ADMIN_PASSWORD) < 10:
        raise RuntimeError("ต้องตั้งค่า ADMIN_PASSWORD อย่างน้อย 10 ตัวอักษร")
    pool = await asyncpg.create_pool(_db_url(url), min_size=1, max_size=10)
    async with pool.acquire() as c:
        await c.execute(SCHEMA)
        await c.execute("DELETE FROM sessions WHERE expires_at < now()")
    app["db"] = pool
    log.info("เชื่อมฐานข้อมูลแล้ว")


async def close_db(app):
    await app["db"].close()


async def get_rate(db) -> float:
    v = await db.fetchval("SELECT value FROM settings WHERE key='hourly_rate'")
    try:
        return float(v or 0)
    except ValueError:
        return 0.0


# ---------- ความปลอดภัย ----------
_ip_hits: dict = {}


def client_ip(req) -> str:
    fwd = req.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (req.remote or "?")


def ip_limited(req, bucket: str, limit: int, window: int) -> bool:
    """คืน True ถ้าเกินโควตา (limit ครั้ง ต่อ window วินาที)"""
    key = (bucket, client_ip(req))
    nowt = time.monotonic()
    hits = [t for t in _ip_hits.get(key, []) if nowt - t < window]
    hits.append(nowt)
    _ip_hits[key] = hits
    if len(_ip_hits) > 20000:
        _ip_hits.clear()
    return len(hits) > limit


@web.middleware
async def security_mw(req, handler):
    if req.method == "POST" and req.path.startswith("/api/"):
        # กัน cross-site form post: ต้องส่งเป็น JSON พร้อม header นี้ (เบราว์เซอร์ส่งข้ามโดเมนเองไม่ได้)
        if req.headers.get("X-Requested-With") != "timeclock":
            return web.json_response({"error": "คำขอไม่ถูกต้อง"}, status=403)
    try:
        resp = await handler(req)
    except web.HTTPException as e:
        resp = e
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    )
    if req.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def set_cookie(resp, name, tok, max_age):
    resp.set_cookie(name, tok, max_age=max_age, httponly=True, secure=COOKIE_SECURE,
                    samesite="Lax", path="/")


async def new_session(db, kind, employee_id, seconds):
    tok = secrets.token_urlsafe(32)
    await db.execute(
        "INSERT INTO sessions(token_hash, kind, employee_id, expires_at) VALUES($1,$2,$3, now() + $4::interval)",
        token_hash(tok), kind, employee_id, timedelta(seconds=seconds),
    )
    return tok


async def session_of(req, kind):
    name = "tc_emp" if kind == "emp" else "tc_admin"
    tok = req.cookies.get(name)
    if not tok:
        return None
    return await req.app["db"].fetchrow(
        "SELECT s.employee_id, e.name, e.code, e.active FROM sessions s "
        "LEFT JOIN employees e ON e.id = s.employee_id "
        "WHERE s.token_hash=$1 AND s.kind=$2 AND s.expires_at > now()",
        token_hash(tok), kind,
    )


def need(kind):
    def deco(fn):
        async def wrapper(req):
            s = await session_of(req, kind)
            if not s or (kind == "emp" and not s["active"]):
                return web.json_response({"error": "กรุณาเข้าสู่ระบบ"}, status=401)
            req["sess"] = s
            return await fn(req)
        return wrapper
    return deco


async def body(req) -> dict:
    try:
        d = await req.json()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


# ---------- API พนักงาน ----------
async def emp_login(req):
    if ip_limited(req, "emp_login", 30, 600):
        return web.json_response({"error": "ลองบ่อยเกินไป รอสักครู่"}, status=429)
    d = await body(req)
    code = str(d.get("code", "")).strip().upper()
    pin = str(d.get("pin", "")).strip()
    db = req.app["db"]
    e = await db.fetchrow("SELECT * FROM employees WHERE code=$1", code)
    if not e or not e["active"]:
        await asyncio.sleep(0.5)
        return web.json_response({"error": "รหัสพนักงานหรือ PIN ไม่ถูกต้อง"}, status=401)
    if e["locked_until"] and e["locked_until"] > datetime.now(TZ):
        mins = int((e["locked_until"] - datetime.now(TZ)).total_seconds() // 60) + 1
        return web.json_response({"error": f"ใส่ PIN ผิดหลายครั้ง ล็อกอีก {mins} นาที"}, status=423)
    if not check_pin(pin, e["pin_hash"]):
        fails = e["failed_attempts"] + 1
        if fails >= PIN_MAX_FAILS:
            await db.execute(
                "UPDATE employees SET failed_attempts=0, locked_until=now() + $2::interval WHERE id=$1",
                e["id"], timedelta(minutes=PIN_LOCK_MIN))
            return web.json_response({"error": f"ใส่ PIN ผิด {PIN_MAX_FAILS} ครั้ง ล็อก {PIN_LOCK_MIN} นาที"}, status=423)
        await db.execute("UPDATE employees SET failed_attempts=$2 WHERE id=$1", e["id"], fails)
        return web.json_response({"error": f"รหัสพนักงานหรือ PIN ไม่ถูกต้อง (เหลือ {PIN_MAX_FAILS - fails} ครั้ง)"}, status=401)
    await db.execute("UPDATE employees SET failed_attempts=0, locked_until=NULL WHERE id=$1", e["id"])
    tok = await new_session(db, "emp", e["id"], EMP_SESSION_DAYS * 86400)
    resp = web.json_response({"ok": True})
    set_cookie(resp, "tc_emp", tok, EMP_SESSION_DAYS * 86400)
    return resp


async def logout(req):
    db = req.app["db"]
    resp = web.json_response({"ok": True})
    for name in ("tc_emp", "tc_admin"):
        tok = req.cookies.get(name)
        if tok:
            await db.execute("DELETE FROM sessions WHERE token_hash=$1", token_hash(tok))
            resp.del_cookie(name, path="/")
    return resp


def day_view(d: date, rec):
    r = shift_rule(d)
    status = rec["status"] if rec else None
    in_at = rec["in_at"] if rec else None
    out_at = rec["out_at"] if rec else None
    tiers = tier_hours(d, in_at, out_at) if status == "work" else {t: 0.0 for t in TIERS}
    return {
        "date": d.isoformat(),
        "dow": TH_DAYS[d.weekday()],
        "shift_in": r["in"], "shift_out": r["out"], "holiday": is_holiday(d),
        "status": status,
        "in": fmt_t(in_at), "out": fmt_t(out_at),
        "hours": {str(t): h for t, h in tiers.items()},
        "units": units_of(tiers),
        "leave_hours": float(rec["leave_hours"]) if rec else 0.0,
        "note": rec["note"] if rec else None,
        "edited": bool(rec and rec["edited_by_admin"]),
        "self_edited": bool(rec and rec["edited_by_emp"]),
    }


def summarize(views):
    s = {"worked": 0, "absent": 0, "leave": 0, "holiday": 0, "units": 0.0, "leave_hours": 0.0,
         "hours": {str(t): 0.0 for t in TIERS}}
    for v in views:
        st = v["status"]
        if st == "work":
            s["worked"] += 1
            s["units"] += v["units"]
            for t in TIERS:
                s["hours"][str(t)] += v["hours"][str(t)]
        elif st in ("absent", "leave", "holiday"):
            s[st] += 1
        s["leave_hours"] += v["leave_hours"]
    return s


@need("emp")
async def emp_me(req):
    s = req["sess"]
    db = req.app["db"]
    now = now_local()
    today = now.date()
    try:
        offset = min(0, int(req.query.get("offset", "0")))
    except ValueError:
        offset = 0
    start, end = period_for(today, offset)
    rows = await db.fetch(
        "SELECT * FROM days WHERE employee_id=$1 AND day BETWEEN $2 AND $3", s["employee_id"], start, end)
    by = {r["day"]: r for r in rows}
    today_rec = by.get(today) if start <= today <= end else await db.fetchrow(
        "SELECT * FROM days WHERE employee_id=$1 AND day=$2", s["employee_id"], today)
    lst = [day_view(d, by.get(d)) for d in daterange(start, end)]
    sm = summarize(lst)
    rate = await get_rate(db)
    sm["rate"] = rate
    sm["pay"] = (sm["units"] + sm["leave_hours"]) * rate
    return web.json_response({
        "name": s["name"], "code": s["code"],
        "now": now.strftime("%H:%M:%S"), "today": day_view(today, today_rec),
        "period": {"start": start.isoformat(), "end": end.isoformat(), "offset": offset},
        "days": lst, "summary": sm,
    })


TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def parse_times(day: date, tin, tout):
    """คืน (in_at, out_at) หรือยก ValueError พร้อมข้อความ"""
    for v in (tin, tout):
        if not v or not TIME_RE.match(str(v)):
            raise ValueError("ต้องใส่เวลาเข้าและเวลาออก รูปแบบ ชช:นน")
    in_at, out_at = at(day, str(tin)), at(day, str(tout))
    if out_at <= in_at:
        raise ValueError("เวลาออกต้องหลังเวลาเข้า")
    return in_at, out_at


@need("emp")
async def emp_day(req):
    """พนักงานลงวันนี้: work (ตามกะ 1 คลิก) / leave / absent / other (ใส่เวลาเอง) — แก้ได้เฉพาะวันนี้"""
    d = await body(req)
    action = d.get("action")
    if action not in ("work", "leave", "absent", "other"):
        return web.json_response({"error": "คำขอไม่ถูกต้อง"}, status=400)
    if ip_limited(req, "emp_day", 20, 60):
        return web.json_response({"error": "กดถี่เกินไป"}, status=429)
    db = req.app["db"]
    eid = req["sess"]["employee_id"]
    today = now_local().date()
    if is_holiday(today):
        return web.json_response({"error": "วันเสาร์เป็นวันหยุด ลงเวลาไม่ได้"}, status=409)
    r = shift_rule(today)
    note = str(d.get("note", "")).strip()[:200] or None
    in_at = out_at = None
    status = action
    if action == "work":
        in_at, out_at = at(today, r["in"]), at(today, r["out"])
    elif action == "other":
        status = "work"
        try:
            in_at, out_at = parse_times(today, d.get("in"), d.get("out"))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
    async with db.acquire() as c, c.transaction():
        rec = await c.fetchrow("SELECT edited_by_admin FROM days WHERE employee_id=$1 AND day=$2 FOR UPDATE", eid, today)
        if rec and rec["edited_by_admin"]:
            return web.json_response({"error": "แอดมินแก้วันนี้แล้ว ติดต่อแอดมินหากต้องการเปลี่ยน"}, status=409)
        await c.execute(
            "INSERT INTO days(employee_id, day, status, in_at, out_at, edited_by_emp, note, leave_hours) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,0) ON CONFLICT (employee_id, day) DO UPDATE SET status=EXCLUDED.status, "
            "in_at=EXCLUDED.in_at, out_at=EXCLUDED.out_at, edited_by_emp=EXCLUDED.edited_by_emp, note=EXCLUDED.note",
            eid, today, status, in_at, out_at, action == "other", note)
    return web.json_response({"ok": True})


# ---------- API แอดมิน ----------
async def admin_login(req):
    if ip_limited(req, "admin_login", 10, 900):
        return web.json_response({"error": "ลองบ่อยเกินไป รอ 15 นาที"}, status=429)
    d = await body(req)
    pw = str(d.get("password", ""))
    if not hmac.compare_digest(pw.encode(), ADMIN_PASSWORD.encode()):
        await asyncio.sleep(1)
        return web.json_response({"error": "รหัสผ่านไม่ถูกต้อง"}, status=401)
    tok = await new_session(req.app["db"], "admin", None, ADMIN_SESSION_HOURS * 3600)
    resp = web.json_response({"ok": True})
    set_cookie(resp, "tc_admin", tok, ADMIN_SESSION_HOURS * 3600)
    return resp


CODE_RE = re.compile(r"^[A-Z0-9-]{1,20}$")


@need("admin")
async def admin_employees(req):
    rows = await req.app["db"].fetch("SELECT id, code, name, active, locked_until FROM employees ORDER BY active DESC, code")
    return web.json_response({"employees": [
        {"id": r["id"], "code": r["code"], "name": r["name"], "active": r["active"],
         "locked": bool(r["locked_until"] and r["locked_until"] > datetime.now(TZ))} for r in rows]})


@need("admin")
async def admin_add_employee(req):
    d = await body(req)
    code = str(d.get("code", "")).strip().upper()
    name = str(d.get("name", "")).strip()[:80]
    if not CODE_RE.match(code) or not name:
        return web.json_response({"error": "รหัสพนักงานใช้ได้แค่ A-Z 0-9 และ - (ไม่เกิน 20 ตัว) และต้องมีชื่อ"}, status=400)
    pin = new_pin()
    try:
        await req.app["db"].execute("INSERT INTO employees(code, name, pin_hash) VALUES($1,$2,$3)", code, name, hash_pin(pin))
    except asyncpg.UniqueViolationError:
        return web.json_response({"error": "รหัสพนักงานนี้มีอยู่แล้ว"}, status=409)
    return web.json_response({"ok": True, "code": code, "pin": pin})


@need("admin")
async def admin_update_employee(req):
    d = await body(req)
    db = req.app["db"]
    try:
        eid = int(d.get("id"))
    except (TypeError, ValueError):
        return web.json_response({"error": "คำขอไม่ถูกต้อง"}, status=400)
    action = d.get("action")
    if action == "reset_pin":
        pin = new_pin()
        n = await db.execute(
            "UPDATE employees SET pin_hash=$2, failed_attempts=0, locked_until=NULL WHERE id=$1", eid, hash_pin(pin))
        await db.execute("DELETE FROM sessions WHERE employee_id=$1", eid)
        return web.json_response({"ok": n.endswith("1"), "pin": pin})
    if action in ("deactivate", "activate"):
        await db.execute("UPDATE employees SET active=$2 WHERE id=$1", eid, action == "activate")
        if action == "deactivate":
            await db.execute("DELETE FROM sessions WHERE employee_id=$1", eid)
        return web.json_response({"ok": True})
    if action == "rename":
        name = str(d.get("name", "")).strip()[:80]
        if not name:
            return web.json_response({"error": "ต้องมีชื่อ"}, status=400)
        await db.execute("UPDATE employees SET name=$2 WHERE id=$1", eid, name)
        return web.json_response({"ok": True})
    return web.json_response({"error": "คำขอไม่ถูกต้อง"}, status=400)


@need("admin")
async def admin_today(req):
    db = req.app["db"]
    today = now_local().date()
    rows = await db.fetch(
        "SELECT e.id, e.code, e.name, d.* FROM employees e "
        "LEFT JOIN days d ON d.employee_id=e.id AND d.day=$1 WHERE e.active ORDER BY e.code", today)
    r = shift_rule(today)
    out = []
    for x in rows:
        v = day_view(today, x if x["status"] else None)
        v.update({"id": x["id"], "code": x["code"], "name": x["name"]})
        out.append(v)
    return web.json_response({"date": today.isoformat(), "dow": TH_DAYS[today.weekday()],
                              "shift_in": r["in"], "shift_out": r["out"], "holiday": is_holiday(today), "rows": out})


async def _period_report(db, offset):
    today = now_local().date()
    start, end = period_for(today, offset)
    emps = await db.fetch("SELECT id, code, name, active, created_at FROM employees ORDER BY code")
    rows = await db.fetch("SELECT * FROM days WHERE day BETWEEN $1 AND $2", start, end)
    by = {}
    for r in rows:
        by.setdefault(r["employee_id"], {})[r["day"]] = r
    rate = await get_rate(db)
    report = []
    for e in emps:
        recs = by.get(e["id"], {})
        if not e["active"] and not recs:
            continue
        joined = e["created_at"].astimezone(TZ).date()
        first = min([joined] + list(recs.keys()))  # ไม่นับขาดก่อนวันที่เพิ่มพนักงาน
        views = [day_view(d, recs.get(d)) for d in daterange(start, end)]
        sm = summarize(views)
        missing = 0
        for v in views:
            dd = date.fromisoformat(v["date"])
            if v["status"] is None and not v["holiday"] and first <= dd < today:
                missing += 1  # วันทำงานที่ผ่านไปแล้วไม่มีการลง
        sm.update({"id": e["id"], "code": e["code"], "name": e["name"], "active": e["active"],
                   "missing": missing, "pay": (sm["units"] + sm["leave_hours"]) * rate})
        report.append(sm)
    return start, end, rate, report


@need("admin")
async def admin_report(req):
    try:
        offset = min(0, int(req.query.get("offset", "0")))
    except ValueError:
        offset = 0
    start, end, rate, report = await _period_report(req.app["db"], offset)
    return web.json_response({"period": {"start": start.isoformat(), "end": end.isoformat(), "offset": offset},
                              "rate": rate, "rows": report})


def _g(x):
    return f"{x:g}"


@need("admin")
async def admin_report_csv(req):
    try:
        offset = min(0, int(req.query.get("offset", "0")))
    except ValueError:
        offset = 0
    start, end, rate, report = await _period_report(req.app["db"], offset)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"รอบ {start.isoformat()} ถึง {end.isoformat()}", f"ค่าแรง/ชั่วโมง {rate:g}"])
    w.writerow(["รหัส", "ชื่อ", "มาทำงาน", "ชม.100%", "ชม.125%", "ชม.150%", "ชม.175%", "ชม.200%",
                "ชม.ลาที่ได้เงิน", "ชม.เทียบเท่า", "ขาด", "ลา", "วันหยุด", "ไม่ได้ลง", "ยอดเงิน"])
    for r in report:
        name = ("'" + r["name"]) if r["name"][:1] in "=+-@" else r["name"]  # กันสูตรใน Excel
        h = r["hours"]
        w.writerow([r["code"], name, r["worked"], _g(h["100"]), _g(h["125"]), _g(h["150"]), _g(h["175"]), _g(h["200"]),
                    _g(r["leave_hours"]), _g(r["units"] + r["leave_hours"]), r["absent"], r["leave"], r["holiday"],
                    r["missing"], f"{r['pay']:.2f}"])
    data = "﻿" + buf.getvalue()  # BOM ให้ Excel อ่านภาษาไทยถูก
    return web.Response(body=data.encode("utf-8"), content_type="text/csv", charset="utf-8",
                        headers={"Content-Disposition": f'attachment; filename="timeclock-{start.isoformat()}.csv"'})


@need("admin")
async def admin_employee_days(req):
    try:
        eid = int(req.query.get("id"))
        offset = min(0, int(req.query.get("offset", "0")))
    except (TypeError, ValueError):
        return web.json_response({"error": "คำขอไม่ถูกต้อง"}, status=400)
    db = req.app["db"]
    e = await db.fetchrow("SELECT id, code, name FROM employees WHERE id=$1", eid)
    if not e:
        return web.json_response({"error": "ไม่พบพนักงาน"}, status=404)
    start, end = period_for(now_local().date(), offset)
    rows = await db.fetch("SELECT * FROM days WHERE employee_id=$1 AND day BETWEEN $2 AND $3", eid, start, end)
    by = {r["day"]: r for r in rows}
    return web.json_response({"employee": dict(e), "period": {"start": start.isoformat(), "end": end.isoformat()},
                              "days": [day_view(d, by.get(d)) for d in daterange(start, end)]})


@need("admin")
async def admin_set_day(req):
    """แอดมินแก้วัน: work (ตามกะอัตโนมัติ) / edit (ใส่เวลาเอง) / absent / leave (+ชม.ลาที่ได้เงิน) / holiday / clear"""
    d = await body(req)
    try:
        eid = int(d.get("id"))
        day = date.fromisoformat(str(d.get("date")))
    except (TypeError, ValueError):
        return web.json_response({"error": "คำขอไม่ถูกต้อง"}, status=400)
    action = d.get("status")
    db = req.app["db"]
    if action == "clear":
        await db.execute("DELETE FROM days WHERE employee_id=$1 AND day=$2", eid, day)
        return web.json_response({"ok": True})
    if action not in ("work", "edit", "absent", "leave", "holiday"):
        return web.json_response({"error": "สถานะไม่ถูกต้อง"}, status=400)
    if action in ("work", "edit") and is_holiday(day):
        return web.json_response({"error": "วันเสาร์เป็นวันหยุด ลงทำงานไม่ได้"}, status=400)
    note = str(d.get("note", "")).strip()[:200] or None
    in_at = out_at = None
    leave_hours = 0.0
    status = action
    if action == "work":
        r = shift_rule(day)
        in_at, out_at = at(day, r["in"]), at(day, r["out"])
    elif action == "edit":
        status = "work"
        try:
            in_at, out_at = parse_times(day, d.get("in"), d.get("out"))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
    elif action == "leave":
        try:
            leave_hours = float(d.get("leave_hours") or 0)
            if not 0 <= leave_hours <= 24 or (leave_hours * 2) % 1:
                raise ValueError
        except (TypeError, ValueError):
            return web.json_response({"error": "ชั่วโมงลาต้องเป็น 0–24 ทีละครึ่งชั่วโมง"}, status=400)
    await db.execute(
        "INSERT INTO days(employee_id, day, status, in_at, out_at, edited_by_admin, edited_by_emp, note, leave_hours) "
        "VALUES($1,$2,$3,$4,$5,TRUE,FALSE,$6,$7) ON CONFLICT (employee_id, day) DO UPDATE SET status=EXCLUDED.status, "
        "in_at=EXCLUDED.in_at, out_at=EXCLUDED.out_at, edited_by_admin=TRUE, edited_by_emp=FALSE, "
        "note=EXCLUDED.note, leave_hours=EXCLUDED.leave_hours",
        eid, day, status, in_at, out_at, note, leave_hours)
    return web.json_response({"ok": True})


@need("admin")
async def admin_set_rate(req):
    d = await body(req)
    try:
        rate = float(d.get("rate"))
        if not 0 <= rate <= 10000:
            raise ValueError
    except (TypeError, ValueError):
        return web.json_response({"error": "ค่าแรงไม่ถูกต้อง"}, status=400)
    await req.app["db"].execute(
        "INSERT INTO settings(key, value) VALUES('hourly_rate',$1) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        f"{rate:g}")
    return web.json_response({"ok": True})


@need("admin")
async def admin_me(req):
    return web.json_response({"ok": True})


# ---------- หน้าเว็บ ----------
def page(name):
    async def h(req):
        return web.FileResponse(os.path.join(STATIC, name), headers={"Cache-Control": "no-cache"})
    return h


async def health(req):
    await req.app["db"].fetchval("SELECT 1")
    return web.Response(text="OK")


def make_app():
    app = web.Application(middlewares=[security_mw], client_max_size=64 * 1024)
    app.on_startup.append(init_db)
    app.on_cleanup.append(close_db)
    r = app.router
    r.add_get("/", page("index.html"))
    r.add_get("/admin", page("admin.html"))
    r.add_get("/health", health)
    r.add_post("/api/login", emp_login)
    r.add_post("/api/logout", logout)
    r.add_get("/api/me", emp_me)
    r.add_post("/api/day", emp_day)
    r.add_post("/api/admin/login", admin_login)
    r.add_get("/api/admin/me", admin_me)
    r.add_get("/api/admin/employees", admin_employees)
    r.add_post("/api/admin/employees", admin_add_employee)
    r.add_post("/api/admin/employee", admin_update_employee)
    r.add_get("/api/admin/today", admin_today)
    r.add_get("/api/admin/report", admin_report)
    r.add_get("/api/admin/report.csv", admin_report_csv)
    r.add_get("/api/admin/days", admin_employee_days)
    r.add_post("/api/admin/day", admin_set_day)
    r.add_post("/api/admin/rate", admin_set_rate)
    r.add_static("/static/", STATIC, show_index=False)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
