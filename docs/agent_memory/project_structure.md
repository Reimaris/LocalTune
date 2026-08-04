# Project Structure

Map of the LocalTune codebase and module dependencies.

```text
LocalTune/
├── AGENT_INSTRUCTIONS.md      # Master agent anchor instructions
├── usage.md                   # How to install, configure, and run
├── docs/
│   └── agent_memory/          # Persistent context files
├── config/                    # Production persistent data
│   ├── logs/                  # Application logs
│   └── localtune.db           # SQLite database
├── test_config/               # Testing persistent data
├── downloads/                 # Downloaded audio files
├── app/                       # Core Python application
│   ├── main.py                # FastAPI entrypoint
│   ├── worker.py              # RQ worker entrypoint
│   ├── core/                  # Configurations and singletons
│   │   ├── config.py          # Environment settings
│   │   └── logging_config.py  # Logger setup
│   └── templates/             # Jinja2 HTML templates
│       ├── base.html          # Shell layout
│       └── dashboard.html     # Main views
├── docker-compose.yml         # Production deployment
├── docker-compose.test.yml    # Testing deployment
├── Dockerfile                 # Unified container definition
└── requirements.txt           # Python dependencies
```
