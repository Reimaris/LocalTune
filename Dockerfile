FROM python:3.12-slim

# Install system dependencies required for building some python packages and for audio processing
RUN apt-get update && apt-get install -y \
    ffmpeg \
    zip \
    gcc \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN useradd -m -U appuser

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_ROOT_USER_ACTION=ignore
RUN pip install --no-cache-dir -q -r requirements.txt

# Copy the rest of the application
COPY . .

# Set Python path to allow running modules from /app
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Create necessary directories and ensure permissions for the non-root user
RUN mkdir -p /app/config/logs /downloads \
    && chown -R appuser:appuser /app /downloads

USER appuser

# Run the web server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
