import json
import os


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {"offset": None, "subscribers": {}}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "subscribers" in data:
        subscribers = dict(data.get("subscribers", {}))
    else:
        # Legacy format from before per-user filters: {"chat_ids": [...]}
        subscribers = {chat_id: {} for chat_id in data.get("chat_ids", [])}

    return {"offset": data.get("offset"), "subscribers": subscribers}


def _save(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_file = path + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, path)


def load_subscribers(path: str) -> dict:
    """chat_id (str) -> filter dict (see filters.Filter.to_dict/from_dict)."""
    return _load(path)["subscribers"]


def save_subscribers(path: str, subscribers: dict) -> None:
    state = _load(path)
    state["subscribers"] = subscribers
    _save(path, state)


def load_offset(path: str):
    return _load(path)["offset"]


def save_offset(path: str, offset) -> None:
    state = _load(path)
    state["offset"] = offset
    _save(path, state)
