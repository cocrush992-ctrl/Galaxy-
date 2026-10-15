import os
import re
import sqlite3
import logging
from flask import Flask
from threading import Thread
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

# ----------------------------------------------------
# 🌐 FLASK KEEP-ALIVE SERVER (FOR 24/7 RENDER/RAILWAY HOSTING)
# ----------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Galaxy Escrow Bot is Live 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

Thread(target=run_flask, daemon=True).start()

# ----------------------------------------------------
# CONFIGURATION & CONSTANTS
# ----------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "123456789"))
VERIFIED_GROUP_ID = -1002376244934

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# ----------------------------------------------------
# DATABASE INITIALIZATION
# ----------------------------------------------------
def init_db():
    conn = sqlite3.connect("galaxy_escrow.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS deals (
            trade_id TEXT PRIMARY KEY,
            message_id INTEGER,
            chat_id INTEGER,
            buyer TEXT,
            buyer_username TEXT,
            seller TEXT,
            seller_username TEXT,
            deal_info TEXT,
            amount REAL,
            fee REAL,
            net_release REAL,
            admin_id INTEGER,
            admin_username TEXT,
            status TEXT DEFAULT 'ACTIVE',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_stats (
            admin_id INTEGER PRIMARY KEY,
            admin_username TEXT,
            total_deals INTEGER DEFAULT 0,
            total_volume REAL DEFAULT 0.0,
            current_holdings REAL DEFAULT 0.0
        )
    """)
    
    conn.commit()
    conn.close()

init_db()

# ----------------------------------------------------
# STRICT 4-BUTTON KEYBOARDS
# ----------------------------------------------------
def get_user_keyboard():
    keyboard = [
        [KeyboardButton("📊 My Stats"), KeyboardButton("⏳ My Pending Deals")],
        [KeyboardButton("📜 My Deal History"), KeyboardButton("🏆 Top 10")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_keyboard():
    keyboard = [
        [KeyboardButton("💰 My Holdings"), KeyboardButton("⏳ Pending Deals")],
        [KeyboardButton("📜 Completed Deals"), KeyboardButton("📊 Admins Static")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ----------------------------------------------------
# HELPER FUNCTIONS & FEE LOGIC
# ----------------------------------------------------
def calculate_fee(amount: float, is_no_fee: bool = False) -> tuple[float, float]:
    if is_no_fee:
        return 0.0, amount
    
    if amount <= 100:
        fee = 5.0
    elif amount <= 500:
        fee = 15.0
    elif amount <= 1000:
        fee = 30.0
    elif amount <= 5000:
        fee = 100.0
    else:
        fee = amount * 0.035
        
    net_release = amount - fee
    return round(fee, 2), round(net_release, 2)

def generate_trade_id() -> str:
    import random
    return f"GLX-{random.randint(1000, 9999)}"

async def in_verified_group_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if user_id == BOT_OWNER_ID:
        return True
    try:
        member = await context.bot.get_chat_member(VERIFIED_GROUP_ID, user_id)
        return member.status in ["administrator", "creator"]
    except Exception:
        return False

# ----------------------------------------------------
# CARD FORMATTERS
# ----------------------------------------------------
def format_deal_card(deal_data: dict) -> str:
    return (
        f"🔻 <b>Deal Amount:</b> ₹{deal_data['amount']:,.2f}\n"
        f"📤 <b>Fee:</b> 3.5% — ₹{deal_data['fee']:,.2f}\n"
        f"📤 <b>Net Release:</b> ₹{deal_data['net_release']:,.2f}\n"
        f"🪪 <b>Trade ID:</b> {deal_data['trade_id']}\n\n"
        f"👤 <b>Buyer:</b> {deal_data['buyer']}\n"
        f"👤 <b>Seller:</b> {deal_data['seller']}\n"
        f"✏️ <b>Detail:</b> {deal_data['deal_info']}\n"
        f"⏱️ <b>Escrow Till:</b> 1 Hour\n\n"
        f"🛡️ <b>Escrowed By:</b> @{deal_data['admin_username']}"
    )

def format_close_card(trade_id: str, released_amount: float, admin_username: str, buyer_uname: str, seller_uname: str, original_amount: float) -> str:
    return (
        f"✅ <b>Deal Completed</b>\n"
        f"🪪 <b>Trade ID:</b> {trade_id}\n"
        f"📤 <b>Released:</b> ₹{released_amount:,.2f}\n"
        f"🛡️ <b>Escrowed By:</b> @{admin_username}\n\n"
        f"~ {buyer_uname} and {seller_uname} are requested to drop the vouch before leaving 👆\n\n"
        f"Vouch @GalaxyEscrowService for ₹{original_amount:,.2f} smooth escrow deal"
    )

# ----------------------------------------------------
# COMMAND HANDLERS
# ----------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if chat.type != "private":
        await update.message.reply_text("<b>⚡ Galaxy Escrow Bot Active in Group!</b>", parse_mode=ParseMode.HTML)
        return

    is_admin = await in_verified_group_admin(context, user.id)

    if is_admin:
        msg = f"🙏 <b>Welcome, {user.first_name} 🍁! This is your personal admin deal dashboard.</b>"
        kb = get_admin_keyboard()
    else:
        msg = f"🙏 <b>Welcome, {user.first_name} 🍁! Welcome to @GalaxyEscrowService.</b>"
        kb = get_user_keyboard()

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb)

async def add_deal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    if not await in_verified_group_admin(context, user.id):
        await update.message.reply_text("❌ <b>Only authorized admins can add deals.</b>", parse_mode=ParseMode.HTML)
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ <b>Please reply to the deal form message with /add [amount]</b>", parse_mode=ParseMode.HTML)
        return

    args = context.args
    if not args:
        await update.message.reply_text("⚠️ <b>Usage: /add [amount] or /add [amount] no fee</b>", parse_mode=ParseMode.HTML)
        return

    try:
        amount = float(args[0])
    except ValueError:
        await update.message.reply_text("❌ <b>Invalid amount specified!</b>", parse_mode=ParseMode.HTML)
        return

    is_no_fee = "no fee" in " ".join(args).lower()
    fee, net_release = calculate_fee(amount, is_no_fee)
    
    replied_text = update.message.reply_to_message.text or ""
    replied_user = update.message.reply_to_message.from_user

    # Precise Buyer & Seller Parsing
    buyer_match = re.search(r"buyer\s*[:\-]\s*(@?\w+|me)", replied_text, re.IGNORECASE)
    seller_match = re.search(r"seller\s*[:\-]\s*(@?\w+|me)", replied_text, re.IGNORECASE)

    if buyer_match and buyer_match.group(1).lower() != "me":
        b_raw = buyer_match.group(1)
        b_uname = b_raw if b_raw.startswith("@") else f"@{b_raw}"
        buyer_display = f"{b_uname} `(UID: {replied_user.id})`"
    else:
        b_uname = f"@{replied_user.username}" if replied_user.username else f"@{replied_user.first_name}"
        buyer_display = f"{b_uname} `(UID: {replied_user.id})`"

    if seller_match and seller_match.group(1).lower() != "me":
        s_raw = seller_match.group(1)
        s_uname = s_raw if s_raw.startswith("@") else f"@{s_raw}"
        seller_display = f"{s_uname} `(UID: {replied_user.id})`"
    else:
        # Fallback if seller is mentioned with @ in form or extract properly
        s_uname = "@Seller"
        seller_display = s_uname

    # If seller was typed like "SELLER :- @wiperout" in text
    if seller_match and seller_match.group(1).startswith("@"):
        s_uname = seller_match.group(1)
        seller_display = f"{s_uname} `(UID: {replied_user.id})`"

    info_match = re.search(r"(?:deal info|detail)\s*[:\-]\s*(.+)", replied_text, re.IGNORECASE)
    deal_info = info_match.group(1).split("\n")[0].strip() if info_match else "Escrow Trade"

    trade_id = generate_trade_id()

    deal_data = {
        "trade_id": trade_id,
        "amount": amount,
        "fee": fee,
        "net_release": net_release,
        "buyer": buyer_display,
        "seller": seller_display,
        "deal_info": deal_info[:50],
        "admin_username": user.username or user.first_name
    }

    card_text = format_deal_card(deal_data)
    sent_msg = await chat.send_message(card_text, parse_mode=ParseMode.HTML)

    conn = sqlite3.connect("galaxy_escrow.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO deals (trade_id, message_id, chat_id, buyer, buyer_username, seller, seller_username, deal_info, amount, fee, net_release, admin_id, admin_username)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (trade_id, sent_msg.message_id, chat.id, buyer_display, b_uname, seller_display, s_uname, deal_info, amount, fee, net_release, user.id, user.username or user.first_name))
    
    cursor.execute("""
        INSERT INTO admin_stats (admin_id, admin_username, current_holdings)
        VALUES (?, ?, ?)
        ON CONFLICT(admin_id) DO UPDATE SET
        current_holdings = current_holdings + ?
    """, (user.id, user.username or user.first_name, amount, amount))
    
    conn.commit()
    conn.close()

async def close_deal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if not update.message.photo:
        await update.message.reply_text("⚠️ <b>Please attach payment screenshot with caption: /close [Trade_ID]</b>", parse_mode=ParseMode.HTML)
        return

    caption = update.message.caption or ""
    match = re.search(r"/close\s+(GLX-\d+)", caption, re.IGNORECASE)
    if not match:
        await update.message.reply_text("⚠️ <b>Invalid format! Use caption: /close GLX-XXXX</b>", parse_mode=ParseMode.HTML)
        return

    trade_id = match.group(1).upper()

    conn = sqlite3.connect("galaxy_escrow.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM deals WHERE trade_id = ? AND status = 'ACTIVE'", (trade_id,))
    deal = cursor.fetchone()

    if not deal:
        await update.message.reply_text("❌ <b>Active deal with this Trade ID not found!</b>", parse_mode=ParseMode.HTML)
        conn.close()
        return

    admin_id = deal[11]
    if user.id != admin_id and user.id != BOT_OWNER_ID:
        await update.message.reply_text("❌ <b>Only the assigned deal admin or owner can close this deal!</b>", parse_mode=ParseMode.HTML)
        conn.close()
        return

    cursor.execute("UPDATE deals SET status = 'COMPLETED' WHERE trade_id = ?", (trade_id,))
    
    amount = deal[8]
    cursor.execute("""
        UPDATE admin_stats 
        SET current_holdings = MAX(0, current_holdings - ?),
            total_deals = total_deals + 1,
            total_volume = total_volume + ?
        WHERE admin_id = ?
    """, (amount, amount, admin_id))
    
    conn.commit()
    conn.close()

    # Clean usernames only for vouch/completion card
    b_clean = deal[4].split(" `(")[0]
    s_clean = deal[6].split(" `(")[0] if " `(" in deal[6] else deal[6]

    close_card = format_close_card(
        trade_id=deal[0],
        released_amount=deal[10],
        admin_username=deal[12],
        buyer_uname=b_clean,
        seller_uname=s_clean,
        original_amount=deal[8]
    )

    await update.message.reply_photo(
        photo=update.message.photo[-1].file_id,
        caption=close_card,
        parse_mode=ParseMode.HTML
    )

async def hold_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("galaxy_escrow.db")
    cursor = conn.cursor()
    cursor.execute("SELECT admin_username, current_holdings FROM admin_stats WHERE current_holdings > 0")
    records = cursor.fetchall()
    conn.close()

    if not records:
        await update.message.reply_text("<b>✅ Currently no active holdings across admins.</b>", parse_mode=ParseMode.HTML)
        return

    msg = "👑 <b>𝐆𝐀𝐋𝐀𝐗𝐘 𝐄𝐒𝐂𝐑𝐎𝐖 | 𝐀𝐃𝐌𝐈𝐍 𝐇𝐎𝐋𝐃𝐈𝐍𝐆𝐒</b>\n\n"
    for uname, hold in records:
        msg += f"👤 <b>Admin:</b> @{uname}\n💰 <b>Current Holding:</b> <b>₹{hold:,.2f}</b>\n\n"

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def top10_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("galaxy_escrow.db")
    cursor = conn.cursor()
    cursor.execute("SELECT admin_username, total_deals, total_volume FROM admin_stats ORDER BY total_volume DESC LIMIT 10")
    records = cursor.fetchall()
    conn.close()

    if not records:
        await update.message.reply_text("🏆 <b>Leaderboard is currently empty.</b>", parse_mode=ParseMode.HTML)
        return

    msg = "🏆 <b>𝐆𝐀𝐋𝐀𝐗𝐘 𝐄𝐒𝐂𝐑𝐎𝐖 | 𝐓𝐎𝐏 𝟏𝟎 𝐋𝐄𝐀𝐃𝐄𝐑𝐁𝐎𝐀𝐑𝐃</b>\n\n"
    for idx, (uname, deals, vol) in enumerate(records, 1):
        msg += f"<b>{idx}. @{uname}</b> — <b>{deals} Deals</b> | Volume: <b>₹{vol:,.2f}</b>\n"

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != BOT_OWNER_ID:
        await update.message.reply_text("❌ <b>This command is restricted to the bot owner only!</b>", parse_mode=ParseMode.HTML)
        return

    args = context.args
    if not args:
        await update.message.reply_text("⚠️ <b>Usage: /reset [username or admin_id]</b>", parse_mode=ParseMode.HTML)
        return

    target = args[0].lstrip("@")
    
    conn = sqlite3.connect("galaxy_escrow.db")
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM admin_stats WHERE admin_username LIKE ? OR admin_id = ?", (target, target))
    cursor.execute("DELETE FROM deals WHERE admin_username LIKE ?", (target,))
    
    conn.commit()
    conn.close()
    
    await update.message.reply_text(f"✅ <b>Successfully reset stats and deals data for:</b> <code>{target}</code>", parse_mode=ParseMode.HTML)

# ----------------------------------------------------
# BUTTON HANDLERS (REPLY KEYBOARD)
# ----------------------------------------------------
async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user = update.effective_user

    if text == "📊 My Stats":
        conn = sqlite3.connect("galaxy_escrow.db")
        cursor = conn.cursor()
        cursor.execute("SELECT total_deals, total_volume, current_holdings FROM admin_stats WHERE admin_id = ?", (user.id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            deals, volume, holding = row
            msg = f"📊 <b>Your Escrow Stats:</b>\n\n• Completed Deals: <b>{deals}</b>\n• Total Volume: <b>₹{volume:,.2f}</b>\n• Active Holdings: <b>₹{holding:,.2f}</b>"
        else:
            msg = "📊 <b>Your Escrow Stats:</b>\n\n• Completed Deals: <b>0</b>\n• Total Volume: <b>₹0.00</b>\n• Active Holdings: <b>₹0.00</b>"
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

    elif text in ["⏳ My Pending Deals", "⏳ Pending Deals"]:
        conn = sqlite3.connect("galaxy_escrow.db")
        cursor = conn.cursor()
        cursor.execute("SELECT trade_id, amount, deal_info FROM deals WHERE admin_id = ? AND status = 'ACTIVE'", (user.id,))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            await update.message.reply_text("⏳ <b>You have no pending active deals right now.</b>", parse_mode=ParseMode.HTML)
            return

        msg = "⏳ <b>Active Pending Deals:</b>\n\n"
        for tid, amt, info in rows:
            msg += f"• <code>{tid}</code> | ₹{amt:,.2f} | {info}\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

    elif text in ["📜 My Deal History", "📜 Completed Deals"]:
        conn = sqlite3.connect("galaxy_escrow.db")
        cursor = conn.cursor()
        cursor.execute("SELECT trade_id, amount, created_at FROM deals WHERE admin_id = ? AND status = 'COMPLETED' ORDER BY created_at DESC LIMIT 10", (user.id,))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            await update.message.reply_text("📜 <b>No completed deal history found.</b>", parse_mode=ParseMode.HTML)
            return

        msg = "📜 <b>Recent Completed Deals:</b>\n\n"
        for tid, amt, date in rows:
            msg += f"• <code>{tid}</code> — ₹{amt:,.2f} ({date})\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

    elif text == "💰 My Holdings":
        await hold_command(update, context)

    elif text == "📊 Admins Static":
        await top10_command(update, context)

    elif text == "🏆 Top 10":
        await top10_command(update, context)

# ----------------------------------------------------
# MAIN EXECUTION
# ----------------------------------------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("add", add_deal_command))
    app.add_handler(CommandHandler("hold", hold_command))
    app.add_handler(CommandHandler("top10", top10_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(r"/close"), close_deal_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_buttons))

    print("⚡ Galaxy Escrow Bot Running Successfully...")
    app.run_polling()

if __name__ == "__main__":
    main()
