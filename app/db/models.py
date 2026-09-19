from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String

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
    synced_playlist_id = Column(
        Integer, ForeignKey("synced_playlists.id"), nullable=True
    )
    source_url = Column(String, nullable=True)
    downloaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SyncedPlaylist(Base):
    __tablename__ = "synced_playlists"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, unique=True, index=True, nullable=False)
    title = Column(String, nullable=False)
    sync_mode = Column(String, default="append_only")  # "append_only" or "mirror"
    is_active = Column(Boolean, default=True)
    status = Column(String, default="Active")  # "Active", "Syncing", "Paused", "Failed"
    last_error = Column(String, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    telegram_bot_token = Column(String, nullable=True)
    telegram_chat_id = Column(String, nullable=True)
    spotify_client_id = Column(String, nullable=True)
    spotify_client_secret = Column(String, nullable=True)
    enable_browser_downloads = Column(Boolean, default=False, nullable=False)
    naming_template = Column(
        String,
        default="{playlist}/{artist} - {title}.{ext}",
        nullable=False,
    )
    default_audio_format = Column(String, default="opus", nullable=False)
    default_audio_bitrate = Column(String, default="best", nullable=False)
    sync_interval_hours = Column(Integer, default=6, nullable=False)
    sync_on_startup = Column(Boolean, default=True, nullable=False)
