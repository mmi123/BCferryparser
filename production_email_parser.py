import imaplib
import email
import os
import json
import re
from bs4 import BeautifulSoup
from datetime import datetime

# ------------------------------------------------------------
#  IMAP CONNECTION
# ------------------------------------------------------------

IMAP_SERVER = os.getenv("IMAP_SERVER")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
TARGET_SENDER = os.getenv("TARGET_SENDER", "no_reply@bcferries.com")


# ------------------------------------------------------------
#  EMAIL BODY EXTRACTION
# ------------------------------------------------------------

def extract_body(msg):
    """Extract plain text or HTML body from an email."""
    for part in msg.walk():
        content_type = part.get_content_type()
        payload = part.get_payload(decode=True)

        if payload is None:
            continue

        if content_type == "text/plain":
            return payload.decode(errors="ignore")

        if content_type == "text/html":
            html = payload.decode(errors="ignore")
            soup = BeautifulSoup(html, "html.parser")
            return soup.get_text(separator="\n")

    return ""


# ------------------------------------------------------------
#  BOOKING PARSING LOGIC
# ------------------------------------------------------------

def split_bookings(text):
    """Split the email into individual booking blocks."""
    parts = text.split("BOOKING CONFIRMATION")
    return [p.strip() for p in parts if "Booking reference" in p]


patterns = {
    "reference": r"Booking reference:\s*([A-Z0-9]+)",
    "depart_terminal": r"DEPARTS\s+([\w\s\(\)]+)\s+\d",
    "depart_time": r"DEPARTS[\s\S]*?(\d{1,2}:\d{2}\s*[AP]M)",
    "depart_date": r"DEPARTS[\s\S]*?(\d{2}/[A-Za-z]{3}/\d{4})",
    "arrive_terminal": r"ARRIVES\s+([\w\s\(\)]+)\s+\d",
    "arrive_time": r"ARRIVES[\s\S]*?(\d{1,2}:\d{2}\s*[AP]M)",
    "arrive_date": r"ARRIVES[\s\S]*?(\d{2}/[A-Za-z]{3}/\d{4})",
    "fare_type": r"Fare type:\s*([A-Za-z ]+)",
    "ferry": r"Ferry:\s*([A-Za-z0-9 ]+)",
    "total": r"Total\s*\$([0-9]+\.[0-9]{2})",
    "paid": r"Amount paid\s*\$([0-9]+\.[0-9]{2})",
}


def parse_fares(block):
    """Extract fare line items."""
    fares = []
    fare_lines = re.findall(r"(\d+x.*?)\$(\d+\.\d{2})", block)

    for line, amount in fare_lines:
        qty = int(line.split("x")[0])
        desc = line.split("x")[1].strip()
        fares.append({
            "quantity": qty,
            "description": desc,
            "amount": float(amount)
        })

    return fares


def parse_booking(block):
    """Parse a single booking block into structured data."""
    data = {}

    for key, pattern in patterns.items():
        m = re.search(pattern, block)
        if m:
            data[key] = m.group(1).strip()

    data["fares"] = parse_fares(block)

    return data


def parse_email_body(text):
    """Parse all bookings from the email body."""
    bookings = []
    blocks = split_bookings(text)

    for block in blocks:
        bookings.append(parse_booking(block))

    return bookings


# ------------------------------------------------------------
#  MAIN EXECUTION
# ------------------------------------------------------------

def main():
    print("Connecting to IMAP server...")

    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("INBOX")

    status, data = mail.search(None, f'FROM "{TARGET_SENDER}"')
    if status != "OK":
        print("No messages found from target sender.")
        return

    all_bookings = []

    for msg_id in data[0].split():
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        body = extract_body(msg)
        bookings = parse_email_body(body)
        all_bookings.extend(bookings)

    # Output JSON
    output = {
        "generated_at": datetime.utcnow().isoformat(),
        "total_bookings": len(all_bookings),
        "bookings": all_bookings
    }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
