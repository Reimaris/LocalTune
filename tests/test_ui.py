from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_get_dashboard():
    response = client.get("/")
    assert response.status_code == 200
    # Check if the title exists in the rendered HTML
    assert "LocalTune Dashboard" in response.text
    # Check if the form is present
    assert 'hx-post="/download"' in response.text

def test_post_download_valid_youtube():
    response = client.post("/download", data={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    assert response.status_code == 200
    assert "Success!" in response.text
    assert "Job queued for" in response.text

def test_post_download_valid_spotify():
    response = client.post("/download", data={"url": "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"})
    assert response.status_code == 200
    assert "Success!" in response.text
    assert "Job queued for" in response.text

def test_post_download_invalid_url():
    response = client.post("/download", data={"url": "https://soundcloud.com/some/track"})
    assert response.status_code == 200
    assert "Error!" in response.text
    assert "Invalid URL" in response.text
