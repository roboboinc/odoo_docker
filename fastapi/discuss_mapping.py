"""
Persistent mapping: Receevi conversation_id <-> Odoo Discuss channel_id.
Stored in JSON file for simplicity (single-instance deployment).
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

# Use /app/data in container; ensure directory exists
MAPPING_DIR = Path(os.getenv("DISCUSS_MAPPING_DIR", "/app/data"))
MAPPING_FILE = MAPPING_DIR / "discuss_mapping.json"


def _ensure_dir():
    MAPPING_DIR.mkdir(parents=True, exist_ok=True)


def _load() -> Dict[str, Any]:
    _ensure_dir()
    if not MAPPING_FILE.exists():
        return {"conversations": {}, "last_message_ids": {}}
    try:
        with open(MAPPING_FILE) as f:
            return json.load(f)
    except Exception:
        return {"conversations": {}, "last_message_ids": {}}


def _save(data: Dict[str, Any]) -> None:
    _ensure_dir()
    with open(MAPPING_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_channel_for_conversation(conversation_id: int) -> Optional[Dict[str, Any]]:
    """Get channel_id and partner_id for a Receevi conversation."""
    data = _load()
    key = str(conversation_id)
    return data.get("conversations", {}).get(key)


def set_channel_for_conversation(
    conversation_id: int,
    channel_id: int,
    partner_id: int,
    inbox_id: Optional[int] = None,
) -> None:
    """Store mapping from conversation to Odoo channel (optional inbox_id for inbox-channel sync)."""
    data = _load()
    if "conversations" not in data:
        data["conversations"] = {}
    entry = {"channel_id": channel_id, "partner_id": partner_id}
    if inbox_id is not None:
        entry["inbox_id"] = inbox_id
    data["conversations"][str(conversation_id)] = entry
    _save(data)


def migrate_conversation_id(old_id: int, new_id: int) -> bool:
    """
    Rekey mapping from old_id (e.g. display_id) to new_id (internal id).
    Returns True if migration was done.
    """
    data = _load()
    key_old = str(old_id)
    key_new = str(new_id)
    if key_old not in data.get("conversations", {}):
        return False
    entry = data["conversations"].pop(key_old)
    data["conversations"][key_new] = entry
    if key_old in data.get("last_message_ids", {}):
        data["last_message_ids"][key_new] = data["last_message_ids"].pop(key_old)
    _save(data)
    return True


def get_last_message_id(conversation_id: int) -> int:
    """Last Odoo message id we've synced to Chatwoot for this conversation."""
    data = _load()
    return data.get("last_message_ids", {}).get(str(conversation_id), 0)


def set_last_message_id(conversation_id: int, message_id: int) -> None:
    """Update last synced message id."""
    data = _load()
    if "last_message_ids" not in data:
        data["last_message_ids"] = {}
    data["last_message_ids"][str(conversation_id)] = message_id
    _save(data)


def remove_channel_for_conversation(conversation_id: int) -> None:
    """Remove mapping for a conversation (e.g. when Odoo channel was deleted)."""
    data = _load()
    key = str(conversation_id)
    if key in data.get("conversations", {}):
        del data["conversations"][key]
    if key in data.get("last_message_ids", {}):
        del data["last_message_ids"][key]
    _save(data)


def get_all_conversation_mappings() -> Dict[int, Dict[str, Any]]:
    """All conversation_id -> {channel_id, partner_id} for polling."""
    data = _load()
    result = {}
    for k, v in data.get("conversations", {}).items():
        try:
            result[int(k)] = v
        except ValueError:
            pass
    return result
