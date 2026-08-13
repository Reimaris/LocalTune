from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime, timezone
from app.db.database import Base

class Download(Base):
    __tablename__ = "downloads"

    id = Column(Integer, primary_key=True, index=True)
    track_id = Column(String, unique=True, index=True, nullable=False)
    title = Column(String, nullable=False)
    artist = Column(String, nullable=False)
    file_path = Column(String, nullable=True)
    status = Column(String, default="Completed")
    job_id = Column(String, index=True, nullable=True)
    job_title = Column(String, nullable=True)
    downloaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    telegram_bot_token = Column(String, nullable=True)
    telegram_chat_id = Column(String, nullable=True)
    spotify_client_id = Column(String, nullable=True)
    spotify_client_secret = Column(String, nullable=True)
