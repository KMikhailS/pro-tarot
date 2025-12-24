FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .
COPY handlers/ handlers/
COPY database/ database/
COPY messages/ messages/
COPY scheduler/ scheduler/
COPY utils/ utils/
COPY cards/ cards/
COPY images/ images/
COPY video/ video/

# Create directory for data persistence
RUN mkdir -p /app/data /app/logs

# Run the bot
CMD ["python3", "main.py"]
