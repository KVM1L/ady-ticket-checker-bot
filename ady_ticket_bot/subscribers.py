import json
import os

DEFAULT_STATE = {"offset": None, "chat_ids": []}


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return dict(DEFAULT_STATE)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {"offset": data.get("offset"), "chat_ids": list(data.get("chat_ids", []))}


def _save(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_file = path + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, path)


def load_subscribers(path: str) -> set[str]:
    return set(_load(path)["chat_ids"])


def save_subscribers(path: str, chat_ids: set[str]) -> None:
    state = _load(path)
    state["chat_ids"] = sorted(chat_ids)
    _save(path, state)


def load_offset(path: str):
    return _load(path)["offset"]


def save_offset(path: str, offset) -> None:
    state = _load(path)
    state["offset"] = offset
    _save(path, state)
