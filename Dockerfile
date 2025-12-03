# SNP Printer Service
# Minimal Docker image for network thermal printer support
# Designed for Unraid deployment

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies for python-escpos
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcups2-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for Docker layer caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .

# Environment defaults (override in docker-compose or Unraid)
ENV PRINTER_TYPE=network
ENV NETWORK_HOST=192.168.1.100
ENV NETWORK_PORT=9100
ENV HOST=0.0.0.0
ENV PORT=5000
ENV DEBUG=false

# Expose the API port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

# Run the application
CMD ["python", "app.py"]
