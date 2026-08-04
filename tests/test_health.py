from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db.database import Base, get_db
from app.db.models import Download

# Setup in-memory SQLite for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base.metadata.create_all(bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

def test_health_check_empty_db():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["logger"] == "configured"
    assert data["database"] == "connected"
    assert data["downloads_count"] == 0

def test_health_check_with_data():
    # Insert a dummy record
    db = TestingSessionLocal()
    dummy_download = Download(
        track_id="dummy_track_1",
        title="Test Song",
        artist="Test Artist",
        file_path="/downloads/test.mp3"
    )
    db.add(dummy_download)
    db.commit()
    db.close()

    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["downloads_count"] == 1
