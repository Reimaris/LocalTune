import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db.database import Base, get_db
from app.db import models

SQLALCHEMY_DATABASE_URL = "sqlite://"

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


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "synced_playlists_count" in data


def test_add_and_list_synced_playlists():
    response = client.post(
        "/api/synced-playlists",
        data={
            "url": "https://soundcloud.com/artist/sets/test-playlist",
            "sync_mode": "append_only",
            "title": "My SoundCloud Set",
        },
    )
    assert response.status_code == 200
    assert "My SoundCloud Set" in response.text
    assert "Append-Only" in response.text


def test_toggle_synced_playlist():
    client.post(
        "/api/synced-playlists",
        data={
            "url": "https://soundcloud.com/artist/sets/test-playlist-2",
            "sync_mode": "mirror",
            "title": "Mirror Test",
        },
    )

    db = TestingSessionLocal()
    sp = db.query(models.SyncedPlaylist).filter_by(title="Mirror Test").first()
    assert sp is not None
    sp_id = sp.id
    db.close()

    toggle_resp = client.post(f"/api/synced-playlists/{sp_id}/toggle")
    assert toggle_resp.status_code == 200
    assert "Paused" in toggle_resp.text


def test_update_synced_playlist():
    client.post(
        "/api/synced-playlists",
        data={
            "url": "https://soundcloud.com/artist/sets/test-playlist-3",
            "sync_mode": "append_only",
            "title": "Old Title",
        },
    )

    db = TestingSessionLocal()
    sp = db.query(models.SyncedPlaylist).filter_by(title="Old Title").first()
    assert sp is not None
    sp_id = sp.id
    db.close()

    update_resp = client.put(
        f"/api/synced-playlists/{sp_id}",
        data={"title": "New Updated Title", "sync_mode": "mirror"},
    )
    assert update_resp.status_code == 200
    assert "New Updated Title" in update_resp.text


def test_delete_synced_playlist():
    client.post(
        "/api/synced-playlists",
        data={
            "url": "https://soundcloud.com/artist/sets/test-playlist-4",
            "sync_mode": "append_only",
            "title": "To Be Deleted",
        },
    )

    db = TestingSessionLocal()
    sp = db.query(models.SyncedPlaylist).filter_by(title="To Be Deleted").first()
    assert sp is not None
    sp_id = sp.id
    db.close()

    del_resp = client.request(
        "DELETE",
        f"/api/synced-playlists/{sp_id}",
        data={"delete_files": "false"},
    )
    assert del_resp.status_code == 200
    assert "To Be Deleted" not in del_resp.text


def test_check_exists_redownloads_if_file_deleted(tmp_path):
    from app.core.downloader import check_exists, insert_download

    db = TestingSessionLocal()
    dummy_file = tmp_path / "test_song.opus"
    dummy_file.write_text("audio content")

    # Insert completed download with existing file
    insert_download(
        db=db,
        track_id="ytdlp_12345",
        title="Test Song",
        artist="Test Artist",
        file_path=str(dummy_file),
        status="Completed",
        job_id="job1",
    )

    # When file exists on disk, check_exists returns True
    assert check_exists(db, "ytdlp_12345") is True

    # Manually delete the file from disk
    dummy_file.unlink()

    # Now check_exists returns False and purges stale DB record for re-download
    assert check_exists(db, "ytdlp_12345") is False
    stale_dl = db.query(models.Download).filter_by(track_id="ytdlp_12345").first()
    assert stale_dl is None
    db.close()


def test_delete_synced_playlist_removes_folder(tmp_path, monkeypatch):
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "DOWNLOAD_DIR", tmp_path)

    playlist_folder = tmp_path / "My Test Playlist"
    playlist_folder.mkdir()
    song_file = playlist_folder / "song.opus"
    song_file.write_text("audio data")

    client.post(
        "/api/synced-playlists",
        data={
            "url": "https://soundcloud.com/artist/sets/test-playlist-folder-del",
            "sync_mode": "append_only",
            "title": "My Test Playlist",
        },
    )

    db = TestingSessionLocal()
    sp = db.query(models.SyncedPlaylist).filter_by(title="My Test Playlist").first()
    assert sp is not None
    sp_id = sp.id

    from app.core.downloader import insert_download
    insert_download(
        db=db,
        track_id="ytdlp_9999",
        title="song",
        artist="artist",
        file_path=str(song_file),
        status="Completed",
        synced_playlist_id=sp_id,
    )
    db.close()

    del_resp = client.request(
        "DELETE",
        f"/api/synced-playlists/{sp_id}?delete_files=true",
    )
    assert del_resp.status_code == 200
    assert not playlist_folder.exists()
