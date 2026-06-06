"""One-off migration: point chats at built-in local LLM instead of Docker Ollama."""
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "app.db"
SETTINGS = ROOT / "data" / "settings.json"
CHAT_URL = "http://127.0.0.1:11435/v1/chat/completions"
BASE_URL = "http://127.0.0.1:11435/v1"
MODEL_ID = str(ROOT / "data" / "models" / "bundled" / "qwen2.5-3b-instruct-q4_k_m.gguf")
NOW = datetime.utcnow().isoformat()


def main() -> None:
    conn = sqlite3.connect(DB)
    row = conn.execute(
        "SELECT id FROM model_endpoints WHERE base_url = ?", (BASE_URL,)
    ).fetchone()
    if row:
        ep_id = row[0]
        conn.execute(
            "UPDATE model_endpoints SET is_enabled = 1 WHERE id = ?", (ep_id,)
        )
    else:
        ep_id = str(uuid.uuid4())[:8]
        conn.execute(
            """
            INSERT INTO model_endpoints
                (id, name, base_url, is_enabled, model_type, created_at, updated_at)
            VALUES (?, ?, ?, 1, 'llm', ?, ?)
            """,
            (ep_id, "Built-in AI (local)", BASE_URL, NOW, NOW),
        )

    conn.execute(
        "UPDATE model_endpoints SET is_enabled = 0 WHERE base_url LIKE ?",
        ("%host.docker.internal%",),
    )
    conn.execute(
        "UPDATE sessions SET endpoint_url = ?, model = ? WHERE endpoint_url LIKE ?",
        (CHAT_URL, MODEL_ID, "%host.docker.internal%"),
    )
    conn.execute(
        "UPDATE sessions SET model = ? WHERE endpoint_url LIKE ? AND model != ?",
        (MODEL_ID, "%127.0.0.1:11435%", MODEL_ID),
    )
    conn.commit()
    print("endpoint:", ep_id)
    for sess in conn.execute("SELECT id, model, endpoint_url FROM sessions"):
        print("session:", sess)
    conn.close()

    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    settings["default_endpoint_id"] = ep_id
    settings["default_model"] = MODEL_ID
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print("settings updated")


if __name__ == "__main__":
    main()
