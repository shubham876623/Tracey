"""
Test script for SMS confirmation and 24-hour reminder.
Run the server first: uvicorn main:app --reload --port 8000
Then run: python test_sms.py
"""
import os
import requests
from datetime import datetime, timedelta
import pytz

BASE_URL = os.getenv("API_URL", "http://localhost:8000")
# Test number for confirmation + reminder SMS. In production, phone comes from Retell agent.
TEST_PHONE = os.getenv("TEST_PHONE", "+61418981067")
TEST_EMAIL = os.getenv("TEST_EMAIL", "test@example.com")  # Use real email to avoid Outlook invite delivery failure


def test_book_and_confirmation():
    """Test 1: Book meeting - sends confirmation SMS immediately after booking."""
    print("\n" + "=" * 50)
    print("TEST 1: Book Meeting (confirmation SMS after booking)")
    print("=" * 50)

    adl = pytz.timezone("Australia/Adelaide")
    start = datetime.now(adl) + timedelta(minutes=10)
    end = start + timedelta(minutes=30)
    start_str = start.strftime("%Y-%m-%dT%H:%M:%S")
    end_str = end.strftime("%Y-%m-%dT%H:%M:%S")

    payload = {
        "subject": "SMS Test - Confirmation",
        "body": "Test booking for SMS confirmation",
        "start_time": start_str,
        "end_time": end_str,
        "attendee": TEST_EMAIL,
        "attendee_name": "Test User",
        "phone": TEST_PHONE,
    }

    r = requests.post(f"{BASE_URL}/book", json=payload)
    print(f"Status: {r.status_code}")
    try:
        print(f"Response: {r.json()}")
    except Exception:
        print(f"Response (raw): {r.text[:500]}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    print("[OK] Meeting booked! Confirmation SMS should arrive immediately. Check your phone.")


def test_reminder():
    """Test 2: Trigger reminder job for meetings in 5-15 min window."""
    print("\n" + "=" * 50)
    print("TEST 2: 24-Hour Reminder (test-reminder with 5-15 min window)")
    print("=" * 50)
    print("Sends reminders for meetings starting in 5-15 min from now.")
    print("(Uses same logic as production 24h reminder, just shorter window)")

    r = requests.post(
        f"{BASE_URL}/test-reminder",
        params={"hours_start": 0.08, "hours_end": 0.25},  # ~5-15 min
    )
    print(f"Status: {r.status_code}")
    try:
        print(f"Response: {r.json()}")
    except Exception:
        print(f"Response (raw): {r.text[:500]}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    print("[OK] Reminder job ran! If a meeting was in the 5-15 min window, reminder SMS was sent.")


if __name__ == "__main__":
    print("\nSMS Testing - 1) Confirmation after book, 2) Reminder only")
    print(f"Base URL: {BASE_URL}")
    print(f"Test phone: {TEST_PHONE} (in production: from Retell agent)")

    try:
        test_book_and_confirmation()
    except Exception as e:
        print(f"[FAIL] Test 1 failed: {e}")

    try:
        test_reminder()
    except Exception as e:
        print(f"[FAIL] Test 2 failed: {e}")

    print("\n" + "=" * 50)
    print("All tests completed. Check your phone for SMS.")
    print("=" * 50)
