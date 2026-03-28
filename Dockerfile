FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libxml2 \
    libxslt1.1 \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy script
COPY production_email_parser.py .

# Install Python dependencies
RUN pip install --no-cache-dir \
    beautifulsoup4 \
    lxml

# Default command
CMD ["python", "production_email_parser.py"]
