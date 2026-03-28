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

OUTPUT_DIR = "bookings"


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
#  JSON OUTPUT
# ------------------------------------------------------------

def write_booking_json(booking):
    """Write a single booking to a JSON file."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    ref = booking.get("reference", "UNKNOWN")
    filename = os.path.join(OUTPUT_DIR, f"{ref}.json")

    with open(filename, "w") as f:
        json.dump(booking, f, indent=2)

    print(f"Saved booking → {filename}")

# ------------------------------------------------------------
#  ics generate
# ------------------------------------------------------------


def generate_ical(bookings, output_file="ferries.ics"):
    """Generate a single iCal file containing all ferry bookings."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//BC Ferries Parser//EN",
        "CALSCALE:GREGORIAN"
    ]

    for b in bookings:
        # Convert date formats
        # Example input: "05/Apr/2026" + "10:00 PM"
        def to_ical_dt(date_str, time_str):
            dt = datetime.strptime(f"{date_str} {time_str}", "%d/%b/%Y %I:%M %p")
            return dt.strftime("%Y%m%dT%H%M%S")

        dtstart = to_ical_dt(b["depart_date"], b["depart_time"])
        dtend = to_ical_dt(b["arrive_date"], b["arrive_time"])

        summary = f"BC Ferries – {b['depart_terminal']} → {b['arrive_terminal']}"
        description = (
            f"Booking reference: {b['reference']}\\n"
            f"Ferry: {b['ferry']}\\n"
            f"Fare type: {b['fare_type']}\\n"
            f"Amount paid: ${b['paid']}"
        )

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{b['reference']}@bcferries",
            f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{dtstart}",
            f"DTEND:{dtend}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{description}",
            f"LOCATION:{b['depart_terminal']}",
            "END:VEVENT"
        ])

    lines.append("END:VCALENDAR")

    with open(output_file, "w") as f:
        f.write("\n".join(lines))

    print(f"Generated iCal file → {output_file}")



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
    
        for booking in bookings:
            write_booking_json(booking)
            all_bookings.append(booking)
    
    # Generate iCal subscription file
    generate_ical(all_bookings, output_file="ferries.ics")


if __name__ == "__main__":
    main()
