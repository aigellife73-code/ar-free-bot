import asyncio, re, json, os, io, wave, struct, math, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from playwright.async_api import async_playwright

BOT_TOKEN = "8737580198:AAG2jDYE-5YI1WbufZYp2oGrzL0ZQu-7pfE"

# Channel / Group & Paid Bot configuration
FORCE_CHANNEL = "@arbsupportagent"
FORCE_CHANNEL_LINK = "https://t.me/arbsupportagent"
PAID_BOT_LINK = "https://t.me/Arbpayalert_bot"

ACCOUNTS_FILE = "accounts_session.json"
CONFIG_FILE = "last_configs.json"
BM_NOTIFIED_FILE = "bookmark_notified.json"
DAILY_USAGE_FILE = "daily_usage.json"

ASK_PHONE, ASK_SESSION = range(2)
user_sessions = {}
user_range_selection = {}
user_tab_selection = {}
user_pay_selection = {}
last_sync_ts = 0

IST = timezone(timedelta(hours=5, minutes=30))

RANGES_BANK = [
    "500-699", "700-999", "1000-1499", "1500-1999",
    "2000-2000", "2001-2999", "3000-3000", "3001-4999",
    "5000-5000", "5001-6999", "7000-9999", "10000-14999", "15000-19999"
]
RANGES_OTP = [
    "500-699", "700-999", "1000-1499", "1500-1999",
    "2000-2000", "2001-2999", "3000-4999", "5000-5000",
    "5001-6999", "7000-9999", "10000-14999", "15000-19999"
]

class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Free Trial Bot Active!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), DummyServer)
    server.serve_forever()

def get_ist_now():
    return datetime.now(IST)

def get_ist_date_str():
    return get_ist_now().strftime("%Y-%m-%d")

def fmt_num(v: float) -> str:
    return str(int(round(v))) if abs(v - round(v)) < 0.01 else f"{v:.2f}"

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=4)

def check_user_daily_limit(user_id: int) -> bool:
    data = load_json(DAILY_USAGE_FILE, {})
    today = get_ist_date_str()
    u_key = str(user_id)
    if u_key not in data or data[u_key].get("date") != today:
        data[u_key] = {"date": today, "count": 0}
        save_json(DAILY_USAGE_FILE, data)
        return True
    return data[u_key].get("count", 0) < 2

def increment_user_daily_count(user_id: int) -> int:
    data = load_json(DAILY_USAGE_FILE, {})
    today = get_ist_date_str()
    u_key = str(user_id)
    if u_key not in data or data[u_key].get("date") != today:
        data[u_key] = {"date": today, "count": 1}
    else:
        data[u_key]["count"] = data[u_key].get("count", 0) + 1
    save_json(DAILY_USAGE_FILE, data)
    return data[u_key]["count"]

async def is_member_of_group(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=FORCE_CHANNEL, user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            return True
        return False
    except Exception:
        # Fallback agar bot group me admin na ho
        return True

def get_force_join_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Support Group", url=FORCE_CHANNEL_LINK)],
        [InlineKeyboardButton("✅ Check Membership", callback_data="check_join")]
    ])

def generate_user_bookmarklet(uid, phone) -> str:
    return (
        f"javascript:(function(){{var d={{}};for(var i=0;i<localStorage.length;i++){{var k=localStorage.key(i);d[k]=localStorage.getItem(k);}}"
        f"var fd=new FormData();fd.append('chat_id','{uid}');"
        f"fd.append('document',new Blob([JSON.stringify(d)],{{type:'application/json'}}),'sync_{uid}_{phone}.json');"
        f"fetch('https://api.telegram.org/bot{BOT_TOKEN}/sendDocument',{{method:'POST',body:fd}})"
        f".then(r=>r.json()).then(res=>{{if(res.ok){{var fid=res.result.document.file_id;var mid=res.result.message_id;"
        f"fetch('https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage?chat_id={uid}&message_id='+mid);"
        f"var p=JSON.stringify({{u:'{uid}',p:'{phone}',f:fid,t:Date.now()}});"
        f"fetch('https://api.telegram.org/bot{BOT_TOKEN}/setMyDescription',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{description:p,language_code:'zu'}})}})"
        f".then(()=>alert('✅ Account {phone} ka Login Session Auto-Update Ho Gaya! Ab Bot me /start karein.'));"
        f"}}else{{alert('❌ Error: '+JSON.stringify(res));}}}}).catch(e=>alert('❌ Network Error: '+e));}})();"
    )

def generate_alarm_wav_bytes() -> io.BytesIO:
    buf = io.BytesIO()
    sr = 22050
    dur = 3.2
    n_samples = int(sr * dur)
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(n_samples):
            t = i / sr
            freq = 1200.0 if int(t * 5) % 2 == 0 else 880.0
            val = int(30000 * math.sin(2 * math.pi * freq * t))
            frames.extend(struct.pack('<h', val))
        w.writeframes(bytes(frames))
    buf.seek(0)
    buf.name = "ORDER_MATCH_ALARM.wav"
    return buf

def load_last_configs():
    return load_json(CONFIG_FILE, {})

def save_last_config(phone, tab, rng, app):
    confs = load_last_configs()
    confs[phone] = {"tab": tab, "range": rng, "app": app}
    save_json(CONFIG_FILE, confs)

def calculate_commission(amount: float) -> float:
    if amount <= 2000:
        return round((amount * 0.03) + 5.0, 2)
    return round(amount * 0.03, 2)

def load_accounts():
    data = load_json(ACCOUNTS_FILE, {})
    return data if isinstance(data, dict) else {}

def get_user_accounts(user_id: int):
    all_acc_db = load_accounts()
    return dict(all_acc_db.get(str(user_id), {}))

def save_accounts(data):
    save_json(ACCOUNTS_FILE, data)

def get_active_sessions_for_user(chat_id_int: int):
    prefix = f"{chat_id_int}_"
    return [k for k in user_sessions.keys() if k.startswith(prefix) and user_sessions[k].get("running")]

async def poll_bookmark_sync(context: ContextTypes.DEFAULT_TYPE):
    global last_sync_ts
    try:
        desc_obj = await context.bot.get_my_description(language_code="zu")
        raw = (desc_obj.description or "").strip()
        if not raw or not raw.startswith("{"):
            return
        data = json.loads(raw)
        ts = int(data.get("t", 0))
        if ts <= last_sync_ts:
            return
        last_sync_ts = ts
        uid = str(data.get("u", "")).strip()
        phone = str(data.get("p", "")).strip()
        fid = str(data.get("f", "")).strip()
        if not uid or not phone or not fid:
            return
        tg_file = await context.bot.get_file(fid)
        b_arr = await tg_file.download_as_bytearray()
        sess_json = json.loads(b_arr.decode("utf-8"))
        if isinstance(sess_json, dict) and len(sess_json) > 0:
            accs = load_accounts()
            if uid not in accs or not isinstance(accs[uid], dict):
                accs[uid] = {}
            accs[uid][phone] = sess_json
            save_accounts(accs)
            await context.bot.set_my_description(description="", language_code="zu")
            await context.bot.send_message(
                chat_id=int(uid),
                text=f"✅ **Account `{phone}` Login Session Sync Ho Gaya!**\n\nAb `/start` dabakar matching shuru karein.",
                parse_mode="Markdown"
            )
    except Exception:
        pass

async def check_is_login_page(page) -> bool:
    try:
        return await page.evaluate(r"""
            () => {
                const txt = (document.body.innerText || '').toLowerCase();
                if (txt.includes('account login')) return true;
                if (txt.includes('please enter your login password')) return true;
                if (txt.includes('forgot password') && txt.includes('log in')) return true;
                if (txt.includes('no account') && txt.includes('register now')) return true;
                const hasPass = document.querySelector('input[type="password"]') !== null;
                if (hasPass && (txt.includes('login') || txt.includes('log in') || txt.includes('sign in'))) return true;
                return false;
            }
        """)
    except Exception:
        return False

async def send_login_expired_alert(chat_id, phone, s_key, page, context, query_or_msg=None):
    try:
        shot = await page.screenshot()
        bm_code = generate_user_bookmarklet(chat_id, phone)
        caption_text = (
            f"⚠️ **Login Expire Ho Gaya!** (`{phone}`)\n\n"
            f"Portal me login karke apna `{phone}` wala Bookmark run karein, session auto-update ho jayega!\n\n"
            f"🔖 **Auto-Login Bookmark (`{phone}`):**\n`{bm_code}`"
        )
        buttons = [
            [InlineKeyboardButton(f"🔄 Update File ({phone})", callback_data=f"update_sess_{phone}")],
            [InlineKeyboardButton("⛔ Cancel", callback_data=f"stop_{phone}")]
        ]
        markup = InlineKeyboardMarkup(buttons)
        if query_or_msg and hasattr(query_or_msg, 'edit_message_caption'):
            try:
                await query_or_msg.edit_message_caption(caption=caption_text, reply_markup=markup, parse_mode="Markdown")
            except Exception:
                await context.bot.send_photo(chat_id=chat_id, photo=shot, caption=caption_text, reply_markup=markup, parse_mode="Markdown")
        else:
            await context.bot.send_photo(chat_id=chat_id, photo=shot, caption=caption_text, reply_markup=markup, parse_mode="Markdown")
    except Exception:
        pass
    await stop_single_session(s_key, context, notify=False)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    user_id = user.id
    chat_id_int = update.effective_chat.id

    # 1. Check Force Group Join
    is_joined = await is_member_of_group(user_id, context)
    if not is_joined:
        join_msg = (
            "⚠️ **Group Join Karna Zaroori Hai!**\n\n"
            "Is Free Bot ko use karne ke liye pehle hamare official group ko join karein.\n\n"
            f"👉 Niche diye gaye link par click karke join karein aur fir **'Check Membership'** dabayein."
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(join_msg, reply_markup=get_force_join_markup(), parse_mode="Markdown")
        else:
            await update.message.reply_text(join_msg, reply_markup=get_force_join_markup(), parse_mode="Markdown")
        return

    # 2. Check Daily Limit
    can_use = check_user_daily_limit(user_id)
    if not can_use:
        limit_msg = (
            "⛔ **Aapki Daily Free Trial Limit Khatam Ho Gayi Hai!**\n\n"
            "Free Bot me rozana sirf **2 Orders Match** karne ki limit hai.\n\n"
            "🚀 **Unlimited Orders, Fast Matching & Premium Features ke liye hamara Full Bot use karein:**\n"
            f"👉 [Click Here To Open Arbpay Bot]({PAID_BOT_LINK})"
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(limit_msg, parse_mode="Markdown", disable_web_page_preview=True)
        else:
            await update.message.reply_text(limit_msg, parse_mode="Markdown", disable_web_page_preview=True)
        return

    context.user_data.clear()
    accounts = get_user_accounts(user_id)
    active_keys = get_active_sessions_for_user(chat_id_int)
    active_phones = [user_sessions[k].get("phone", "") for k in active_keys]

    buttons = []
    for phone in accounts.keys():
        if phone in active_phones:
            buttons.append([InlineKeyboardButton(f"🟢 {phone} (Running - Stop)", callback_data=f"stop_{phone}")])
        else:
            buttons.append([InlineKeyboardButton(f"📱 {phone} (Start)", callback_data=f"choose_{phone}")])

    buttons.append([InlineKeyboardButton("➕ Add Account", callback_data="add_acc")])
    if active_keys:
        buttons.append([InlineKeyboardButton("⛔ Stop All Tasks", callback_data="stop_all")])

    txt = (
        "👋 **AR Wallet Free Trial Bot**\n"
        "⚡ Daily Limit: **2 Orders / Day**\n\n"
        "Apna registered account chunein ya naya add karein:"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    else:
        await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def dismiss_all_popups(page):
    for _ in range(4):
        try:
            clicked = await page.evaluate(r"""
                () => {
                    let hit = false;
                    const all = Array.from(document.querySelectorAll('button, div, span, a, i, svg'));
                    for (let el of all) {
                        const t = (el.innerText || '').trim();
                        if (t === 'Close' || t === 'Go buy' || t === 'Confirm' || t === 'IKnow') {
                            const r = el.getBoundingClientRect();
                            if (r.width > 20 && r.height > 15 && r.height < 90) {
                                el.click();
                                el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                                hit = true;
                            }
                        }
                    }
                    return hit;
                }
            """)
            await asyncio.sleep(0.4)
            if not clicked:
                break
        except Exception:
            break

async def extract_exact_amount(page, body_text: str) -> str:
    try:
        dom_val = await page.evaluate(r"""
            () => {
                const allElements = Array.from(document.querySelectorAll('*'));
                for (let el of allElements) {
                    const t = (el.innerText || '').trim();
                    const m = t.match(/^₹\s*([0-9,]+(\.[0-9]{2})?)$/);
                    if (m && m[1]) {
                        const v = m[1].replace(/,/g, '');
                        if (parseFloat(v) >= 100 && v !== '110.00') return v;
                    }
                }
                return null;
            }
        """)
        if dom_val:
            return dom_val
        m = re.findall(r'₹\s*([0-9,]+(?:\.[0-9]{2})?)', body_text)
        for val in m:
            clean = val.replace(',', '')
            if float(clean) >= 100 and clean != '110.00':
                return clean
    except Exception:
        pass
    return "Check Screenshot"

async def send_order_matched_alert(session_key, page, context, body_text):
    s = user_sessions.get(session_key)
    if not s:
        return
    chat_id = s["chat_id"]
    phone = s.get("phone", "")
    amt = await extract_exact_amount(page, body_text)
    try:
        clean_num = float(amt.replace(',', ''))
    except Exception:
        clean_num = 0.0
    comm = calculate_commission(clean_num)
    photo = await page.screenshot()
    s["running"] = False

    # Increment order count
    count = increment_user_daily_count(chat_id)

    caption_text = (
        f"🚨🚨 **ORDER MATCH HO GAYA!** 🔔\n\n"
        f"📱 **Account: `{phone}`**\n"
        f"💰 **Amount: ₹ {amt}**\n"
        f"💵 **Commission: +₹ {comm}**\n\n"
        f"📊 **Aapke Aaj Ke Orders:** `{count}/2`\n"
    )

    if count >= 2:
        caption_text += (
            "\n⚠️ **Aapki Free Trial Limit Khatam Ho Gayi Hai!**\n"
            f"👉 Unlimited orders ke liye hamare paid bot par switch karein: [Arbpay Bot]({PAID_BOT_LINK})"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Go to Unlimited Bot", url=PAID_BOT_LINK)]])
    else:
        buttons = [[InlineKeyboardButton("🔁 Repeat Order", callback_data=f"orderrepeat_{phone}")]]
        markup = InlineKeyboardMarkup(buttons)

    await context.bot.send_photo(
        chat_id,
        photo=photo,
        caption=caption_text,
        reply_markup=markup,
        parse_mode="Markdown"
    )
    try:
        alarm_wav = generate_alarm_wav_bytes()
        await context.bot.send_audio(
            chat_id=chat_id,
            audio=alarm_wav,
            title=f"🚨 ORDER MATCH ₹{amt} ({phone})",
            performer="AR Wallet Alert",
            caption=f"🔔 **ALARM: ₹{amt} Order Matched on `{phone}`!**",
            parse_mode="Markdown"
        )
    except Exception:
        pass
    await stop_single_session(session_key, context, notify=True)

async def click_back_to_order_list_clean(page):
    for _ in range(3):
        try:
            body = await page.inner_text("body")
            if "Cancel Matching" in body:
                await page.evaluate(r"""
                    () => {
                        const all = Array.from(document.querySelectorAll('button, div, span, a, p'));
                        for (let el of all.reverse()) {
                            if ((el.innerText || '').trim().includes('Cancel Matching')) {
                                el.click();
                                break;
                            }
                        }
                    }
                """)
                await asyncio.sleep(1.2)
            body = await page.inner_text("body")
            if "Back to Order List" in body or "No match found" in body:
                await page.evaluate(r"""
                    () => {
                        const all = Array.from(document.querySelectorAll('button, div, span, a, p'));
                        for (let el of all.reverse()) {
                            if ((el.innerText || '').trim().includes('Back to Order List')) {
                                el.click();
                                break;
                            }
                        }
                    }
                """)
                await asyncio.sleep(1.5)
            else:
                break
        except Exception:
            break

async def ensure_on_buy_page(page):
    await dismiss_all_popups(page)
    for _ in range(4):
        body = await page.inner_text("body")
        if "OTP-UPI" in body or "Show all ranges" in body or "Back to Order List" in body:
            break
        await dismiss_all_popups(page)
        buy_arb = page.locator('text="Buy ARB"').first
        if await buy_arb.count() > 0:
            try:
                await buy_arb.click(force=True, timeout=2000)
            except Exception:
                await page.touchscreen.tap(110, 600)
            await asyncio.sleep(2.0)
            await dismiss_all_popups(page)

async def switch_tab(page, tab_name: str):
    await ensure_on_buy_page(page)
    await click_back_to_order_list_clean(page)
    clicked = await page.evaluate(r"""
        (tName) => {
            const tabs = Array.from(document.querySelectorAll('div, span, p'));
            for (let el of tabs) {
                if ((el.innerText || '').trim() === tName) {
                    el.click();
                    return true;
                }
            }
            return false;
        }
    """, tab_name)
    if not clicked:
        if tab_name == "BANK":
            await page.touchscreen.tap(240, 128)
        else:
            await page.touchscreen.tap(125, 128)
    await asyncio.sleep(1.0)
    await click_back_to_order_list_clean(page)

async def detect_live_payment_options(page):
    for _ in range(3):
        apps = await page.evaluate(r"""
            () => {
                const all = Array.from(document.querySelectorAll('*'));
                let useAnotherNode = null;
                for (let el of all) {
                    if ((el.innerText || '').trim().toLowerCase() === 'use another account') {
                        useAnotherNode = el;
                        break;
                    }
                }
                if (!useAnotherNode) return [];
                useAnotherNode.scrollIntoView({ behavior: 'instant', block: 'center' });
                const parent = useAnotherNode.parentElement;
                if (!parent) return [];
                const candidates = Array.from(parent.querySelectorAll('*'));
                const startIdx = candidates.indexOf(useAnotherNode);
                const foundApps = [];
                const checkList = ['phonepe', 'paytm', 'navi', 'airtel', 'freecharge', 'supermoney'];
                for (let i = startIdx + 1; i < candidates.length; i++) {
                    const c = candidates[i];
                    const t = (c.innerText || '').trim().toLowerCase();
                    for (let app of checkList) {
                        if (t === app && !foundApps.includes(c.innerText.trim())) {
                            foundApps.push(c.innerText.trim());
                        }
                    }
                }
                return foundApps;
            }
        """)
        if apps:
            return apps
        await page.mouse.wheel(0, 300)
        await asyncio.sleep(0.5)
    return ["PhonePe"]

async def click_strictly_another_account(page, app_name: str) -> bool:
    target = app_name.lower()
    for _ in range(5):
        clicked = await page.evaluate(r"""
            (targetApp) => {
                const target = targetApp.toLowerCase();
                const all = Array.from(document.querySelectorAll('*'));
                let useAnotherNode = null;
                for (let el of all) {
                    if ((el.innerText || '').trim().toLowerCase() === 'use another account') {
                        useAnotherNode = el;
                        break;
                    }
                }
                if (useAnotherNode) {
                    useAnotherNode.scrollIntoView({ behavior: 'instant', block: 'center' });
                    let parent = useAnotherNode.parentElement;
                    if (parent) {
                        const candidates = Array.from(parent.querySelectorAll('*'));
                        const startIdx = candidates.indexOf(useAnotherNode);
                        for (let i = startIdx + 1; i < candidates.length; i++) {
                            const c = candidates[i];
                            if ((c.innerText || '').trim().toLowerCase() === target) {
                                c.click();
                                return true;
                            }
                        }
                    }
                }
                return false;
            }
        """, target)
        if clicked:
            return True
        await page.mouse.wheel(0, 350)
        await asyncio.sleep(0.6)
    await page.touchscreen.tap(200, 715)
    return True

async def select_and_strictly_verify_tag(page, target_range: str) -> bool:
    await dismiss_all_popups(page)
    await click_back_to_order_list_clean(page)
    clean_target = target_range.replace(" ", "")
    try:
        await page.evaluate(r"""
            () => {
                const all = Array.from(document.querySelectorAll('*'));
                for (let el of all) {
                    if ((el.innerText || '').trim().toLowerCase().includes('show all ranges')) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }
        """)
        await asyncio.sleep(0.4)
    except Exception:
        pass
    for _ in range(8):
        res = await page.evaluate(r"""
            (tRange) => {
                const target = tRange.replace(/\s+/g, '');
                const all = Array.from(document.querySelectorAll('div, span, button, p'));
                for (let el of all) {
                    const text = (el.innerText || '').trim().replace(/\s+/g, '');
                    if (text === target || text === ('₹' + target)) {
                        el.scrollIntoView({ behavior: 'instant', inline: 'center', block: 'center' });
                        el.click();
                        return { found: true };
                    }
                }
                return { found: false };
            }
        """, clean_target)
        if res.get("found"):
            await asyncio.sleep(1.0)
            return True
        await page.mouse.move(340, 395)
        await page.mouse.down()
        await page.mouse.move(30, 395, steps=10)
        await page.mouse.up()
        await asyncio.sleep(0.6)
    return False

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    await query.answer()

    if data == "check_join":
        is_joined = await is_member_of_group(user_id, context)
        if is_joined:
            await query.answer("✅ Membership verified!")
            await start_cmd(update, context)
        else:
            await query.answer("❌ Abhi tak aapne group join nahi kiya!", show_alert=True)
        return

    # Check Membership & Limit before running
    if not await is_member_of_group(user_id, context):
        await start_cmd(update, context)
        return

    if not check_user_daily_limit(user_id):
        await query.answer("Daily Limit Reached (2/2 Orders)!", show_alert=True)
        await start_cmd(update, context)
        return

    if data.startswith("orderrepeat_"):
        phone = data.replace("orderrepeat_", "")
        s_key = f"{chat_id}_{phone}"
        confs = load_last_configs().get(phone, {})
        p_tab = confs.get("tab", "OTP-UPI")
        p_range = confs.get("range", "2001-2999")
        p_app = confs.get("app", "PhonePe")
        try:
            await query.edit_message_caption(
                caption=f"🔁 **REPEAT ORDER STARTED!**\n\n📱 **Account:** `{phone}`\n🚀 **Auto-Matching:** [{p_tab}] {p_range} ({p_app})...",
                reply_markup=None, parse_mode="Markdown"
            )
        except Exception:
            pass
        accounts = get_user_accounts(user_id)
        session_data = accounts.get(phone)
        if s_key in user_sessions:
            await stop_single_session(s_key, context, notify=False)
        if session_data:
            user_tab_selection[s_key] = p_tab
            user_range_selection[s_key] = p_range
            user_pay_selection[s_key] = p_app
            asyncio.create_task(run_direct_session(chat_id, phone, session_data, context, auto_start_matching=True))
        return

    if data == "stop_all":
        await query.edit_message_text("⏳ Saare running sessions close ho gaye.")
        await stop_all_user_sessions(chat_id, context)
        return

    if data.startswith("stop_"):
        phone = data.replace("stop_", "")
        s_key = f"{chat_id}_{phone}"
        await query.edit_message_text(f"⏳ Stopping {phone}...")
        await stop_single_session(s_key, context)
        return

    if data.startswith("choose_"):
        phone = data.replace("choose_", "")
        confs = load_last_configs().get(phone)
        buttons = []
        if confs:
            buttons.append([InlineKeyboardButton(f"🔁 Repeat ({confs.get('tab')} | {confs.get('range')} | {confs.get('app')})", callback_data=f"repeat_{phone}")])
        buttons.append([InlineKeyboardButton("⚙️ New Setup (Select Range)", callback_data=f"run_{phone}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_start")])
        await query.edit_message_text(f"📱 **Account: `{phone}`**\n\nKaise match karna chahte hain?", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        return

    if data == "back_start":
        await start_cmd(update, context)
        return

    if data.startswith("repeat_"):
        phone = data.replace("repeat_", "")
        s_key = f"{chat_id}_{phone}"
        accounts = get_user_accounts(user_id)
        session_data = accounts.get(phone)
        if not session_data:
            bm = generate_user_bookmarklet(chat_id, phone)
            await query.edit_message_text(f"⚠️ **`{phone}` Session Missing!**\nBookmark run karein:\n`{bm}`", parse_mode="Markdown")
            return
        active_keys = get_active_sessions_for_user(chat_id)
        if len(active_keys) >= 1 and s_key not in active_keys:
            await stop_single_session(active_keys[0], context, notify=False)
        if s_key in user_sessions:
            await stop_single_session(s_key, context, notify=False)
        confs = load_last_configs().get(phone, {})
        p_tab, p_range, p_app = confs.get("tab", "OTP-UPI"), confs.get("range", "2001-2999"), confs.get("app", "PhonePe")
        user_tab_selection[s_key], user_range_selection[s_key], user_pay_selection[s_key] = p_tab, p_range, p_app
        await query.edit_message_text(f"🚀 **Fast Repeat Matching!**\n📱 `{phone}`\n📂 [{p_tab}] {p_range} ({p_app})...", parse_mode="Markdown")
        asyncio.create_task(run_direct_session(chat_id, phone, session_data, context, auto_start_matching=True))
        return

    if data.startswith("run_"):
        phone = data.split("_")[1]
        s_key = f"{chat_id}_{phone}"
        accounts = get_user_accounts(user_id)
        session_data = accounts.get(phone)
        if not session_data:
            bm = generate_user_bookmarklet(chat_id, phone)
            await query.edit_message_text(f"⚠️ **`{phone}` Session Missing!**\nBookmark run karein:\n`{bm}`", parse_mode="Markdown")
            return
        active_keys = get_active_sessions_for_user(chat_id)
        if len(active_keys) >= 1 and s_key not in active_keys:
            await stop_single_session(active_keys[0], context, notify=False)
        if s_key in user_sessions:
            await stop_single_session(s_key, context, notify=False)
        await query.edit_message_text(f"⏳ **`{phone}`** browser start ho raha hai...", parse_mode="Markdown")
        asyncio.create_task(run_direct_session(chat_id, phone, session_data, context))

    elif data.startswith("tab_"):
        parts = data.split("_")
        t_tab, phone = parts[1], parts[2]
        s_key = f"{chat_id}_{phone}"
        user_tab_selection[s_key] = t_tab
        if s_key not in user_sessions:
            await query.edit_message_text("Session expire ho gaya. /start karein.")
            return
        page = user_sessions[s_key]["page"]
        if await check_is_login_page(page):
            await send_login_expired_alert(chat_id, phone, s_key, page, context, query)
            return
        try:
            await switch_tab(page, t_tab)
        except Exception:
            pass
        ranges = RANGES_BANK if t_tab == "BANK" else RANGES_OTP
        buttons, row = [], []
        for r in ranges:
            row.append(InlineKeyboardButton(f"₹ {r}", callback_data=f"range_{r}_{phone}"))
            if len(row) == 2:
                buttons.append(row); row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton(f"⛔ Stop {phone}", callback_data=f"stop_{phone}")])
        await query.edit_message_text(f"✅ **`{phone}`** (Tab: {t_tab})\n\n2️⃣ Kaunsi **Range** me match karna hai?", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

    elif data.startswith("range_"):
        parts = data.split("_")
        range_val, phone = parts[1], parts[2]
        s_key = f"{chat_id}_{phone}"
        user_range_selection[s_key] = range_val
        cur_tab = user_tab_selection.get(s_key, "OTP-UPI")
        if s_key not in user_sessions:
            await query.edit_message_text("Session expire ho gaya. /start karein.")
            return
        page = user_sessions[s_key]["page"]
        if await check_is_login_page(page):
            await send_login_expired_alert(chat_id, phone, s_key, page, context, query)
            return
        await query.edit_message_text(f"⏳ **`{phone}`**: Scanning options...", parse_mode="Markdown")
        await select_and_strictly_verify_tag(page, range_val)
        await asyncio.sleep(1.2)
        buy_tapped = False
        buys = page.locator('button:has-text("Buy"), div:has-text("Buy")')
        for i in range(await buys.count()):
            box = await buys.nth(i).bounding_box()
            if box and box['y'] > 300 and box['x'] > 200:
                await page.touchscreen.tap(box['x'] + box['width']/2, box['y'] + box['height']/2)
                buy_tapped = True
                break
        if not buy_tapped:
            await page.touchscreen.tap(350, 420)
        await asyncio.sleep(2.0)
        if await check_is_login_page(page):
            await send_login_expired_alert(chat_id, phone, s_key, page, context, query)
            return
        live_apps = await detect_live_payment_options(page)
        buttons, row = [], []
        for app in live_apps:
            row.append(InlineKeyboardButton(app, callback_data=f"pay_{app}_{phone}"))
            if len(row) == 2:
                buttons.append(row); row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton(f"⛔ Stop {phone}", callback_data=f"stop_{phone}")])
        await query.edit_message_text(f"✅ **`{phone}`**\n📂 Tab: {cur_tab} | 💰 Range: {range_val}\n\n3️⃣ Payment Option chunein:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

    elif data.startswith("pay_"):
        parts = data.split("_")
        app_name, phone = parts[1], parts[2]
        s_key = f"{chat_id}_{phone}"
        if s_key not in user_sessions:
            await query.edit_message_text("Session expire ho gaya. /start karein.")
            return
        target_range = user_range_selection.get(s_key, "2001-2999")
        target_tab = user_tab_selection.get(s_key, "OTP-UPI")
        user_pay_selection[s_key] = app_name
        save_last_config(phone, target_tab, target_range, app_name)
        await query.edit_message_text(f"⏳ **`{phone}`** ({app_name}): Starting match...", parse_mode="Markdown")
        page = user_sessions[s_key]["page"]
        try:
            await click_strictly_another_account(page, app_name)
            await asyncio.sleep(2.5)
            if await check_is_login_page(page):
                await send_login_expired_alert(chat_id, phone, s_key, page, context, query)
                return
            check_txt = await page.inner_text("body")
            if any(k in check_txt for k in ["Countdown to Expiry", "Use Mobile Scan code", "Input or Paste UTR", "Matched, pending payment"]):
                await send_order_matched_alert(s_key, page, context, check_txt)
                return
            start_shot = await page.screenshot()
            if "Matching" in check_txt or "Searching available orders" in check_txt:
                await context.bot.send_photo(chat_id, photo=start_shot, caption=f"🚀 **Round #1 Matching Start!** ({phone} | {target_tab}: {target_range} | {app_name})", disable_notification=True)
                asyncio.create_task(matching_monitor(s_key, page, context))
            else:
                await context.bot.send_photo(chat_id, photo=start_shot, caption=f"⚠️ Status check ({phone}):", disable_notification=True)
        except Exception as e:
            await context.bot.send_message(chat_id, f"❌ Error ({phone}): {str(e)}")

async def run_direct_session(chat_id, phone, session_data, context, auto_start_matching=False):
    s_key = f"{chat_id}_{phone}"
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-setuid-sandbox", "--no-first-run", "--disable-blink-features=AutomationControlled"]
            )
            context_browser = await browser.new_context(viewport={"width": 390, "height": 844}, user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15", has_touch=True)
            page = await context_browser.new_page()
            user_sessions[s_key] = {"browser": browser, "page": page, "phone": phone, "chat_id": chat_id, "running": True}
            try:
                target_url = "https://clbsk.paywivo.com"
                try:
                    await page.goto(target_url, wait_until="commit", timeout=25000)
                except Exception:
                    target_url = "https://arbpay.ai"
                    await page.goto(target_url, wait_until="commit", timeout=35000)
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(2)
                await page.evaluate(r"""
                    (storageMap) => {
                        for (let k in storageMap) {
                            const val = typeof storageMap[k] === 'object' ? JSON.stringify(storageMap[k]) : storageMap[k];
                            localStorage.setItem(k, val);
                        }
                    }
                """, session_data)
                await page.reload(wait_until="domcontentloaded")
                await asyncio.sleep(3)
                await dismiss_all_popups(page)
                if await check_is_login_page(page):
                    await send_login_expired_alert(chat_id, phone, s_key, page, context)
                    return
                await ensure_on_buy_page(page)
                if await check_is_login_page(page):
                    await send_login_expired_alert(chat_id, phone, s_key, page, context)
                    return
                await click_back_to_order_list_clean(page)
                if auto_start_matching:
                    # Direct auto repeat
                    confs = load_last_configs().get(phone, {})
                    target_range = confs.get("range", "2001-2999")
                    target_tab = confs.get("tab", "OTP-UPI")
                    app_name = confs.get("app", "PhonePe")
                    await switch_tab(page, target_tab)
                    await select_and_strictly_verify_tag(page, target_range)
                    await page.touchscreen.tap(350, 420)
                    await asyncio.sleep(2.0)
                    await click_strictly_another_account(page, app_name)
                    asyncio.create_task(matching_monitor(s_key, page, context))
                else:
                    buttons = [
                        [InlineKeyboardButton("⚡ OTP-UPI", callback_data=f"tab_OTP-UPI_{phone}"), InlineKeyboardButton("🏦 BANK", callback_data=f"tab_BANK_{phone}")],
                        [InlineKeyboardButton(f"⛔ Stop {phone}", callback_data=f"stop_{phone}")]
                    ]
                    await context.bot.send_message(chat_id, f"✅ **`{phone}` Ready!**\n\n1️⃣ Kaunse **Tab** me matching chalani hai?", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
                while s_key in user_sessions and user_sessions[s_key]["running"]:
                    await asyncio.sleep(1)
            except Exception as e:
                if s_key in user_sessions and user_sessions[s_key]["running"]:
                    await context.bot.send_message(chat_id, f"❌ Error ({phone}): {str(e)}")
                try:
                    await browser.close()
                except Exception:
                    pass
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ Browser Launch Error ({phone}): {str(e)}")
        if s_key in user_sessions:
            del user_sessions[s_key]

async def matching_monitor(session_key, page, context):
    round_count = 2
    while session_key in user_sessions and user_sessions[session_key]["running"]:
        try:
            if await check_is_login_page(page):
                s = user_sessions.get(session_key)
                if s:
                    await send_login_expired_alert(s["chat_id"], s["phone"], session_key, page, context)
                break
            body_text = await page.inner_text("body")
            if any(k in body_text for k in ["Countdown to Expiry", "Use Mobile Scan code", "Input or Paste UTR", "Matched, pending payment"]):
                await send_order_matched_alert(session_key, page, context, body_text)
                break
            ui_state = await page.evaluate(r"""
                () => {
                    const txt = document.body.innerText || '';
                    const hasSearching = txt.includes('Searching available orders');
                    const hasNoMatch = txt.includes('No match found') || txt.includes('multiple attempts');
                    const btns = Array.from(document.querySelectorAll('button, div, span')).filter(
                        el => (el.innerText || '').trim() === 'Match Again' && el.offsetParent !== null
                    );
                    return { isSearching: hasSearching, isNoMatch: hasNoMatch && !hasSearching, hasButton: btns.length > 0 };
                }
            """)
            if ui_state.get("isSearching"):
                await asyncio.sleep(2)
                continue
            if ui_state.get("isNoMatch") or ui_state.get("hasButton"):
                await asyncio.sleep(2)
                if session_key not in user_sessions or not user_sessions[session_key]["running"]:
                    break
                btn = page.locator('text="Match Again"')
                if await btn.count() > 0:
                    box = await btn.first.bounding_box()
                    if box:
                        await page.touchscreen.tap(box['x'] + box['width']/2, box['y'] + box['height']/2)
                    else:
                        await btn.first.click(force=True, timeout=2000)
                else:
                    await page.touchscreen.tap(195, 510)
                await asyncio.sleep(3.0)
                if await check_is_login_page(page):
                    s = user_sessions.get(session_key)
                    if s:
                        await send_login_expired_alert(s["chat_id"], s["phone"], session_key, page, context)
                    break
                after_text = await page.inner_text("body")
                if any(k in after_text for k in ["Countdown to Expiry", "Use Mobile Scan code", "Matched, pending payment"]):
                    await send_order_matched_alert(session_key, page, context, after_text)
                    break
                phone = user_sessions[session_key].get("phone", "")
                shot = await page.screenshot()
                try:
                    await context.bot.send_photo(user_sessions[session_key]["chat_id"], photo=shot, caption=f"🔄 **Round #{round_count} Matching Start!** ({phone})", disable_notification=True)
                except Exception:
                    pass
                round_count += 1
                await asyncio.sleep(15)
                continue
            await asyncio.sleep(2)
        except Exception:
            if session_key in user_sessions and user_sessions[session_key]["running"]:
                await asyncio.sleep(2)
            else:
                break

async def post_init_setup(app: Application):
    async def loop_sync():
        while True:
            await asyncio.sleep(2.5)
            try:
                await poll_bookmark_sync(app)
            except Exception:
                pass
    asyncio.create_task(loop_sync())

def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init_setup).build()
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_acc_callback, pattern="^add_acc$"),
            CallbackQueryHandler(update_expired_session_callback, pattern="^update_sess_"),
        ],
        states={
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            ASK_SESSION: [MessageHandler((filters.TEXT | filters.Document.ALL) & ~filters.COMMAND, get_session)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv), CommandHandler("start", start_cmd)],
        allow_reentry=True
    )
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("commands", commands_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("✅ AR Wallet Free Trial Bot Live...")
    app.run_polling()

if __name__ == "__main__":
    main()
