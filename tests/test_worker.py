import pytest
from unittest.mock import patch, MagicMock
import json
from app.worker import process_download
from app.db.database import Base, engine

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@patch("app.core.downloader.subprocess.run")
@patch("app.core.notifications.httpx.post")
@patch("app.core.downloader.uuid")
@patch("app.core.downloader.fix_permissions")
def test_process_youtube_download(mock_fix, mock_uuid, mock_post, mock_run):
    mock_uuid.uuid4.return_value = MagicMock(hex="dummy_uuid")
    
    # Mock yt-dlp -J --flat-playlist output
    mock_result = MagicMock()
    mock_result.stdout = json.dumps({"id": "yt_123", "title": "Test YT Video", "uploader": "Test Channel"}) + "\n"
    mock_run.return_value = mock_result
    
    # Execute worker logic
    process_download("https://www.youtube.com/watch?v=yt_123")
    
    # Verify subprocess called correctly for JSON dump
    mock_run.assert_any_call(["yt-dlp", "-J", "--flat-playlist", "https://www.youtube.com/watch?v=yt_123"], check=True, capture_output=True, text=True)
    
    # Verify Telegram notification
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert "Test YT Video" in kwargs['json']['text']
    mock_fix.assert_called_once()

@patch("app.core.downloader.subprocess.run")
@patch("app.core.notifications.httpx.post")
@patch("app.core.downloader.uuid")
@patch("app.core.downloader.fix_permissions")
@patch("builtins.open", new_callable=MagicMock)
def test_process_spotify_download(mock_open, mock_fix, mock_uuid, mock_post, mock_run):
    mock_uuid.uuid4.return_value = MagicMock(hex="dummy_uuid")
    
    # Mock spotdl save json output by faking the file read
    mock_file = mock_open.return_value.__enter__.return_value
    mock_file.read.return_value = json.dumps([{"song_id": "sp_123", "name": "Test SP Track", "artist": "Test Artist"}])
    
    process_download("https://open.spotify.com/track/sp_123")
    
    # Verify spotdl was called
    mock_run.assert_any_call(["spotdl", "save", "https://open.spotify.com/track/sp_123", "--save-file", "temp_dummy_uuid.spotdl"], check=True, capture_output=True)
    
    # Verify Telegram notification
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert "Test SP Track" in kwargs['json']['text']
    mock_fix.assert_called_once()
