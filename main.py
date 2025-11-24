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
ODOO_URL = os.getenv("ODOO_URL")              # e.g. https://execconnect.odoo.com
ODOO_DB = os.getenv("ODOO_DB")                # e.g. execconnect
ODOO_USER = os.getenv("ODOO_USER")            # your Odoo login email
ODOO_API_KEY = os.getenv("ODOO_API_KEY")      # API key from Odoo Settings > Account > API Keys

# ---- Twilio credentials ----
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

# ---- App setup ----
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["https://graph.microsoft.com/.default"]

app = FastAPI(title="Tammy Calendar + Odoo API")
from datetime import datetime
import pytz

def format_datetime(dt_str):
    # Convert ISO to Python datetime
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

    # Convert to Adelaide timezone
    adl_tz = pytz.timezone("Australia/Adelaide")
    dt_adl = dt.astimezone(adl_tz)

    # Format cleanly
    return dt_adl.strftime("%d %b %Y, %I:%M %p")


# ---- Get token ----
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


class SMSRequests(BaseModel):
    phone: str
    start_time: str
    end_time: str


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

    # Outlook booking endpoint
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

    # 1️⃣ Create event in Outlook
    response = requests.post(outlook_url, headers=headers, json=event)

    if response.status_code != 201:
        # Outlook booking failed → stop everything here
        raise HTTPException(status_code=response.status_code, detail={
            "error": "Outlook booking failed",
            "response": response.json()
        })

    # Outlook success
    data = response.json()
    outlook_event_id = data.get("id")

    print(f"✅ Outlook meeting created successfully: {outlook_event_id}")

    # 2️⃣ Then sync to Odoo CRM
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
        print(f"⚠️ Outlook booked, but Odoo sync failed: {e}")
        odoo_event_id = None

    # 3️⃣ Return full summary
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
@app.post("/sms_confirmation")
def send_sms_confirmation(request: SMSRequests):
    user_number = request.phone

    # Format times nicely
    start_fmt = format_datetime(request.start_time)
    end_fmt = format_datetime(request.end_time)

    client = Client(TWILIO_SID, TWILIO_AUTH)

    msg = (
        f"Your meeting with Tracey has been booked for "
        f"{start_fmt} to {end_fmt}. "
        f"If you need to make changes, please call: 0483 905 455"
    )

    client.messages.create(
        to=user_number,
        from_=TWILIO_NUMBER,
        body=msg
    )
    print(msg)
    return {"status": f"SMS sent to {user_number}"}


def create_odoo_event(name, email, phone, start, stop, subject):
    """Create an Odoo calendar event with correctly formatted datetimes."""
    import requests, os
    from datetime import datetime

    ODOO_URL = os.getenv("ODOO_URL")
    ODOO_DB = os.getenv("ODOO_DB")
    ODOO_USER = os.getenv("ODOO_USER")
    ODOO_API_KEY = os.getenv("ODOO_API_KEY")

    # ---- Step 1: Authenticate ----
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
        raise Exception(f"❌ Authentication failed: {auth_res}")

    print(f"✅ Authenticated to Odoo as UID {uid}")

    # ---- Step 2: Clean up the datetime formats ----
    def clean_datetime(dt_str):
        """
        Converts things like '2025-10-16T10:00:00.0000000' or
        '2025-10-16T10:00:00Z' → '2025-10-16 10:00:00'
        """
        try:
            # Strip timezone, milliseconds, and replace T with space
            clean = dt_str.split("T")[0] + " " + dt_str.split("T")[1].split(".")[0]
            return clean.strip()
        except Exception:
            return dt_str.replace("T", " ").split(".")[0]

    start_fmt = clean_datetime(start)
    stop_fmt = clean_datetime(stop)

    print(f"🕒 Converted start={start_fmt}, stop={stop_fmt}")

    # ---- Step 3: Create event in Odoo ----
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
        raise Exception(f"❌ Odoo event creation error: {r['error']}")
    else:
        event_id = r.get("result")
        print(f"✅ Appointment created in Odoo, event ID: {event_id}")
        return event_id


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
