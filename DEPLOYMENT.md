# Deployment Guide - Tammy Calendar + Odoo API

## How the Main Code Works

### Overview
The API handles meeting booking for Tracey (ExecConnect) with:
1. **Outlook** - Creates calendar events + Teams meetings
2. **Odoo** - Syncs to CRM
3. **Twilio** - Sends SMS (confirmation + 24h reminder)

---

### Pipeline Flow

```
Retell Agent calls /book
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│  1. CREATE OUTLOOK EVENT                                       │
│     - Microsoft Graph API: POST /users/{email}/events          │
│     - Creates Teams meeting, sends email invite to attendee     │
│     - Stores phone in event body for reminder lookup           │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│  2. SYNC TO ODOO CRM                                           │
│     - Creates calendar.event in Odoo                           │
│     - Continues even if Odoo fails (Outlook already booked)     │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│  3. SEND CONFIRMATION SMS (immediate)                          │
│     - Twilio sends to caller's phone                           │
│     - Only if phone provided in request                        │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
   Return 200 + meeting details


BACKGROUND (runs every hour):
┌───────────────────────────────────────────────────────────────┐
│  4. 24-HOUR REMINDER JOB                                       │
│     - Fetches Outlook events starting in 23h30m-24h30m         │
│     - Parses phone from event body                             │
│     - Sends reminder SMS via Twilio                            │
│     - Tracks sent reminders in reminder_sent.json              │
│     - Meetings < 24h away: no reminder (only confirmation)    │
└───────────────────────────────────────────────────────────────┘
```

---

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/book` | POST | Book meeting (Outlook + Odoo + SMS confirmation) |
| `/availability` | POST | Check available meeting times |
| `/test-reminder` | POST | Manually trigger reminder (testing only) |
| `/test-odoo` | GET | Verify Odoo connection |

---

### Required Environment Variables

```env
# Microsoft / Outlook
CLIENT_ID=
CLIENT_SECRET=
TENANT_ID=
OWNER_EMAIL=

# Odoo
ODOO_URL=
ODOO_DB=
ODOO_USER=
ODOO_API_KEY=

# Twilio
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=

# Optional (for Docker/volume persistence)
REMINDER_SENT_FILE=/app/data/reminder_sent.json
```

---

### Deployment Options

#### Option A: Azure App Service (current setup)
1. Push code to GitHub/Azure DevOps
2. Create/update Azure App Service (Python 3.10+)
3. Configure Application Settings with env vars (no .env in production)
4. Set startup command: `uvicorn main:app --host=0.0.0.0 --port=8000`
5. **Important**: Add persistent storage for `reminder_sent.json` or use Azure Files mount

#### Option B: Docker
```bash
# Build
docker build -t tammy-booking-api .

# Run (pass env via --env-file or -e)
docker run -p 8000:8000 --env-file .env tammy-booking-api

# For persistent reminder tracking, mount a volume:
docker run -p 8000:8000 -v $(pwd)/data:/app tammy-booking-api
# Then set REMINDER_SENT_FILE=/app/reminder_sent.json if needed
```

#### Option C: Local / VM
```bash
pip install -r requirements.txt
uvicorn main:app --host=0.0.0.0 --port=8000
```

---

### Pre-Deployment Checklist

- [ ] All env vars set in production (no .env file with secrets in repo)
- [ ] Retell agent configured to call `/book` with `phone` from caller
- [ ] `reminder_sent.json` persists across restarts (volume or durable storage)
- [ ] Twilio number has sufficient balance
- [ ] Microsoft app has Calendar permissions
- [ ] Odoo API key is valid

---

### Retell Agent Integration

The agent must call `/book` with this payload:
```json
{
  "subject": "Meeting subject",
  "body": "Meeting notes",
  "start_time": "2025-03-15T10:00:00.000000",
  "end_time": "2025-03-15T10:30:00.000000",
  "attendee": "caller@email.com",
  "attendee_name": "Caller Name",
  "phone": "+61418981067"
}
```

**Critical**: `phone` must be included for SMS to be sent.
