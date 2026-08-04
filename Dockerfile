FROM python:3.11-slim

# Install system dependencies required for building some python packages and for audio processing
RUN apt-get update && apt-get install -y \
    ffmpeg \
    zip \
    gcc \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Set Python path to allow running modules from /app
ENV PYTHONPATH=/app

# Create necessary directories
RUN mkdir -p /app/config/logs /downloads

# By default, we will run the web server. 
# The worker container will override the command in docker-compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
