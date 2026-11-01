"""Settings the admin can change from the web: Gemini API key, Gemini models and the AI password.

Values saved by the admin live in config/settings.json in the app's storage and take
priority over the environment (Secret Manager on Cloud Run, .env locally). Each instance
caches them briefly, so a change reaches every Cloud Run instance within CACHE_SECONDS.
"""
import os
import threading
import time
from typing import Any, Dict, Optional

from storage import store

SETTINGS_KEY = "config/settings.json"
DEFAULT_MODEL = "gemini-3.5-flash"
CACHE_SECONDS = 30

_lock = threading.Lock()
_cache: Dict[str, Any] = {"loaded_at": 0.0, "data": {}}


def load(force: bool = False) -> Dict[str, Any]:
    with _lock:
        if force or time.time() - _cache["loaded_at"] > CACHE_SECONDS:
            _cache["data"] = store.get(SETTINGS_KEY) or {}
            _cache["loaded_at"] = time.time()
        return dict(_cache["data"])


def update(changes: Dict[str, Optional[str]], updated_by: str) -> Dict[str, Any]:
    """Apply changes; a value of None removes the admin override for that setting"""
    data = load(force=True)
    for name, value in changes.items():
        if value is None:
            data.pop(name, None)
        else:
            data[name] = value
    data["updated_by"] = updated_by
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    store.put(SETTINGS_KEY, data)
    with _lock:
        _cache["data"] = data
        _cache["loaded_at"] = time.time()
    return data


def _value(name: str, env_name: str) -> str:
    return load().get(name) or os.getenv(env_name, "")


def _source(name: str, env_name: str) -> str:
    if load().get(name):
        return "admin"
    return "environment" if os.getenv(env_name) else "none"


def gemini_api_key() -> str:
    return _value("gemini_api_key", "GOOGLE_API_KEY")


def gemini_api_key_source() -> str:
    return _source("gemini_api_key", "GOOGLE_API_KEY")


def deployment_chat_model() -> str:
    return os.getenv("GEMINI_MODEL") or DEFAULT_MODEL


def deployment_eval_model() -> str:
    return os.getenv("GEMINI_EVAL_MODEL") or deployment_chat_model()


def chat_model() -> str:
    """Model for the chat, sentiment, suggestions and emotion advice"""
    return load().get("chat_model") or deployment_chat_model()


def eval_model() -> str:
    """Model that scores a case when it is closed"""
    return load().get("eval_model") or deployment_eval_model()


def ai_password() -> str:
    return _value("ai_password", "AI_PASSWORD")


def ai_password_source() -> str:
    return _source("ai_password", "AI_PASSWORD")
