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
│   ├── db/                    # Database models and setup
│   │   ├── database.py        # SQLAlchemy engine and session
│   │   └── models.py          # SQLAlchemy ORM models
│   ├── static/                # Static assets
│   │   └── styles.css         # Custom CSS overrides
│   └── templates/             # Jinja2 HTML templates
│       ├── base.html          # Shell layout
│       └── dashboard.html     # Main views
├── tests/                     # Unit and integration tests
│   ├── test_health.py         # Tests for health endpoint and DB
│   └── test_ui.py             # Tests for UI routes and HTMX
├── docker-compose.yml         # Production deployment
├── docker-compose.test.yml    # Testing deployment
├── Dockerfile                 # Unified container definition
└── requirements.txt           # Python dependencies
```
