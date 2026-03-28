FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    libxml2 \
    libxslt1.1 \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY production_email_parser.py .

RUN pip install --no-cache-dir beautifulsoup4 lxml

ENV POLL_INTERVAL_SECONDS=600

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD pgrep -f production_email_parser.py || exit 1

CMD ["python", "production_email_parser.py"]
