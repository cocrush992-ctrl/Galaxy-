import os
import re
import logging
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from html import escape

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from database import init_db, db, get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("galaxy")

BOT_TOKEN = os.environ["BOT_TOKEN"]
GROUP_ID = int(os.getenv("GROUP_ID", "-1002376244934"))
OWNER_ID = int(os.environ["OWNER_ID"])
SERVICE_NAME = os.getenv("SERVICE_NAME", "GALAXY SECURITY SERVICE")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///galaxy.db")
ADMIN_ALERT_CHAT_ID = int(os.getenv("ADMIN_ALERT_CHAT_ID", str(GROUP_ID)))

MEMBER_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("📊 My Stats"), KeyboardButton("⏳ My Pending Deals")],
     [KeyboardButton("📜 My Deal History"), KeyboardButton("🏆 Leaderboard")]],
    resize_keyboard=True
)
ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("📂 My Holds"), KeyboardButton("📈 My Statistics")],
     [KeyboardButton("⏳ My Ongoing Deals"), KeyboardButton("👥 Admins Stats")],
     [KeyboardButton("📜 My Deal History"), KeyboardButton("🏆 Member Leaderboard")]],
    resize_keyboard=True
)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def money(value):
    return f"₹{Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"

def fee_for(amount: Decimal) -> Decimal:
    if amount < 0:
        raise ValueError("Amount cannot be negative")
    if amount <= 100:
        fee = Decimal("5")
    elif amount <= 300:
        fee = Decimal("10")
    elif amount <= 500:
        fee = Decimal("20")
    elif amount <= 1000:
        fee = Decimal("30")
    elif amount <= 2000:
        fee = Decimal("50")
    elif amount <= 5000:
        fee = (amount * Decimal("0.035")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        fee = (amount * Decimal("0.03")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return fee

def parse_form(text):
    # Supports the supplied labeled form; keep parsing strict to avoid silent wrong deals.
    labels = {
        "amount": r"DEAL\s*AMOUNT\s*:\s*(.+)",
        "buyer": r"BUYER\s*:\s*(.+)",
        "seller": r"SELLER\s*:\s*(.+)",
        "info": r"DEAL\s*INFO\s*:\s*(.+)",
        "until": r"ESCROW\s*TILL\s*:\s*(.+)",
        "method": r"PAYMENT\s*METHOD\s*:\s*(.+)",
    }
    normalized = text.replace("𝐃𝐄𝐀𝐋 𝐀𝐌𝐎𝐔𝐍𝐓", "DEAL AMOUNT").replace("𝐁𝐔𝐘𝐄𝐑", "BUYER").replace("𝐒𝐄𝐋𝐋𝐄𝐑", "SELLER").replace("𝐃𝐄𝐀𝐋 𝐈𝐍𝐅𝐎", "DEAL INFO").replace("𝐄𝐒𝐂𝐑𝐎𝐖 𝐓𝐈𝐋𝐋", "ESCROW TILL").replace("𝐏𝐀𝐘𝐌𝐄𝐍𝐓 𝐌𝐄𝐓𝐇𝐎𝐃", "PAYMENT METHOD")
    result = {}
    for key, pattern in labels.items():
        m = re.search(pattern, normalized, re.I)
        if not m:
            raise ValueError(f"Missing form field: {key}")
        result[key] = m.group(1).strip()
        if not result[key] or result[key] == "-":
            raise ValueError(f"Empty form field: {key}")
    amount_text = re.sub(r"[₹,\s]", "", result["amount"])
    amount = Decimal(amount_text)
    if amount <= 0:
        raise ValueError("Deal amount must be greater than zero")
    result["amount"] = amount
    return result

async def is_group_admin(context, user_id):
    try:
        member = await context.bot.get_chat_member(GROUP_ID, user_id)
        return member.status in ("administrator", "creator")
    except TelegramError:
        return False

async def is_authorized_admin(update, context):
    user = update.effective_user
    if not user:
        return False
    return user.id == OWNER_ID or await is_group_admin(context, user.id)

async def silent_delete(update, context):
    # User requested unauthorized command messages be deleted without a bot reply.
    try:
        if update.effective_message:
            await update.effective_message.delete()
    except TelegramError:
        pass

async def admin_only(update, context):
    if not await is_authorized_admin(update, context):
        await silent_delete(update, context)
        return False
    return True

def user_label(user):
    if user.username:
        return f"@{escape(user.username)}"
    return escape(user.full_name or str(user.id))

def deal_card(deal, completed=False):
    status = "✅ <b>DEAL COMPLETED</b>" if completed else "🛡️ <b>GALAXY ESCROW DEAL</b>"
    fee_label = "0%" if deal["fee_mode"] == "zero" else f"{deal['fee_percent']}%"
    buyer_name = escape(deal["buyer_name"] or "Unknown")
    seller_name = escape(deal["seller_name"] or "Unknown")
    buyer_username = escape(deal["buyer_username"] or "not set")
    seller_username = escape(deal["seller_username"] or "not set")
    return (
        f"{status}\n\n"
        f"💰 <b>Deal Amount:</b> <code>{money(deal['amount'])}</code>\n"
        f"💸 <b>Escrow Fee:</b> <code>{money(deal['fee'])}</code> ({fee_label})\n"
        f"📤 <b>Net Release:</b> <code>{money(deal['release_amount'])}</code>\n"
        f"🆔 <b>Trade ID:</b> <code>{escape(deal['trade_id'])}</code>\n\n"
        f"👤 <b>Buyer:</b> {buyer_username} · UID <code>{deal['buyer_id'] or 'pending'}</code>\n"
        f"👤 <b>Seller:</b> {seller_username} · UID <code>{deal['seller_id'] or 'pending'}</code>\n"
        f"📝 <b>Deal Info:</b> {escape(deal['info'])}\n"
        f"⏰ <b>Escrow Till:</b> {escape(deal['until_text'])}\n"
        f"💳 <b>Payment Method:</b> {escape(deal['method'])}\n"
        f"🛡️ <b>Escrowed By:</b> {escape(deal['escrowed_by_username'] or str(deal['escrowed_by_id']))}\n"
        f"📌 <b>Status:</b> {escape(deal['status'].upper())}\n\n"
        f"✨ <i>Safe • Transparent • Accountable</i>"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    db("INSERT OR IGNORE INTO users(user_id, username, full_name, first_seen) VALUES(?,?,?,?)",
       (user.id, user.username, user.full_name, now_iso()))
    db("UPDATE users SET username=?, full_name=? WHERE user_id=?", (user.username, user.full_name, user.id))
    is_admin = user.id == OWNER_ID or await is_group_admin(context, user.id)
    msg = f"🙏 Welcome, {user_label(user)}!\nThis is your GALAXY deal dashboard."
    await update.effective_message.reply_text(msg, reply_markup=ADMIN_KEYBOARD if is_admin else MEMBER_KEYBOARD)

async def help_cmd(update, context):
    await update.effective_message.reply_text(
        "Use /start to open your dashboard.\nAdmins: reply to a complete deal form with /add or /add amount.\n"
        "Zero-fee: reply to a form with /add amount no fees.\n"
        "Close: /close TRADE-ID and attach one or more payment proof images in the same message or as a reply.\n"
        "Important: screenshots are evidence, not independent payment verification."
    )

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    msg = update.effective_message
    zero_fee = any(x.lower() in ("no", "fees", "fee", "nofees") for x in context.args[1:]) or "no fees" in msg.text.lower()
    amount_arg = None
    if context.args:
        try:
            amount_arg = Decimal(re.sub(r"[₹,\s]", "", context.args[0]))
        except Exception:
            amount_arg = None
    source = msg.reply_to_message
    form_text = source.text or source.caption if source else None
    if not form_text:
        await msg.reply_text("Reply to the full deal form with /add or /add amount [no fees].")
        return
    try:
        form = parse_form(form_text)
        amount = amount_arg if amount_arg is not None else form["amount"]
        if amount <= 0:
            raise ValueError("Amount must be greater than zero.")
    except Exception as e:
        await msg.reply_text(f"❌ Form could not be validated: {escape(str(e))}")
        return
    fee = Decimal("0") if zero_fee else fee_for(amount)
    release = amount - fee
    if release < 0:
        await msg.reply_text("❌ Fee exceeds the deal amount; deal not created.")
        return
    trade_id = f"GS-{datetime.now(timezone.utc):%y%m%d}-{os.urandom(3).hex().upper()}"
    fee_percent = (fee / amount * 100).quantize(Decimal("0.01")) if amount else Decimal("0")
    db("""INSERT INTO deals(trade_id, amount, fee, release_amount, fee_percent, fee_mode, buyer_name, seller_name,
       buyer_username, seller_username, buyer_id, seller_id, info, until_text, method, escrowed_by_id,
       escrowed_by_username, status, created_at, group_id)
       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
       (trade_id, str(amount), str(fee), str(release), str(fee_percent), "zero" if zero_fee else "standard",
        form["buyer"], form["seller"], None, None, None, None, form["info"], form["until"], form["method"],
        update.effective_user.id, update.effective_user.username, "ongoing", now_iso(), GROUP_ID))
    deal = db("SELECT * FROM deals WHERE trade_id=?", (trade_id,), one=True)
    sent = await context.bot.send_message(GROUP_ID, deal_card(deal), parse_mode=ParseMode.HTML)
    db("UPDATE deals SET card_message_id=? WHERE trade_id=?", (sent.message_id, trade_id))
    db("INSERT INTO audit_log(actor_id, action, trade_id, details, created_at) VALUES(?,?,?,?,?)",
       (update.effective_user.id, "CREATE_DEAL", trade_id, "Deal card created; payment not independently verified.", now_iso()))
    await msg.reply_text(f"✅ Deal card created.\nTrade ID: {trade_id}\nFee: {money(fee)}\nNet release: {money(release)}")

async def close_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    msg = update.effective_message
    if not context.args:
        await msg.reply_text("Usage: reply to payment proof photo(s) with /close TRADE-ID, or send /close TRADE-ID with a proof photo.")
        return
    trade_id = context.args[0].strip().upper()
    deal = db("SELECT * FROM deals WHERE trade_id=?", (trade_id,), one=True)
    if not deal:
        await msg.reply_text("❌ Trade ID not found.")
        return
    if deal["status"] != "ongoing":
        await msg.reply_text("❌ This deal is not ongoing.")
        return
    photos = []
    if msg.photo:
        photos.append(msg.photo[-1].file_id)
    if msg.reply_to_message and msg.reply_to_message.photo:
        photos.append(msg.reply_to_message.photo[-1].file_id)
    if msg.reply_to_message and msg.reply_to_message.document and (msg.reply_to_message.document.mime_type or "").startswith("image/"):
        photos.append(msg.reply_to_message.document.file_id)
    if not photos:
        await msg.reply_text("❌ Payment proof is mandatory. Attach at least one image or reply to an image with /close TRADE-ID.")
        return
    for file_id in photos:
        db("INSERT INTO evidence(trade_id, file_id, submitted_by, created_at) VALUES(?,?,?,?)",
           (trade_id, file_id, update.effective_user.id, now_iso()))
    db("UPDATE deals SET status='completed', closed_at=?, closed_by_id=? WHERE trade_id=?",
       (now_iso(), update.effective_user.id, trade_id))
    deal = db("SELECT * FROM deals WHERE trade_id=?", (trade_id,), one=True)
    await context.bot.send_message(GROUP_ID, deal_card(deal, completed=True), parse_mode=ParseMode.HTML)
    # Vouch explicitly uses original deal amount, not release amount.
    vouch = f"Vouch {trade_id} for {money(deal['amount'])} smooth escrow deal"
    await context.bot.send_message(GROUP_ID, f"<code>{escape(vouch)}</code>", parse_mode=ParseMode.HTML)
    db("INSERT INTO audit_log(actor_id, action, trade_id, details, created_at) VALUES(?,?,?,?,?)",
       (update.effective_user.id, "CLOSE_DEAL", trade_id, f"Proof file count: {len(photos)}. Human review/payment-provider confirmation still required.", now_iso()))
    await msg.reply_text("⚠️ Deal marked completed in the bot. Screenshot evidence is stored, but this does not itself verify that funds arrived or were released.")

async def hold_cmd(update, context):
    if not await admin_only(update, context):
        return
    rows = db("SELECT escrowed_by_id, escrowed_by_username, COUNT(*) AS n, SUM(CAST(amount AS REAL)) AS total FROM deals WHERE status='ongoing' GROUP BY escrowed_by_id, escrowed_by_username ORDER BY total DESC")
    if not rows:
        await update.effective_message.reply_text("📂 No ongoing holds.")
        return
    lines = ["📂 <b>GALAXY — CURRENT HOLDS</b>"]
    for r in rows:
        lines.append(f"\n👤 @{escape(r['escrowed_by_username'] or str(r['escrowed_by_id']))} · UID <code>{r['escrowed_by_id']}</code>\n   Deals: <b>{r['n']}</b> | Amount: <code>{money(r['total'] or 0)}</code>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def myholds_cmd(update, context):
    if not await admin_only(update, context):
        return
    rows = db("SELECT * FROM deals WHERE escrowed_by_id=? AND status='ongoing' ORDER BY created_at DESC", (update.effective_user.id,))
    await send_deal_list(update, context, rows, "📂 MY HOLDS")

async def ongoing_cmd(update, context):
    if not await admin_only(update, context):
        return
    rows = db("SELECT * FROM deals WHERE escrowed_by_id=? AND status='ongoing' ORDER BY created_at DESC", (update.effective_user.id,))
    await send_deal_list(update, context, rows, "⏳ MY ONGOING DEALS")

async def history_cmd(update, context):
    rows = db("SELECT * FROM deals WHERE buyer_id=? OR seller_id=? ORDER BY created_at DESC LIMIT 30",
              (update.effective_user.id, update.effective_user.id))
    await send_deal_list(update, context, rows, "📜 MY DEAL HISTORY")

async def send_deal_list(update, context, rows, title):
    if not rows:
        await update.effective_message.reply_text(f"{title}\n\nNo deals found.")
        return
    for row in rows[:30]:
        await update.effective_message.reply_text(deal_card(row, row["status"] == "completed"), parse_mode=ParseMode.HTML)

async def stats_for_user(update, context):
    user = update.effective_user
    stats = db("""SELECT COUNT(*) AS total, SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
      SUM(CASE WHEN status='ongoing' THEN 1 ELSE 0 END) AS ongoing,
      SUM(CASE WHEN status='completed' THEN CAST(amount AS REAL) ELSE 0 END) AS volume,
      MAX(CASE WHEN status='completed' THEN CAST(amount AS REAL) ELSE 0 END) AS highest
      FROM deals WHERE buyer_id=? OR seller_id=?""", (user.id, user.id), one=True)
    volume = stats["volume"] or 0
    highest = stats["highest"] or 0
    await update.effective_message.reply_text(
        f"📊 <b>MY STATS</b>\n\n🏆 Completed rank: calculated from recorded deals\n"
        f"💰 Total Volume: <code>{money(volume)}</code>\n🤝 Total Deals: <b>{stats['completed'] or 0}</b>\n"
        f"⏳ Ongoing Deals: <b>{stats['ongoing'] or 0}</b>\n📈 Highest Deal: <code>{money(highest)}</code>",
        parse_mode=ParseMode.HTML)

async def admin_stats(update, context):
    if not await admin_only(update, context):
        return
    admins = []
    try:
        admins = await context.bot.get_chat_administrators(GROUP_ID)
    except TelegramError:
        await update.effective_message.reply_text("Could not read group admins. Check bot access.")
        return
    rows = []
    for member in admins:
        uid = member.user.id
        s = db("SELECT COUNT(*) AS completed, SUM(CAST(amount AS REAL)) AS volume FROM deals WHERE escrowed_by_id=? AND status='completed'", (uid,), one=True)
        rows.append((uid, member.user.username, s["completed"] or 0, s["volume"] or 0))
    rows.sort(key=lambda x: (-x[2], -x[3], x[0]))
    lines = ["👥 <b>GALAXY ADMIN PERFORMANCE</b>"]
    for i, (uid, username, count, volume) in enumerate(rows, 1):
        lines.append(f"\n<b>#{i}</b> @{escape(username or str(uid))} · UID <code>{uid}</code>\nDeals: <b>{count}</b> | Volume: <code>{money(volume)}</code>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def mystatic_cmd(update, context):
    if not await admin_only(update, context):
        return
    uid = update.effective_user.id
    all_admins = await context.bot.get_chat_administrators(GROUP_ID)
    ranked = []
    for member in all_admins:
        s = db("SELECT COUNT(*) AS completed, SUM(CASE WHEN status='ongoing' THEN 1 ELSE 0 END) AS ongoing FROM deals WHERE escrowed_by_id=?", (member.user.id,), one=True)
        ranked.append((member.user.id, s["completed"] or 0, s["ongoing"] or 0))
    ranked.sort(key=lambda x: (-x[1], x[0]))
    rank = next((i for i, item in enumerate(ranked, 1) if item[0] == uid), None)
    own = next((x for x in ranked if x[0] == uid), (uid, 0, 0))
    await update.effective_message.reply_text(
        f"📈 <b>ADMIN STATISTICS</b>\n\n🏆 Admin Rank: <b>#{rank or '—'}</b>\n"
        f"✅ Completed Deals: <b>{own[1]}</b>\n⏳ Ongoing Deals: <b>{own[2]}</b>",
        parse_mode=ParseMode.HTML)

async def leaderboard(update, context):
    rows = db("""SELECT u.user_id, u.username, COUNT(d.trade_id) AS completed,
      SUM(CAST(d.amount AS REAL)) AS volume FROM users u JOIN deals d ON
      (d.buyer_id=u.user_id OR d.seller_id=u.user_id) WHERE d.status='completed'
      GROUP BY u.user_id, u.username ORDER BY completed DESC, volume DESC LIMIT 10""")
    if not rows:
        await update.effective_message.reply_text("🏆 Leaderboard will appear when verified deal records exist.")
        return
    lines = ["🏆 <b>TOP 10 MEMBER LEADERBOARD</b>"]
    for i, r in enumerate(rows, 1):
        lines.append(f"\n<b>#{i}</b> @{escape(r['username'] or str(r['user_id']))}\nDeals: <b>{r['completed']}</b> · Volume: <code>{money(r['volume'] or 0)}</code>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def reboot_cmd(update, context):
    if update.effective_user.id != OWNER_ID:
        await silent_delete(update, context)
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /reboot USER_ID or @username")
        return
    target = context.args[0].lstrip("@")
    user = db("SELECT user_id, username FROM users WHERE CAST(user_id AS TEXT)=? OR lower(username)=lower(?)",
              (target, target), one=True)
    if not user:
        await update.effective_message.reply_text("User not found in the bot database.")
        return
    # Safe reset is intentionally limited to non-financial profile/test state.
    db("DELETE FROM user_sessions WHERE user_id=?", (user["user_id"],))
    db("INSERT INTO audit_log(actor_id, action, details, created_at) VALUES(?,?,?,?)",
       (update.effective_user.id, "REBOOT_USER_SESSION", f"Reset session for user {user['user_id']}; deal history retained.", now_iso()))
    await update.effective_message.reply_text(f"♻️ Session reset for UID {user['user_id']}. Deal and financial audit records were retained.")

async def admin_list(update, context):
    try:
        admins = await context.bot.get_chat_administrators(GROUP_ID)
    except TelegramError:
        await update.effective_message.reply_text("Admin list unavailable.")
        return
    lines = ["👑 <b>GALAXY ADMIN LIST</b>"]
    for m in admins:
        lines.append(f"• @{escape(m.user.username or m.user.full_name)} · UID <code>{m.user.id}</code>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def text_router(update, context):
    text = (update.effective_message.text or "").strip()
    mapping = {
        "📊 My Stats": stats_for_user, "⏳ My Pending Deals": pending_member,
        "📜 My Deal History": history_cmd, "🏆 Leaderboard": leaderboard,
        "📂 My Holds": myholds_cmd, "📈 My Statistics": mystatic_cmd,
        "⏳ My Ongoing D
