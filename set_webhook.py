from __future__ import annotations
import json
import os
import sys
import urllib.request
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
bot_token = os.environ.get("BOT_TOKEN", "").strip()
base_url = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
if not bot_token or not base_url.startswith("https://") or not secret:
    print("Заполни BOT_TOKEN, PUBLIC_BASE_URL (HTTPS) и TELEGRAM_WEBHOOK_SECRET в .env")
    sys.exit(1)


def call(method: str, payload: dict):
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode())


result = call("setWebhook", {
    "url": base_url + "/telegram/webhook",
    "secret_token": secret,
    "allowed_updates": ["message", "pre_checkout_query"],
    "drop_pending_updates": False,
})
print("Webhook:")
print(json.dumps(result, ensure_ascii=False, indent=2))
if not result.get("ok"):
    sys.exit(1)

try:
    menu_result = call("setChatMenuButton", {
        "menu_button": {
            "type": "web_app",
            "text": "Играть",
            "web_app": {"url": base_url},
        }
    })
    print("\nКнопка меню:")
    print(json.dumps(menu_result, ensure_ascii=False, indent=2))
except Exception as exc:
    print(f"Не удалось автоматически настроить кнопку меню: {exc}")
    print("Её можно задать вручную через @BotFather → /setmenubutton.")
