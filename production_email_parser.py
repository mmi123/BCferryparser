import imaplib
import email
import os
import json
import re
import time
from bs4 import BeautifulSoup
from datetime import datetime

# ------------------------------------------------------------
#  CONFIGURATION
# ------------------------------------------------------------

IMAP_SERVER = os.getenv("IMAP_SERVER")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
TARGET_SENDER = os.getenv("TARGET_SENDER", "no_reply@bcferries.com")

OUTPUT_JSON_DIR = "bookings"
OUTPUT_ICAL_FILE = "calendar/ferries.ics"

IMAP_MAILBOX = os.getenv("IMAP_MAILBOX", "INBOX")
IMAP_PROCESSED_FOLDER = os.getenv("IMAP_PROCESSED_FOLDER", "Processed/BCFerries")

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "600"))


# ------------------------------------------------------------
#  LOGGING
# ------------------------------------------------------------

def log(message: str) -> None:
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {message}", flush=True)


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
    parts = text.split("BOOKING CONFIRMATION")
    return [p.strip() for p in parts if "Booking reference" in p]


patterns = {
    "reference": r"Booking reference:\s*([A-Z0-9]+)",

    # Terminal extraction (line after DEPARTS/ARRIVES)
    "depart_terminal": r"DEPARTS[\s\S]*?\n([A-Za-z0-9\s\(\)-]+?)\s+\d{1,2}:\d{2}",
    "arrive_terminal": r"ARRIVES[\s\S]*?\n([A-Za-z0-9\s\(\)-]+?)\s+\d{1,2}:\d{2}",

    # Times and dates
    "depart_time": r"DEPARTS[\s\S]*?(\d{1,2}:\d{2}\s*[AP]M)",
    "depart_date": r"DEPARTS[\s\S]*?(\d{2}/[A-Za-z]{3}/\d{4})",
    "arrive_time": r"ARRIVES[\s\S]*?(\d{1,2}:\d{2}\s*[AP]M)",
    "arrive_date": r"ARRIVES[\s\S]*?(\d{2}/[A-Za-z]{3}/\d{4})",

    # Other fields
    "fare_type": r"Fare type:\s*([A-Za-z ]+)",
    "ferry": r"Ferry:\s*([A-Za-z0-9 ]+)",
    "total": r"Total\s*\$([0-9]+\.[0-9]{2})",
    "paid": r"Amount paid\s*\$([0-9]+\.[0-9]{2})",
}


def parse_fares(block):
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
    data = {}

    for key, pattern in patterns.items():
        m = re.search(pattern, block)
        if m:
            data[key] = m.group(1).strip()

    data["fares"] = parse_fares(block)

    return data


def parse_email_body(text):
    bookings = []
    blocks = split_bookings(text)

    for block in blocks:
        bookings.append(parse_booking(block))

    return bookings


# ------------------------------------------------------------
#  JSON OUTPUT
# ------------------------------------------------------------

def write_booking_json(booking):
    os.makedirs(OUTPUT_JSON_DIR, exist_ok=True)

    ref = booking.get("reference", "UNKNOWN")
    filename = os.path.join(OUTPUT_JSON_DIR, f"{ref}.json")

    with open(filename, "w") as f:
        json.dump(booking, f, indent=2)

    log(f"Saved booking → {filename}")


# ------------------------------------------------------------
#  ICAL GENERATION
# ------------------------------------------------------------

def generate_ical(bookings, output_file=OUTPUT_ICAL_FILE):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//BC Ferries Parser//EN",
        "CALSCALE:GREGORIAN"
    ]

    for b in bookings:

        required = [
            "depart_date", "depart_time",
            "arrive_date", "arrive_time",
            "depart_terminal", "arrive_terminal"
        ]

        if any(k not in b or not b[k] for k in required):
            log(f"Skipping incomplete booking in ICS: {b.get('reference', 'UNKNOWN')}")
            continue

        def to_ical_dt(date_str, time_str):
            dt = datetime.strptime(f"{date_str} {time_str}", "%d/%b/%Y %I:%M %p")
            return dt.strftime("%Y%m%dT%H%M%S")

        dtstart = to_ical_dt(b["depart_date"], b["depart_time"])
        dtend = to_ical_dt(b["arrive_date"], b["arrive_time"])

        summary = f"BCF {b['reference']}"
        location = b["depart_terminal"]

        # Under-height vehicle
        under_height = None
        passenger_count = 0

        for fare in b["fares"]:
            desc = fare["description"].lower()
            if "under height" in desc:
                under_height = fare["description"]
            if "year" in desc or "passenger" in desc:
                passenger_count += fare["quantity"]

        description_lines = [
            f"Booking reference: {b['reference']}",
            f"Booking date: {b.get('depart_date', 'Unknown')}",
            f"Ferry: {b.get('ferry', 'Unknown')}",
            f"Fare type: {b.get('fare_type', 'Unknown')}",
            f"Passengers: {passenger_count}",
            f"Amount paid: ${b.get('paid', '0.00')}",
        ]

        if under_height:
            description_lines.append(f"Vehicle: {under_height}")

        description = "\\n".join(description_lines)

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{b['reference']}@bcferries",
            f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{dtstart}",
            f"DTEND:{dtend}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{description}",
            f"LOCATION:{location}",
            "END:VEVENT"
        ])

    lines.append("END:VCALENDAR")

    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    with open(output_file, "w") as f:
        f.write("\n".join(lines))

    log(f"Generated iCal file → {output_file}")


# ------------------------------------------------------------
#  IMAP HELPERS
# ------------------------------------------------------------

def ensure_processed_folder(mail):
    typ, data = mail.list()
    if typ != "OK":
        log("Failed to list mailboxes; skipping processed folder check.")
        return

    existing = [line.decode(errors="ignore") for line in data if line]
    if any(IMAP_PROCESSED_FOLDER in line for line in existing):
        return

    typ, _ = mail.create(IMAP_PROCESSED_FOLDER)
    if typ == "OK":
        log(f"Created processed folder: {IMAP_PROCESSED_FOLDER}")
    else:
        log(f"Could not create processed folder: {IMAP_PROCESSED_FOLDER}")


def move_message_to_processed(mail, msg_id):
    typ, _ = mail.copy(msg_id, IMAP_PROCESSED_FOLDER)
    if typ != "OK":
        log(f"Failed to copy message {msg_id.decode()} to {IMAP_PROCESSED_FOLDER}")
        return

    mail.store(msg_id, "+FLAGS", r"(\Deleted)")
    log(f"Moved message {msg_id.decode()} to {IMAP_PROCESSED_FOLDER}")


# ------------------------------------------------------------
#  MAIN EXECUTION LOOP (SCHEDULER)
# ------------------------------------------------------------

def run_once():
    log("Starting BC Ferries email parser run")

    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_USER, EMAIL_PASS)
    log(f"Logged in as {EMAIL_USER}")

    mail.select(IMAP_MAILBOX)
    ensure_processed_folder(mail)

    status, data = mail.search(None, f'FROM "{TARGET_SENDER}"')
    if status != "OK":
        log("No messages found from target sender.")
        mail.logout()
        return

    msg_ids = data[0].split()
    log(f"Found {len(msg_ids)} messages from {TARGET_SENDER}")

    all_bookings = []
    seen_refs = set()

    for msg_id in msg_ids:
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            log(f"Failed to fetch message {msg_id.decode()}")
            continue

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        subject = msg.get("Subject", "").strip()
        log(f"Processing message {msg_id.decode()} with subject: {subject}")

        if "reminder" in subject.lower():
            log(f"Skipping reminder email: {subject}")
            move_message_to_processed(mail, msg_id)
            continue

        body = extract_body(msg)
        bookings = parse_email_body(body)

        for booking in bookings:
            ref = booking.get("reference")
            if not ref:
                log("Skipping booking with no reference")
                continue

            if ref in seen_refs:
                log(f"Skipping duplicate booking reference: {ref}")
                continue

            seen_refs.add(ref)
            write_booking_json(booking)
            all_bookings.append(booking)

        move_message_to_processed(mail, msg_id)

    generate_ical(all_bookings)

    mail.expunge()
    mail.logout()
    log("Finished BC Ferries email parser run")


def main():
    while True:
        run_once()
        log(f"Sleeping for {POLL_INTERVAL_SECONDS} seconds")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
