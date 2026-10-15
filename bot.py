import os
import re
import sqlite3
import logging
from flask import Flask
from threading import Thread
from telegram import Update, Message
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

# ----------------------------------------------------
# 🌐 FLASK KEEP-ALIVE SERVER (FOR 24/7 RENDER HOSTING)
# ----------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Galaxy Escrow Bot is Live 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# Background thread start
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
            seller TEXT,
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
        f"<b>🍁 𝐆𝐀𝐋𝐀𝐗𝐘 𝐄𝐒𝐂𝐑𝐎𝐖 𝐃𝐄𝐀𝐋 𝐅𝐎𝐑𝐌</b>\n\n"
        f"🆔 <b>𝐓𝐫𝐚𝐝𝐞 𝐈𝐃:</b> <code>{deal_data['trade_id']}</code>\n\n"
        f"💰 <b>𝐃𝐄𝐀𝐋 𝐀𝐌𝐎𝐔𝐍𝐓 :-</b> <b>₹{deal_data['amount']:,.2f}</b>\n"
        f"💸 <b>𝐄𝐒𝐂𝐑𝐎𝐖 𝐅𝐄𝐄 :-</b> <b>₹{deal_data['fee']:,.2f}</b>\n"
        f"📤 <b>𝐍𝐄𝐓 𝐑𝐄𝐋𝐄𝐀𝐒𝐄 :-</b> <b>₹{deal_data['net_release']:,.2f}</b>\n\n"
        f"👤 <b>𝐁𝐔𝐘𝐄𝐑 :-</b> {deal_data['buyer']}\n"
        f"👤 <b>𝐒𝐄𝐋𝐋𝐄𝐑 :-</b> {deal_data['seller']}\n\n"
        f"📝 <b>𝐃𝐄𝐀𝐋 𝐈𝐍𝐅𝐎 :-</b> <b>{deal_data['deal_info']}</b>\n"
        f"⏱️ <b>𝐄𝐒𝐂𝐑𝐎𝐖 𝐓𝐈𝐋𝐋 :-</b> <b>1 Hour</b>\n"
        f"💳 <b>𝐏𝐀𝐘𝐌𝐄𝐍𝐓 𝐌𝐄𝐓𝐇𝐎𝐃 :-</b> <b>UPI</b>\n\n"
        f"🛡️ <b>𝐄𝐒𝐂𝐑𝐎𝐖𝐄𝐃 𝐁𝐘 :-</b> <b>@{deal_data['admin_username']}</b>\n\n"
        f"⚡ <b>@GalaxyEscrowService</b>\n"
        f"📌 <i>Add in your bio 👆👆</i>\n\n"
        f"👑 <b>𝐂𝐇𝐄𝐂𝐊 𝐎𝐔𝐑 𝐀𝐃𝐌𝐈𝐍𝐒 𝐋𝐈𝐒𝐓</b>\n"
        f"👉 <b>/Adminlist</b> 👈"
    )

def format_close_card(trade_id: str, released_amount: float, admin_username: str, buyer: str, seller: str, original_amount: float) -> str:
    return (
        f"✅ <b>𝐆𝐀𝐋𝐀𝐗𝐘 𝐄𝐒𝐂𝐑𝐎𝐖 | 𝐃𝐄𝐀𝐋 𝐂𝐎𝐌𝐏𝐋𝐄𝐓𝐄𝐃</b>\n\n"
        f"🆔 <b>𝐓𝐫𝐚𝐝𝐞 𝐈𝐃:</b> <code>{trade_id}</code>\n"
        f"📤 <b>𝐀𝐦𝐨𝐮𝐧𝐭 𝐑𝐞𝐥𝐞𝐚𝐬𝐞𝐝:</b> <b>₹{released_amount:,.2f}</b>\n"
        f"🛡️ <b>𝐄𝐒𝐂𝐑𝐎𝐖𝐄𝐃 𝐁𝐘:</b> <b>@{admin_username}</b>\n\n"
        f"🤝 <b>{buyer}</b> and <b>{seller}</b> are requested to drop the vouch before leaving 👇\n\n"
        f"<code>Vouch @GalaxyEscrowService for ₹{original_amount:,.2f} smooth escrow deal</code>"
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
        msg = (
            f"<b>👑 Welcome Admin {user.first_name}!</b>\n\n"
            f"<b>Your Admin Management Commands:</b>\n"
            f"• <b>/myhold</b> - View your current active holdings\n"
            f"• <b>/mypastdeals</b> - View count of completed deals\n"
            f"• <b>/mycurrentlydeals</b> - View running deals\n"
            f"• <b>/AdminsStatic</b> - View all admin metrics & holdings\n"
        )
    else:
        msg = (
            f"<b>🍁 Welcome to @GalaxyEscrowService!</b>\n\n"
            f"<b>User Commands:</b>\n"
            f"• <b>/mystats</b> - View your ranking and escrow stats\n"
            f"• <b>/mypendingdeals</b> - Check active running deals\n"
            f"• <b>/mydealhistory</b> - View past trade logs\n"
            f"• <b>/top10</b> - View group escrow leaderboard\n"
        )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

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

    buyer_match = re.search(r"buyer\s*:\s*(@?\w+|me)", replied_text, re.IGNORECASE)
    seller_match = re.search(r"seller\s*:\s*(@?\w+|me)", replied_text, re.IGNORECASE)

    buyer = buyer_match.group(1) if buyer_match else f"{replied_user.first_name} `(UID: {replied_user.id})`"
    seller = seller_match.group(1) if seller_match else "@Seller"

    if buyer.lower() == "me" or not buyer.startswith("@"):
        buyer = f"{replied_user.first_name} `(UID: {replied_user.id})`"
    if seller.lower() == "me":
        seller = f"{replied_user.first_name} `(UID: {replied_user.id})`"

    trade_id = generate_trade_id()
    deal_info = replied_text.split("\n")[0] if replied_text else "Escrow Trade"

    deal_data = {
        "trade_id": trade_id,
        "amount": amount,
        "fee": fee,
        "net_release": net_release,
        "buyer": buyer,
        "seller": seller,
        "deal_info": deal_info[:50],
        "admin_username": user.username or user.first_name
    }

    card_text = format_deal_card(deal_data)
    sent_msg = await chat.send_message(card_text, parse_mode=ParseMode.HTML)

    conn = sqlite3.connect("galaxy_escrow.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO deals (trade_id, message_id, chat_id, buyer, seller, deal_info, amount, fee, net_release, admin_id, admin_username)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (trade_id, sent_msg.message_id, chat.id, buyer, seller, deal_info, amount, fee, net_release, user.id, user.username or user.first_name))
    
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
        await update.message.reply_text("⚠️ <b>Please attach the payment screenshot with command: /close [Trade_ID]</b>", parse_mode=ParseMode.HTML)
        return

    caption = update.message.caption or ""
    match = re.search(r"/close\s+(GLX-\d+)", caption, re.IGNORECASE)
    if not match:
        await update.message.reply_text("⚠️ <b>Invalid command format! Use caption: /close GLX-XXXX</b>", parse_mode=ParseMode.HTML)
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

    admin_id = deal[9]
    if user.id != admin_id and user.id != BOT_OWNER_ID:
        await update.message.reply_text("❌ <b>Only the assigned deal admin or owner can close this deal!</b>", parse_mode=ParseMode.HTML)
        conn.close()
        return

    cursor.execute("UPDATE deals SET status = 'COMPLETED' WHERE trade_id = ?", (trade_id,))
    
    amount = deal[6]
    cursor.execute("""
        UPDATE admin_stats 
        SET current_holdings = MAX(0, current_holdings - ?),
            total_deals = total_deals + 1,
            total_volume = total_volume + ?
        WHERE admin_id = ?
    """, (amount, amount, admin_id))
    
    conn.commit()
    conn.close()

    close_card = format_close_card(
        trade_id=deal[0],
        released_amount=deal[8],
        admin_username=deal[10],
        buyer=deal[3],
        seller=deal[4],
        original_amount=deal[6]
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

# ----------------------------------------------------
# MAIN EXECUTION
# ----------------------------------------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("add", add_deal_command))
    app.add_handler(CommandHandler("hold", hold_command))
    app.add_handler(MessageHandler(filters.PHOTO & filters.caption_regex(r"/close"), close_deal_command))

    print("⚡ Galaxy Escrow Bot Running Successfully...")
    app.run_polling()

if __name__ == "__main__":
    main()
