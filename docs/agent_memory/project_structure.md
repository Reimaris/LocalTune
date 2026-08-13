# Project Structure — LocalTune

An overview of the codebase layout and module dependencies.

```
LocalTune/
├── .github/              # GitHub Actions workflows and configuration
├── app/                  # Main FastAPI application directory
├── config/               # Configuration files and environment setups
├── docs/
│   └── agent_memory/     # Agent contextual memory and active state records
├── tests/                # Unit and integration tests
├── venv/                 # Python virtual environment (if used locally)
├── windows_manager/      # Source and builds for the Windows GUI manager
├── .gitignore            # Git ignore rules
├── docker-compose.yml    # Docker Compose definition for running the app
├── Dockerfile            # Dockerfile for the main application
├── README.md             # Project documentation and setup guide
└── requirements.txt      # Python dependencies
```

## Key Components

- **`app/`**: Contains the core logic for the API, database models, background task runners, and HTMX views.
- **`windows_manager/`**: A lightweight executable manager for Windows to control Docker containers and provide simple access to the UI.
- **`docker-compose.yml`**: Defines the web app, background worker, and Redis queue.
