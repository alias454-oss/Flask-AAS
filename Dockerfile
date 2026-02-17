FROM python:3.11-slim

# Set working directory
WORKDIR /base

# Set environment variables (before pip install for caching)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=app \
    PYTHONPATH=/base \
    DEBIAN_FRONTEND=noninteractive

# System dependencies for build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libsqlite3-dev \
    sqlite3 \
    libpq-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for better Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . /base
COPY entrypoint.sh /base/entrypoint.sh
RUN chmod +x /base/entrypoint.sh

# Flask default port
EXPOSE 5000

# Run the app
ENTRYPOINT ["./entrypoint.sh"]
