"""
Send a test SMS to verify Twilio works.
Usage: python send_test_sms.py
Or: python send_test_sms.py +61418981067
"""
import os
import sys
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

# Default: client's number
PHONE = sys.argv[1] if len(sys.argv) > 1 else "+61418981067"
PHONE = PHONE.replace(" ", "")

msg = "Test: Your meeting with Tracey has been booked. If you need to make changes, please call: 0483 905 455"

print(f"Sending test SMS to {PHONE}...")
client = Client(TWILIO_SID, TWILIO_AUTH)
m = client.messages.create(to=PHONE, from_=TWILIO_NUMBER, body=msg)
print(f"Done. SID: {m.sid}")
