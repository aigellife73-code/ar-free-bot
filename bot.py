import asyncio, re, json, os, io, html, wave, struct, math
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from playwright.async_api import async_playwright

BOT_TOKEN = "8737580198:AAG2jDYE-5YI1WbufZYp2oGrzL0ZQu-7pfE"

ACCOUNTS_FILE = "accounts_session.json"
CONFIG_FILE = "last_configs.json"
BM_NOTIFIED_FILE = "bookmark_notified.json"

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

def get_ist_now():
    return datetime.now(IST)

def get_ist_now_str():
    return get_ist_now().strftime("%Y-%m-%d %I:%M:%S %p")

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

def load_bm_notified():
    return load_json(BM_NOTIFIED_FILE, {})

def is_bm_notified(uid, phone) -> bool:
    return bool(load_bm_notified().get(f"{uid}_{phone}", False))

def mark_bm_notified(uid, phone):
    d = load_bm_notified()
    d[f"{uid}_{phone}"] = True
    save_json(BM_NOTIFIED_FILE, d)

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
    uid_str = str(user_id)
    return dict(all_acc_db.get(uid_str, {}))

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
            mark_bm_notified(uid, phone)
            await context.bot.set_my_description(description="", language_code="zu")
            await context.bot.send_message(
                chat_id=int(uid),
                text=f"✅ **Account `{phone}` ka Login Session Bookmark se Auto-Update Ho Gaya!**\n\nAb `/start` dabakar matching shuru karein.",
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
    
    txt = "👋 **AR Wallet Bot (Free Edition)**\n⚡ Limit: 1 Active Session Per User\n\nApna account chunein ya naya add karein:"
    if update.callback_query:
        await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    else:
        await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🛠 **Available Bot Commands:**\n\n"
        "• `/start` - Bot open aur account list dekhne ke liye.\n"
        "• `/detail` - Orders, Buy aur Commission report.\n"
        "• `/stop` - Running matching process ko stop karne ke liye."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

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
                    const txt = document.body.innerText || '';
                    if (txt.includes('To handle') || txt.includes('is offline')) {
                        const icons = Array.from(document.querySelectorAll('i, svg, [class*="close"], [class*="cross"]'));
                        for (let ic of icons) {
                            ic.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                            hit = true;
                        }
                    }
                    return hit;
                }
            """)
            body = await page.inner_text("body")
            if "Auto Wallet" in body or "Not on the app" in body or "Official APP" in body or "Close" in body:
                close_loc = page.locator('text="Close"')
                if await close_loc.count() > 0:
                    for i in range(await close_loc.count()):
                        box = await close_loc.nth(i).bounding_box()
                        if box and box["width"] > 20 and box["height"] < 90:
                            await page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                            await asyncio.sleep(0.4)
                await page.touchscreen.tap(195, 682)
                await asyncio.sleep(0.5)
            elif "is offline" in body or "To handle" in body:
                await page.touchscreen.tap(324, 262)
                await asyncio.sleep(0.5)
            elif not clicked:
                break
        except Exception:
            break

async def fetch_order_section_data(user_id_str, phone, session_data):
    if not session_data:
        return {"expired": True, "orders": []}
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
            ctx = await browser.new_context(viewport={"width": 390, "height": 844}, user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15", has_touch=True)
            page = await ctx.new_page()
            try:
                base_url = "https://clbsk.paywivo.com"
                try:
                    await page.goto(base_url, wait_until="commit", timeout=20000)
                except Exception:
                    base_url = "https://arbpay.ai"
                    await page.goto(base_url, wait_until="commit", timeout=25000)
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1.5)
                await page.evaluate(r"""
                    (storageMap) => {
                        for (let k in storageMap) {
                            const val = typeof storageMap[k] === 'object' ? JSON.stringify(storageMap[k]) : storageMap[k];
                            localStorage.setItem(k, val);
                        }
                    }
                """, session_data)
                await page.goto(f"{base_url}/#/order", wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(3.5)
                if await check_is_login_page(page):
                    await browser.close()
                    return {"expired": True, "orders": []}
                await dismiss_all_popups(page)
                await page.evaluate(r"""
                    () => {
                        const tabs = Array.from(document.querySelectorAll('*'));
                        for (let el of tabs) {
                            if ((el.innerText || '').trim() === 'My Purchases') {
                                el.click();
                                break;
                            }
                        }
                    }
                """)
                await asyncio.sleep(1.8)
                day_before_str = (get_ist_now() - timedelta(days=2)).strftime("%Y-%m-%d")
                collected_orders = {}
                pattern = re.compile(
                    r'(Completed|Complete)\s+Amount\s+₹\s*([0-9,]+(?:\.[0-9]{1,2})?)(?:(?!Order Number|Canceled).){1,260}?Time\s+([0-9]{4}-[0-9]{2}-[0-9]{2})\s+([0-9:]+)(?:(?!Order Number).){1,160}?Order Number\s+([A-Za-z0-9]+)',
                    re.DOTALL | re.IGNORECASE
                )
                old_hits = 0
                for _ in range(20):
                    body_txt = await page.inner_text("body")
                    oldest_seen = None
                    for match in pattern.finditer(body_txt):
                        _, amt_s, dt_s, _, ord_id = match.groups()
                        dt_clean = dt_s.strip()
                        oldest_seen = dt_clean
                        collected_orders[ord_id] = {"id": ord_id, "amount": float(amt_s.replace(",", "")), "date": dt_clean}
                    if oldest_seen and oldest_seen < day_before_str:
                        old_hits += 1
                        if old_hits >= 2:
                            break
                    await page.evaluate(r"""
                        () => {
                            window.scrollBy(0, 520);
                            const els = Array.from(document.querySelectorAll('*'));
                            for (let el of els) {
                                if (el.scrollHeight > el.clientHeight + 20) el.scrollTop += 520;
                            }
                        }
                    """)
                    await page.mouse.wheel(0, 520)
                    await asyncio.sleep(0.55)
                await browser.close()
                return {"expired": False, "orders": list(collected_orders.values())}
            except Exception:
                try:
                    await browser.close()
                except Exception:
                    pass
                return {"expired": False, "orders": []}
    except Exception:
        return {"expired": False, "orders": []}

async def detail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wait_msg = await update.message.reply_text("⏳ Syncing Short Report...")
    now = get_ist_now()
    today_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    day_before_str = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    user_id_str = str(user_id)
    user_accounts = get_user_accounts(user_id)
    if not user_accounts:
        await wait_msg.edit_text("ℹ️ Koi account nahi mila. `/start` dabakar `➕ Add Account` karein.", parse_mode="Markdown")
        return

    report = "📊 **Short Order Report (/detail)**\n────────────────────\n"
    expired_buttons = []
    idx = 1
    sum_t_cnt = sum_y_cnt = sum_db_cnt = 0
    sum_t_amt = sum_y_amt = sum_db_amt = 0.0
    sum_t_comm = sum_y_comm = sum_db_comm = 0.0

    for phone, session_data in list(user_accounts.items()):
        res = await fetch_order_section_data(user_id_str, phone, session_data)
        if res.get("expired"):
            report += f"⚠️ **`{phone}`**: Login Expired!\n────────────────────\n"
            expired_buttons.append([InlineKeyboardButton(f"🔄 Update ({phone})", callback_data=f"update_sess_{phone}")])
            continue
        t_cnt = y_cnt = db_cnt = 0
        t_amt = y_amt = db_amt = 0.0
        t_comm = y_comm = db_comm = 0.0
        for o in res.get("orders", []):
            amt, dt = o["amount"], o["date"]
            c_val = calculate_commission(amt)
            if dt == today_str:
                t_cnt += 1; t_amt += amt; t_comm += c_val
            elif dt == yesterday_str:
                y_cnt += 1; y_amt += amt; y_comm += c_val
            elif dt == day_before_str:
                db_cnt += 1; db_amt += amt; db_comm += c_val

        a_cnt = t_cnt + y_cnt + db_cnt
        a_amt = t_amt + y_amt + db_amt
        a_comm = t_comm + y_comm + db_comm

        sum_t_cnt += t_cnt; sum_y_cnt += y_cnt; sum_db_cnt += db_cnt
        sum_t_amt += t_amt; sum_y_amt += y_amt; sum_db_amt += db_amt
        sum_t_comm += t_comm; sum_y_comm += y_comm; sum_db_comm += db_comm

        report += (
            f"📱 **Account #{idx}: `{phone}`**\n"
            f"• **Today:** Order {t_cnt} | Buy ₹{fmt_num(t_amt)} | Commission ₹{fmt_num(t_comm)}\n"
            f"• **Yesterday:** Order {y_cnt} | Buy ₹{fmt_num(y_amt)} | Commission ₹{fmt_num(y_comm)}\n"
            f"• **Day Before:** Order {db_cnt} | Buy ₹{fmt_num(db_amt)} | Commission ₹{fmt_num(db_comm)}\n"
            f"∑ **3-Days Total:** Order {a_cnt} | Buy ₹{fmt_num(a_amt)} | **Commission ₹{fmt_num(a_comm)}**\n"
            f"────────────────────\n"
        )
        idx += 1

    if len(user_accounts) > 1:
        g_cnt = sum_t_cnt + sum_y_cnt + sum_db_cnt
        g_amt = sum_t_amt + sum_y_amt + sum_db_amt
        g_comm = sum_t_comm + sum_y_comm + sum_db_comm
        report += (
            f"📊 **KUL TOTAL:**\n"
            f"• **Today:** Order {sum_t_cnt} | Buy ₹{fmt_num(sum_t_amt)} | Commission ₹{fmt_num(sum_t_comm)}\n"
            f"• **Yesterday:** Order {sum_y_cnt} | Buy ₹{fmt_num(sum_y_amt)} | Commission ₹{fmt_num(sum_y_comm)}\n"
            f"• **Day Before:** Order {sum_db_cnt} | Buy ₹{fmt_num(sum_db_amt)} | Commission ₹{fmt_num(sum_db_comm)}\n"
            f"🏆 **3-Days Grand Total:** Order {g_cnt} | Buy ₹{fmt_num(g_amt)} | **Commission ₹{fmt_num(g_comm)}**"
        )

    markup = InlineKeyboardMarkup(expired_buttons) if expired_buttons else None
    try:
        await wait_msg.edit_text(report, reply_markup=markup, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(report, reply_markup=markup, parse_mode="Markdown")

async def add_acc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("👉 Apna **10-digit Phone Number** bhejein:", parse_mode="Markdown")
    return ASK_PHONE

async def update_expired_session_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    phone = query.data.replace("update_sess_", "")
    chat_id = update.effective_chat.id
    s_key = f"{chat_id}_{phone}"
    if s_key in user_sessions:
        await stop_single_session(s_key, context, notify=False)
    context.user_data['temp_phone'] = phone
    bm = generate_user_bookmarklet(chat_id, phone)
    prompt_txt = (
        f"📱 **Account: `{phone}`**\n\n"
        f"🔖 **Auto-Login Bookmark (`{phone}`):**\n`{bm}`\n\n"
        f"👉 Upar diya bookmark portal par login karke tap karein ya session JSON file bhejein:"
    )
    try:
        if query.message and query.message.photo:
            await query.edit_message_caption(caption=prompt_txt, reply_markup=None, parse_mode="Markdown")
        else:
            await query.edit_message_text(text=prompt_txt, reply_markup=None, parse_mode="Markdown")
    except Exception:
        await query.message.reply_text(prompt_txt, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    return ASK_SESSION

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.isdigit() or len(phone) != 10:
        await update.message.reply_text("❌ Sahi 10-digit number dalein:", reply_markup=ReplyKeyboardRemove())
        return ASK_PHONE
    chat_id = str(update.effective_chat.id)
    context.user_data['temp_phone'] = phone
    accounts = load_accounts()
    if chat_id not in accounts or not isinstance(accounts[chat_id], dict):
        accounts[chat_id] = {}
    if phone not in accounts[chat_id]:
        accounts[chat_id][phone] = {}
        save_accounts(accounts)
    mark_bm_notified(chat_id, phone)
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
    accounts = load_accounts()
    if chat_id not in accounts:
        accounts[chat_id] = {}
    accounts[chat_id][phone] = parsed_json
    save_accounts(accounts)
    await update.message.reply_text(f"🎉 **Account `{phone}` Save Ho Gaya!**\nAb chalane ke liye `/start` dabayein.", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    return ConversationHandler.END

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Process cancel ho gaya. /start karein.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await stop_all_user_sessions(update.effective_chat.id, context)

async def stop_single_session(session_key, context, notify=True):
    if session_key in user_sessions:
        s = user_sessions[session_key]
        s["running"] = False
        try:
            if "browser" in s:
                await s["browser"].close()
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
    buttons = [[InlineKeyboardButton("🔁 Repeat Order", callback_data=f"orderrepeat_{phone}")]]
    await context.bot.send_photo(
        chat_id,
        photo=photo,
        caption=(
            f"🚨🚨 **ORDER MATCH HO GAYA!** 🔔\n\n"
            f"📱 **Account: `{phone}`**\n"
            f"💰 **Amount: ₹ {amt}**\n"
            f"💵 **Commission: +₹ {comm}**\n"
            f"⚡ Countdown timer shuru ho gaya hai!\n"
            f"Turant payment complete karein!"
        ),
        reply_markup=InlineKeyboardMarkup(buttons),
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
                res_c = await page.evaluate(r"""
                    () => {
                        const all = Array.from(document.querySelectorAll('button, div, span, a, p'));
                        for (let el of all.reverse()) {
                            const t = (el.innerText || '').trim();
                            if (t.includes('Cancel Matching') && el.offsetParent !== null) {
                                const r = el.getBoundingClientRect();
                                if (r.width > 20 && r.height > 10 && r.height < 100) {
                                    el.click();
                                    return { ok: true, x: r.left + r.width/2, y: r.top + r.height/2 };
                                }
                            }
                        }
                        return { ok: false };
                    }
                """)
                if res_c.get("ok"):
                    await page.touchscreen.tap(res_c["x"], res_c["y"])
                await asyncio.sleep(1.5)
            body = await page.inner_text("body")
            if "Back to Order List" in body or "No match found" in body:
                res_b = await page.evaluate(r"""
                    () => {
                        const all = Array.from(document.querySelectorAll('button, div, span, a, p'));
                        for (let el of all.reverse()) {
                            const t = (el.innerText || '').trim();
                            if (t.includes('Back to Order List') && el.offsetParent !== null) {
                                const r = el.getBoundingClientRect();
                                if (r.width > 20 && r.height > 10 && r.height < 100) {
                                    el.click();
                                    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                                    return { ok: true, x: r.left + r.width/2, y: r.top + r.height/2 };
                                }
                            }
                        }
                        return { ok: false };
                    }
                """)
                if res_b.get("ok"):
                    await page.touchscreen.tap(res_b["x"], res_b["y"])
                else:
                    await page.touchscreen.tap(195, 718)
                await asyncio.sleep(2)
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
                box = await buy_arb.bounding_box()
                if box:
                    await page.touchscreen.tap(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                else:
                    await buy_arb.click(force=True, timeout=2000)
            except Exception:
                await page.touchscreen.tap(110, 600)
            await asyncio.sleep(2.5)
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
    await asyncio.sleep(1.2)
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
        await asyncio.sleep(0.6)
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
                                let clickable = c;
                                for (let depth = 0; depth < 3; depth++) {
                                    if (clickable && clickable.offsetHeight >= 35 && clickable.offsetWidth >= 150) {
                                        clickable.click();
                                        return true;
                                    }
                                    if (clickable.parentElement) clickable = clickable.parentElement;
                                }
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
        await asyncio.sleep(0.8)
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
        await asyncio.sleep(0.5)
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
                        el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                        const r = el.getBoundingClientRect();
                        return { found: true, x: r.left + r.width/2, y: r.top + r.height/2 };
                    }
                }
                return { found: false };
            }
        """, clean_target)
        if res.get("found"):
            tx, ty = res.get("x"), res.get("y")
            if tx and ty:
                await page.touchscreen.tap(tx, ty)
            await asyncio.sleep(1.2)
            return True
        await page.mouse.move(340, 395)
        await page.mouse.down()
        await page.mouse.move(30, 395, steps=10)
        await page.mouse.up()
        await asyncio.sleep(0.8)
    return False

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    await query.answer()

    if data.startswith("orderrepeat_"):
        phone = data.replace("orderrepeat_", "")
        s_key = f"{chat_id}_{phone}"
        await query.answer("🔁 Repeat Order Matching Start Ho Raha Hai...")
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
            await query.edit_message_text(f"⚠️ **`{phone}` ka Login Session Abhi Add Nahi Hai!**\n\nPortal login karke apna `{phone}` Bookmark run karein:\n\n`{bm}`", parse_mode="Markdown")
            return
        active_keys = get_active_sessions_for_user(chat_id)
        if len(active_keys) >= 1 and s_key not in active_keys:
            await stop_single_session(active_keys[0], context, notify=False)
        if s_key in user_sessions:
            await stop_single_session(s_key, context, notify=False)
        confs = load_last_configs().get(phone, {})
        p_tab, p_range, p_app = confs.get("tab", "OTP-UPI"), confs.get("range", "2001-2999"), confs.get("app", "PhonePe")
        user_tab_selection[s_key], user_range_selection[s_key], user_pay_selection[s_key] = p_tab, p_range, p_app
        await query.edit_message_text(f"🚀 **Fast Repeat Matching!**\n📱 `{phone}`\n📂 [{p_tab}] {p_range} ({p_app})\n\nStarting browser...", parse_mode="Markdown")
        asyncio.create_task(run_direct_session(chat_id, phone, session_data, context, auto_start_matching=True))
        return

    if data.startswith("run_"):
        phone = data.split("_")[1]
        s_key = f"{chat_id}_{phone}"
        accounts = get_user_accounts(user_id)
        session_data = accounts.get(phone)
        if not session_data:
            bm = generate_user_bookmarklet(chat_id, phone)
            await query.edit_message_text(f"⚠️ **`{phone}` ka Login Session Abhi Add Nahi Hai!**\n\nPortal login karke apna `{phone}` Bookmark run karein:\n\n`{bm}`", parse_mode="Markdown")
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
        await query.edit_message_text(f"⏳ **`{phone}`**: Live payment options scan ho rahe hain...", parse_mode="Markdown")
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
        await asyncio.sleep(2.5)
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
        await query.edit_message_text(f"✅ **`{phone}`**\n📂 Tab: {cur_tab} | 💰 Range: {range_val}\n\n3️⃣ Live Payment Option chunein:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

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
        await query.edit_message_text(f"⏳ **`{phone}`** ({app_name}): Matching start ki ja rahi hai...", parse_mode="Markdown")
        page = user_sessions[s_key]["page"]
        try:
            await click_strictly_another_account(page, app_name)
            await asyncio.sleep(3)
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

async def start_matching_procedure(s_key, context):
    session = user_sessions.get(s_key)
    if not session:
        return
    page, phone, chat_id = session["page"], session["phone"], session["chat_id"]
    target_range = user_range_selection.get(s_key, "2001-2999")
    target_tab = user_tab_selection.get(s_key, "OTP-UPI")
    app_name = user_pay_selection.get(s_key, "PhonePe")
    try:
        if await check_is_login_page(page):
            await send_login_expired_alert(chat_id, phone, s_key, page, context)
            return
        await ensure_on_buy_page(page)
        await switch_tab(page, target_tab)
        await asyncio.sleep(1.2)
        if await check_is_login_page(page):
            await send_login_expired_alert(chat_id, phone, s_key, page, context)
            return
        await select_and_strictly_verify_tag(page, target_range)
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
        await asyncio.sleep(2.5)
        if await check_is_login_page(page):
            await send_login_expired_alert(chat_id, phone, s_key, page, context)
            return
        await click_strictly_another_account(page, app_name)
        await asyncio.sleep(3)
        if await check_is_login_page(page):
            await send_login_expired_alert(chat_id, phone, s_key, page, context)
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
                await asyncio.sleep(4)
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
                    await start_matching_procedure(s_key, context)
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
                await asyncio.sleep(3.5)
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
                await asyncio.sleep(16)
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
    app.add_handler(CommandHandler("detail", detail_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("✅ AR Wallet Free Bot Running with new token...")
    app.run_polling()

if __name__ == "__main__":
    main()
