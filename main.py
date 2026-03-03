import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import msal
import requests
import os
from dotenv import load_dotenv
from twilio.rest import Client

# ---- Load .env file ----
load_dotenv()

# ---- Microsoft credentials ----
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TENANT_ID = os.getenv("TENANT_ID")
OWNER_EMAIL = os.getenv("OWNER_EMAIL")

# ---- Odoo credentials ----
ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USER = os.getenv("ODOO_USER")
ODOO_API_KEY = os.getenv("ODOO_API_KEY")

# ---- Twilio credentials ----
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

# ---- App setup ----
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["https://graph.microsoft.com/.default"]

app = FastAPI(title="Tammy Calendar + Odoo API")
from datetime import datetime, timedelta
import pytz
import re
import json
from urllib.parse import quote
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler


def format_datetime(dt_str):
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    adl_tz = pytz.timezone("Australia/Adelaide")
    dt_adl = dt.astimezone(adl_tz)
    return dt_adl.strftime("%d %b %Y, %I:%M %p")


def get_token():
    app_msal = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET,
    )
    result = app_msal.acquire_token_for_client(SCOPES)
    if "access_token" not in result:
        raise Exception(f"Failed to acquire token: {result}")
    return result["access_token"]


# ---- Request Models ----
class AvailabilityRequest(BaseModel):
    start_time: str
    end_time: str
    duration: str


class BookMeetingRequest(BaseModel):
    subject: str
    body: str
    start_time: str
    end_time: str
    attendee: str
    attendee_name: str = "Guest"
    phone: str = ""
    location: str = "Microsoft Teams Meeting"


# ---- Check availability ----
@app.post("/availability")
def check_availability(request: AvailabilityRequest):
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}

    url = f"https://graph.microsoft.com/v1.0/users/{OWNER_EMAIL}/findMeetingTimes"
    start_dt = request.start_time.split("+")[0]
    end_dt = request.end_time.split("+")[0]

    payload = {
        "attendees": [
            {"type": "required", "emailAddress": {"address": OWNER_EMAIL, "name": "Owner"}}
        ],
        "timeConstraint": {
            "timeslots": [
                {
                    "start": {"dateTime": start_dt, "timeZone": "Cen. Australia Standard Time"},
                    "end": {"dateTime": end_dt, "timeZone": "Cen. Australia Standard Time"}
                }
            ]
        },
        "meetingDuration": request.duration
    }

    response = requests.post(url, headers=headers, json=payload)
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.json())
    return response.json()


# ---- Book meeting (Outlook + Odoo sync) ----
@app.post("/book")
def book_meeting(request: BookMeetingRequest):
    """Book Outlook meeting first; only then push to Odoo CRM."""
    token = get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    outlook_url = f"https://graph.microsoft.com/v1.0/users/{OWNER_EMAIL}/events?sendInvitations=true"

    event = {
        "subject": request.subject,
        "body": {
            "contentType": "HTML",
            "content": (
                f"{request.body}<br><br>"
                f"<b>Caller details:</b><br>"
                f"Name: {request.attendee_name}<br>"
                f"Email: {request.attendee}<br>"
                f"Phone: {request.phone}"
            ),
        },
        "start": {"dateTime": request.start_time, "timeZone": "Cen. Australia Standard Time"},
        "end": {"dateTime": request.end_time, "timeZone": "Cen. Australia Standard Time"},
        "location": {"displayName": request.location},
        "attendees": [
            {
                "emailAddress": {"address": request.attendee, "name": request.attendee_name},
                "type": "required",
            }
        ],
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness",
    }

    response = requests.post(outlook_url, headers=headers, json=event)

    if response.status_code != 201:
        raise HTTPException(status_code=response.status_code, detail={
            "error": "Outlook booking failed",
            "response": response.json()
        })

    data = response.json()
    outlook_event_id = data.get("id")
    print(f"[OK] Outlook meeting created successfully: {outlook_event_id}")

    try:
        odoo_event_id = create_odoo_event(
            name=request.attendee_name,
            email=request.attendee,
            phone=request.phone,
            start=request.start_time,
            stop=request.end_time,
            subject=request.subject,
        )
    except Exception as e:
        print(f"[WARN] Outlook booked, but Odoo sync failed: {e}")
        odoo_event_id = None

    phone = _normalize_phone(request.phone)
    if phone:
        try:
            _send_sms(
                phone=phone,
                start_time=data.get("start", {}).get("dateTime", request.start_time),
                end_time=data.get("end", {}).get("dateTime", request.end_time),
                is_reminder=False,
            )
        except Exception as e:
            print(f"[WARN] Immediate SMS failed: {e}")
    else:
        print(f"[WARN] No phone number in request - SMS not sent. Phone was: {repr(request.phone)}")

    return {
        "status": "Outlook meeting booked successfully",
        "outlook_event_id": outlook_event_id,
        "odoo_event_id": odoo_event_id,
        "subject": data.get("subject"),
        "start": data.get("start"),
        "end": data.get("end"),
        "attendee": request.attendee,
        "phone": request.phone,
    }


# ---- Twilio SMS ----
def _normalize_phone(phone: str) -> str:
    """Strip spaces and ensure E.164 format for Twilio."""
    if not phone:
        return ""
    return re.sub(r"\s+", "", phone.strip())


def _send_sms(phone: str, start_time: str, end_time: str, is_reminder: bool = False):
    """Send SMS via Twilio. is_reminder=True uses reminder text, else confirmation."""
    phone = _normalize_phone(phone)
    if not phone:
        raise ValueError("Phone number is empty")
    start_fmt = format_datetime(start_time)
    end_fmt = format_datetime(end_time)
    client = Client(TWILIO_SID, TWILIO_AUTH)

    if is_reminder:
        msg = (
            f"Reminder: Your meeting with Tracey is in 24 hours at "
            f"{start_fmt} to {end_fmt}. "
            f"If you need to make changes, please call: 0483 905 455"
        )
    else:
        msg = (
            f"Your meeting with Tracey has been booked for "
            f"{start_fmt} to {end_fmt}. "
            f"If you need to make changes, please call: 0483 905 455"
        )

    m = client.messages.create(to=phone, from_=TWILIO_NUMBER, body=msg)
    print(f"[SMS SENT] {'Reminder' if is_reminder else 'Confirmation'} to {phone} | Twilio SID: {m.sid}")
    return msg


def create_odoo_event(name, email, phone, start, stop, subject):
    """Create an Odoo calendar event with correctly formatted datetimes."""
    import requests, os
    from datetime import datetime

    ODOO_URL = os.getenv("ODOO_URL")
    ODOO_DB = os.getenv("ODOO_DB")
    ODOO_USER = os.getenv("ODOO_USER")
    ODOO_API_KEY = os.getenv("ODOO_API_KEY")

    auth_payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "service": "common",
            "method": "authenticate",
            "args": [ODOO_DB, ODOO_USER, ODOO_API_KEY, {}],
        },
        "id": 1,
    }
    auth_res = requests.post(f"{ODOO_URL}/jsonrpc", json=auth_payload).json()
    uid = auth_res.get("result")
    if not uid or not isinstance(uid, int):
        raise Exception(f"Authentication failed: {auth_res}")

    print(f"[OK] Authenticated to Odoo as UID {uid}")

    def clean_datetime(dt_str):
        try:
            clean = dt_str.split("T")[0] + " " + dt_str.split("T")[1].split(".")[0]
            return clean.strip()
        except Exception:
            return dt_str.replace("T", " ").split(".")[0]

    start_fmt = clean_datetime(start)
    stop_fmt = clean_datetime(stop)
    print(f"[OK] Converted start={start_fmt}, stop={stop_fmt}")

    create_payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "service": "object",
            "method": "execute_kw",
            "args": [
                ODOO_DB,
                uid,
                ODOO_API_KEY,
                "calendar.event",
                "create",
                [{
                    "name": f"{subject} - {name}",
                    "start": start_fmt,
                    "stop": stop_fmt,
                    "description": f"Email: {email}\nPhone: {phone}",
                }],
            ],
        },
        "id": 2,
    }

    r = requests.post(f"{ODOO_URL}/jsonrpc", json=create_payload).json()

    if "error" in r:
        raise Exception(f"Odoo event creation error: {r['error']}")
    else:
        event_id = r.get("result")
        print(f"[OK] Appointment created in Odoo, event ID: {event_id}")
        return event_id


# ---- 24-hour reminder scheduler ----
REMINDER_SENT_FILE = Path(os.getenv("REMINDER_SENT_FILE", str(Path(__file__).parent / "reminder_sent.json")))


def _load_reminder_sent() -> set:
    try:
        if REMINDER_SENT_FILE.exists():
            with open(REMINDER_SENT_FILE) as f:
                return set(json.load(f))
    except Exception as e:
        print(f"[WARN] Could not load reminder_sent: {e}")
    return set()


def _save_reminder_sent(event_ids: set):
    try:
        REMINDER_SENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(REMINDER_SENT_FILE, "w") as f:
            json.dump(list(event_ids), f)
    except Exception as e:
        print(f"[WARN] Could not save reminder_sent: {e}")


def _parse_phone_from_body(body_html: str) -> str | None:
    if not body_html:
        return None
    match = re.search(r"Phone:\s*([+\d\s\-()]+)", body_html, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _run_24h_reminders(hours_start: float = 23.5, hours_end: float = 24.5):
    try:
        token = get_token()
        headers = {"Authorization": f"Bearer {token}"}

        adl_tz = pytz.timezone("Australia/Adelaide")
        now = datetime.now(adl_tz)
        window_start = now + timedelta(hours=hours_start)
        window_end = now + timedelta(hours=hours_end)

        start_raw = window_start.isoformat()
        end_raw = window_end.isoformat()
        start_str = quote(start_raw, safe=":-")
        end_str = quote(end_raw, safe=":-")

        url = (
            f"https://graph.microsoft.com/v1.0/users/{OWNER_EMAIL}/calendar/calendarView"
            f"?startDateTime={start_str}&endDateTime={end_str}"
        )
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"[WARN] Outlook calendarView failed: {response.status_code} - {response.text[:300]}")
            return

        events = response.json().get("value", [])
        sent = _load_reminder_sent()
        updated = False

        for ev in events:
            event_id = ev.get("id")
            if not event_id or event_id in sent:
                continue

            body_html = (ev.get("body") or {}).get("content") or ""
            phone = _parse_phone_from_body(body_html)
            if not phone:
                continue

            start_dt = (ev.get("start") or {}).get("dateTime")
            end_dt = (ev.get("end") or {}).get("dateTime")
            if not start_dt or not end_dt:
                continue

            try:
                _send_sms(phone=phone, start_time=start_dt, end_time=end_dt, is_reminder=True)
                sent.add(event_id)
                updated = True
            except Exception as e:
                print(f"[WARN] Reminder SMS failed for {event_id}: {e}")

        if updated:
            _save_reminder_sent(sent)
    except Exception as e:
        print(f"[WARN] 24h reminder job failed: {e}")


scheduler = BackgroundScheduler(timezone=pytz.timezone("Australia/Adelaide"))
scheduler.add_job(_run_24h_reminders, "interval", hours=1, id="reminder_24h")
scheduler.start()


@app.post("/test-reminder")
def test_reminder(hours_start: float = 0.08, hours_end: float = 0.25):
    """Manually trigger reminder job for testing."""
    _run_24h_reminders(hours_start=hours_start, hours_end=hours_end)
    return {"status": "Reminder job completed (check logs for SMS sent)"}


@app.get("/test-odoo")
def test_odoo():
    import requests, os
    url = f"{os.getenv('ODOO_URL')}/jsonrpc"
    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "service": "common",
            "method": "version",
            "args": []
        },
        "id": 1
    }
    r = requests.post(url, json=payload)
    return {"response": r.json()}
