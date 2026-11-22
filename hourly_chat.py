import asyncio, json, os, ssl, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8737580198:AAG2jDYE-5YI1WbufZYp2oGrzL0ZQu-7pfE").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROUP_CHAT = os.environ.get("FORCE_GROUP_USERNAME", "arbsupportagent").strip()
IST = timezone(timedelta(hours=5, minutes=30))

def _groq_short_msg() -> str:
    now_ist = datetime.now(IST).strftime("%I:%M %p IST")
    key = (GROQ_API_KEY or "").strip()
    if key.startswith("Gsk_"):
        key = "gsk_" + key[4:]
    if not key:
        return f"({now_ist}) Kya chal raha hai be? Koi zinda hai ya sab so gaye?"

    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {
                "role": "system",
                "content": (
                    f"Indian Time: {now_ist}. Desi Hinglish mein 1-2 short line ka mazedar/savage group message likh. "
                    "AR Wallet, order, matching ya payment ka zikar bilkul mat kar."
                )
            },
            {"role": "user", "content": "Har ghante wali short desi masti/banter line bhej."}
        ],
        "temperature": 0.95,
        "max_tokens": 90
    }
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": "Mozilla/5.0"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            txt = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
            return txt[:500] if txt else f"({now_ist}) Bol be, kya scene hai?"
    except Exception:
        return f"({now_ist}) Kya haal hai saalon, sab thik?"

def _send_tg_message(text: str):
    chat_id = f"@{GROUP_CHAT.lstrip('@')}" if GROUP_CHAT else ""
    if not chat_id or not BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context())
    except urllib.error.URLError:
        pass

async def hourly_loop():
    last_slot = ""
    while True:
        now = datetime.now(IST)
        slot = now.strftime("%Y-%m-%d-%H")
        if slot != last_slot:
            last_slot = slot
            msg = await asyncio.get_running_loop().run_in_executor(None, _groq_short_msg)
            await asyncio.get_running_loop().run_in_executor(None, _send_tg_message, msg)
        await asyncio.sleep(30)
