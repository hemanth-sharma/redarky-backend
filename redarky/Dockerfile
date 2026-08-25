# ==========================================
# STAGE 1: Build Go Scraper Binary
# ==========================================
FROM golang:1.25-alpine AS go-builder
WORKDIR /build

# Copy Go mod files from redarky/scraper/
COPY redarky/scraper/go.mod redarky/scraper/go.sum ./
RUN go mod download

# Copy all Go source files from redarky/scraper/
COPY redarky/scraper/ ./
RUN CGO_ENABLED=0 GOOS=linux go build -o /build/go_scraper cmd/main.go

# ==========================================
# STAGE 2: Python Runtime + Multi-Process Setup
# ==========================================
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies, Redis server, and Supervisor
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq-dev \
    gcc \
    redis-server \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements from repository root
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy compiled Go scraper binary from build stage
COPY --from=go-builder /build/go_scraper /app/go_scraper

# Copy Python application code
COPY redarky/api /app/redarky/api
COPY redarky/scraper /app/redarky/scraper

# Environment configuration
ENV PYTHONPATH=/app/redarky/api:/app
ENV CELERY_BROKER_URL=redis://127.0.0.1:6379/0
ENV GO_SCRAPER_URL=http://127.0.0.1:8081/scrape

# Copy supervisor config and launcher script
COPY redarky/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY redarky/start.sh /app/start.sh
RUN chmod +x /app/start.sh /app/go_scraper

EXPOSE 8000

CMD ["/app/start.sh"]