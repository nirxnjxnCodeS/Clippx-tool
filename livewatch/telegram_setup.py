"""Find your Telegram chat id and save it to .env, then send a test message.
Step 1: open your bot in Telegram, press Start (or send it any message).
Step 2: docker exec -w /app openshorts-backend python livewatch/telegram_setup.py
"""
import os
import re
import sys

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(os.path.dirname(HERE), ".env")
token = next((m.group(1).strip() for l in open(ENV)
              if (m := re.match(r"^\s*TELEGRAM_BOT_TOKEN\s*=\s*(\S+)", l))), "")
if not token:
    sys.exit("TELEGRAM_BOT_TOKEN missing in .env")
api = f"https://api.telegram.org/bot{token}"

me = httpx.get(f"{api}/getMe", timeout=30).json()
if not me.get("ok"):
    sys.exit(f"Token rejected by Telegram: {me.get('description')}")
name = me["result"]["username"]
print(f"Bot OK: @{name}")

# A webhook (set by another tool earlier) makes getUpdates fail; remove it, keep pending messages.
hook = httpx.get(f"{api}/getWebhookInfo", timeout=30).json().get("result", {})
if hook.get("url"):
    print(f"Removing webhook {hook['url']} (it blocks reading messages)")
    httpx.post(f"{api}/deleteWebhook", data={"drop_pending_updates": "false"}, timeout=30)

print(f"Waiting up to 30 s for a message... (send 'hi' to @{name} now if you haven't)")
resp = httpx.get(f"{api}/getUpdates", params={"timeout": 30}, timeout=45).json()
if not resp.get("ok"):
    sys.exit(f"Telegram said: {resp.get('description')}")
chats = []
for u in resp.get("result", []):
    for key in ("message", "edited_message", "my_chat_member", "channel_post"):
        chat = (u.get(key) or {}).get("chat")
        if chat and chat.get("id"):
            chats.append(chat)
if not chats:
    sys.exit(f"Still no messages. Check you're messaging @{name} (exactly this bot), send 'hi', "
             "and run this again within a minute.")
chat = chats[-1]
chat_id = str(chat["id"])
print(f"Found chat: {chat.get('first_name') or chat.get('title')} (id {chat_id})")

lines = [l for l in open(ENV).read().splitlines(keepends=True)
         if not re.match(r"^\s*#?\s*TELEGRAM_CHAT_ID\s*=", l)]
out = []
for l in lines:
    out.append(l)
    if re.match(r"^\s*TELEGRAM_BOT_TOKEN\s*=", l):
        out.append(f"TELEGRAM_CHAT_ID={chat_id}\n")
open(ENV, "w").write("".join(out))
httpx.post(f"{api}/sendMessage", timeout=30,
           data={"chat_id": chat_id, "text": "✅ livewatch connected. Clips will arrive here."})
print(f"Saved TELEGRAM_CHAT_ID={chat_id} to .env and sent a test message.")
