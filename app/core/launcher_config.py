import json
import os
import sys
from typing import Any


def get_project_dir() -> str:
    """Returns the base project directory."""
    core_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.dirname(core_dir)
    return os.path.dirname(app_dir)


def get_launcher_config_path(project_dir: str | None = None) -> str:
    """Returns absolute path to config/launcher.json."""
    if project_dir is None:
        project_dir = get_project_dir()
    return os.path.abspath(os.path.join(project_dir, "config", "launcher.json"))


def load_launcher_config(project_dir: str | None = None) -> dict[str, Any]:
    """Loads launcher configuration safely from config/launcher.json."""
    config_path = get_launcher_config_path(project_dir)
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to load launcher config from {config_path}: {e}\n")
    return {}


def save_launcher_config(config: dict[str, Any], project_dir: str | None = None) -> None:
    """Saves launcher configuration to config/launcher.json atomically."""
    config_path = get_launcher_config_path(project_dir)
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    temp_path = config_path + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        if os.name == "nt" and os.path.exists(config_path):
            try:
                os.replace(temp_path, config_path)
            except OSError:
                os.remove(config_path)
                os.rename(temp_path, config_path)
        else:
            os.replace(temp_path, config_path)
    except Exception as e:
        sys.stderr.write(f"Error saving launcher config to {config_path}: {e}\n")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def get_effective_download_dir(project_dir: str | None = None) -> str:
    """Resolves the download directory following strict 3-tier precedence:
    1. System/User os.environ["DOWNLOAD_DIR"]
    2. config/launcher.json -> download_dir
    3. Default <project_dir>/downloads
    """
    if project_dir is None:
        project_dir = get_project_dir()

    env_dir = os.environ.get("DOWNLOAD_DIR")
    if env_dir and env_dir.strip():
        return os.path.abspath(env_dir.strip())

    config = load_launcher_config(project_dir)
    cfg_dir = config.get("download_dir")
    if cfg_dir and isinstance(cfg_dir, str) and cfg_dir.strip():
        return os.path.abspath(cfg_dir.strip())

    return os.path.abspath(os.path.join(project_dir, "downloads"))


def validate_directory_writable(target_dir: str) -> tuple[bool, str]:
    """Ensures the directory exists and probes write permissions with a temporary file."""
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as e:
        return False, f"Could not create directory: {e}"

    test_probe = os.path.join(target_dir, ".localtune_write_test")
    try:
        with open(test_probe, "w", encoding="utf-8") as f:
            f.write("probe")
        if os.path.exists(test_probe):
            try:
                os.remove(test_probe)
            except OSError:
                pass
        return True, ""
    except Exception as e:
        return False, f"Directory is not writable: {e}"
