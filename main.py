

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import msal
import requests
import os
from dotenv import load_dotenv
from twilio.rest import Client
# ---- Load .env file ----
load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TENANT_ID = os.getenv("TENANT_ID")
OWNER_EMAIL = os.getenv("OWNER_EMAIL")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["https://graph.microsoft.com/.default"]

# ---- FastAPI app ----
app = FastAPI(title="Tammy Outlook Calendar API")

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
    start_time: str  # "2025-09-06T09:00:00"
    end_time: str    # "2025-09-06T18:00:00"
    duration: str    # "PT1H" (ISO 8601 duration)

class BookMeetingRequest(BaseModel):
    subject: str
    body: str
    start_time: str  # "2025-09-06T10:00:00"
    end_time: str    # "2025-09-06T11:00:00"
    attendee: str    # guest email
    attendee_name: str = "Guest"
    phone: str = "" 
    location: str = "Microsoft Teams Meeting"
class SMSRequests(BaseModel):
    phone : str
    start_time: str  # "2025-09-06T10:00:00"
    end_time: str    # "2025-09-06T11:00:00"
    
# ---- Endpoints ----
@app.post("/availability")
def check_availability(request: AvailabilityRequest):
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}

    url = f"https://graph.microsoft.com/v1.0/users/{OWNER_EMAIL}/findMeetingTimes"
   # Instead of request.start_time directly (which may include +09:30)
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

    print("payload",payload)

    response = requests.post(url, headers=headers, json=payload)
    print(response.json())
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.json())
    return response.json()

@app.post("/book")
def book_meeting(request: BookMeetingRequest):
    token = get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 👇 add ?sendInvitations=true so Outlook emails the attendee
    url = f"https://graph.microsoft.com/v1.0/users/{OWNER_EMAIL}/events?sendInvitations=true"

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
            )
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

    response = requests.post(url, headers=headers, json=event)
    if response.status_code != 201:
        raise HTTPException(status_code=response.status_code, detail=response.json())

    data = response.json()
    return {
        "status": "Meeting booked",
        "subject": data.get("subject"),
        "start": data.get("start"),
        "end": data.get("end"),
        "attendee": request.attendee,
        "phone": request.phone,
    }
    
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
@app.post("/sms_confirmation")
def send_sms_confirmation(request: SMSRequests):
    """Send an SMS confirmation via Twilio."""
    user_number = request.phone
    start_time = request.start_time
    end_time = request.end_time
    client = Client(TWILIO_SID, TWILIO_AUTH)
    msg = f"your meeting with Tracey has been scheduled for {start_time} to Jul 12, 2025 at {end_time}. If you need to make any changes please call: 0483 905 455"
    # client.messages.create(to=user_number, from_=TWILIO_NUMBER, body=msg)
    # reply = f"✅ SMS sent to {user_number}"
    return {
         "status":"reply"
    }
