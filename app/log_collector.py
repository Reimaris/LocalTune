import docker
import threading
import logging
from logging.handlers import TimedRotatingFileHandler
import os
import sys

# Ensure logs directory exists
os.makedirs("/app/config/logs", exist_ok=True)

# Set up the rotating file logger for 5 days of retention
handler = TimedRotatingFileHandler(
    "/app/config/logs/localtune.log", 
    when="midnight", 
    interval=1, 
    backupCount=5,
    encoding="utf-8"
)
handler.suffix = "%Y-%m-%d.log"
handler.setFormatter(logging.Formatter("%(asctime)s - [%(container_name)s] - %(message)s"))

logger = logging.getLogger("docker_logs")
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# Adding a stream handler so we can debug the aggregator itself
console = logging.StreamHandler(sys.stdout)
console.setFormatter(logging.Formatter("%(asctime)s - [Aggregator] - %(message)s"))
logger.addHandler(console)

try:
    client = docker.from_env()
    logger.info("Successfully connected to Docker engine.", extra={"container_name": "log_collector"})
except Exception as e:
    logger.error(f"Failed to connect to Docker. Is the socket mounted? {e}", extra={"container_name": "log_collector"})
    sys.exit(1)

# List of containers we care about
TARGET_CONTAINERS = ["localtune_web", "localtune_worker", "localtune_redis"]
active_streams = set()

def stream_logs(container):
    logger.info(f"Attached to logs for {container.name}", extra={"container_name": "log_collector"})
    try:
        # tail=0 ensures we don't duplicate old logs if the collector restarts
        for line in container.logs(stream=True, follow=True, tail=0):
            if line:
                log_line = line.decode("utf-8", errors="replace").strip()
                if log_line:
                    logger.info(log_line, extra={"container_name": container.name})
    except Exception as e:
        logger.error(f"Disconnected from {container.name}: {e}", extra={"container_name": "log_collector"})
    finally:
        active_streams.discard(container.name)

def attach_to_container(c_name):
    if c_name in active_streams:
        return
    try:
        container = client.containers.get(c_name)
        active_streams.add(c_name)
        threading.Thread(target=stream_logs, args=(container,), daemon=True).start()
    except docker.errors.NotFound:
        pass # Container not up yet

# 1. Attach to any already running target containers
for c_name in TARGET_CONTAINERS:
    attach_to_container(c_name)

# 2. Listen to Docker events to attach to containers started after this script
try:
    for event in client.events(decode=True):
        if event.get('Type') == 'container' and event.get('Action') == 'start':
            c_name = event.get('Actor', {}).get('Attributes', {}).get('name')
            if c_name in TARGET_CONTAINERS:
                attach_to_container(c_name)
except KeyboardInterrupt:
    logger.info("Log collector shutting down.", extra={"container_name": "log_collector"})
