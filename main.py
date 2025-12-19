import os
import random
import asyncio
import pymongo
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- 1. CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
OWNER_ID = int(os.getenv("OWNER_ID", 0)) # Security: Env se lega

# --- 2. DATABASE CONNECTION ---
try:
    client = pymongo.MongoClient(MONGO_URL)
    db = client["CasinoBot"]
    users_col = db["users"]
    codes_col = db["codes"]
    print("✅ Database Connected!")
except Exception as e:
    print(f"❌ DB Error: {e}")

# --- 3. FLASK SERVER ---
app = Flask('')
@app.route('/')
def home(): return "Bot Running!"
def run(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): t = Thread(target=run); t.start()

# --- 4. SETTINGS & CONFIGS ---
GRID_SIZE = 4

# 📉 HARD ECONOMY (1 Bomb = Low Profit)
BOMB_CONFIG = {
    1:  [1.01, 1.02, 1.05, 1.08, 1.12, 1.16, 1.25, 1.35, 1.50, 1.70, 2.20, 3.50],
    3:  [1.10, 1.25, 1.45, 1.75, 2.15, 2.65, 3.30, 4.20, 5.50, 7.50, 10.0, 15.0],
    5:  [1.30, 1.65, 2.20, 3.00, 4.20, 6.00, 9.00, 14.0, 22.0, 35.0, 50.0],    
    10: [2.50, 4.50, 9.00, 18.0, 40.0, 80.0]                                   
}

active_games = {} 
MAX_LOAN = 5000
LOAN_INTEREST = 0.10

# 📊 SHARE MARKET STOCKS (Base Prices)
STOCKS = {
    "BTC":   {"name": "Bitcoin",     "price": 5000},
    "TATA":  {"name": "Tata Motors", "price": 1000},
    "RELI":  {"name": "Reliance",    "price": 2500},
    "MRF":   {"name": "MRF Tyres",   "price": 800},
    "ZOMA":  {"name": "Zomato",      "price": 150}
}

# 🛍️ SHOP ITEMS
SHOP_ITEMS = {
    "vip":   {"name": "👑 VIP",      "price": 10000},
    "king":  {"name": "🦁 King",     "price": 50000},
    "god":   {"name": "⚡ God Mode", "price": 500000}
}

# --- 5. HELPER FUNCTIONS ---
def get_user(user_id, name):
    user = users_col.find_one({"_id": user_id})
    if not user:
        user = {
            "_id": user_id, "name": name, "balance": 1000, 
            "loan": 0, "redeemed_codes": [], "titles": [], "portfolio": {}
        } 
        users_col.insert_one(user)
    # Ensure name is updated if changed
    if user.get("name") != name:
        users_col.update_one({"_id": user_id}, {"$set": {"name": name}})
    return user

def update_balance(user_id, amount):
    users_col.update_one({"_id": user_id}, {"$inc": {"balance": amount}}, upsert=True)

def get_balance(user_id):
    user = users_col.find_one({"_id": user_id})
    return user["balance"] if user else 0

# Market Price Calculator (Random Fluctuation -5% to +5%)
def get_current_price(symbol):
    base = STOCKS[symbol]["price"]
    fluctuation = random.uniform(0.95, 1.05) 
    return int(base * fluctuation)

# Auto Delete & Cleanup
async def cleanup(update: Update):
    try: await update.message.delete()
    except: pass

async def delete_job(context: ContextTypes.DEFAULT_TYPE):
    try: await context.bot.delete_message(context.job.chat_id, context.job.data)
    except: pass

# --- 6. GAME LOGIC ---
def create_board(mines):
    cells = [0] * 16
    indices = random.sample(range(16), mines)
    for i in indices: cells[i] = 1
    return cells

def get_keyboard(game, game_over=False):
    grid = game["grid"]; revealed = game["revealed"]; uid = game["user_id"]
    mines = game["mines"]; mults = BOMB_CONFIG.get(mines, BOMB_CONFIG[3])
    kb = []
    for r in range(4):
        row = []
        for c in range(4):
            idx = r * 4 + c
            if game_over: txt = "💣" if grid[idx] == 1 else "💎"; cb = "noop"
            elif idx in revealed: txt = "💎"; cb = "noop"
            else: txt = "🟦"; cb = f"click_{idx}_{uid}"
            row.append(InlineKeyboardButton(txt, callback_data=cb))
        kb.append(row)
    
    if not game_over:
        m = mults[len(revealed)-1] if revealed else 1.0
        win = int(game["bet"] * m)
        kb.append([InlineKeyboardButton(f"💰 Cashout (₹{win})", callback_data=f"cashout_{uid}")])
        kb.append([InlineKeyboardButton("❌ Quit", callback_data=f"close_{uid}")])
    else:
        kb.append([InlineKeyboardButton("❌ Close", callback_data=f"close_{uid}")])
    return InlineKeyboardMarkup(kb)

# --- 7. MAIN COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup(update)
    user = update.effective_user
    u = get_user(user.id, user.first_name)
    titles = " ".join(u.get("titles", []))
    
    txt = (f"👋 **Hi {user.first_name}!** {titles}\n"
           f"💰 Wallet: ₹{u['balance']}\n\n"
           "🎮 `/bet 100` - Mines Game\n"
           "📈 `/market` - Buy Shares\n"
           "🏆 `/top` - Leaderboard\n"
           "💳 `/balance` - Check Money")
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

# 🏆 FIXED LEADERBOARD (Net Worth Based)
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup(update)
    all_users = users_col.find({})
    rich_list = []
    
    msg = await update.message.reply_text("🔄 **Calculating Net Worth...**")
    
    for u in all_users:
        bal = u.get("balance", 0)
        port = u.get("portfolio", {})
        stock_val = 0
        
        # Calculate Share Value Live
        for sym, qty in port.items():
            if qty > 0 and sym in STOCKS:
                stock_val += (get_current_price(sym) * qty)
        
        total = bal + stock_val
        name = u.get("name", "Unknown")
        rich_list.append({"name": name, "worth": total, "cash": bal, "stock": stock_val})
    
    # Sort by Net Worth (Highest First)
    rich_list.sort(key=lambda x: x["worth"], reverse=True)
    
    text = "🏆 **TOP 10 RICH LIST (Net Worth)** 🏆\n\n"
    for i, p in enumerate(rich_list[:10], 1):
        text += f"#{i} **{p['name']}**\n💰 Net Worth: ₹{p['worth']}\n(Cash: {p['cash']} | Shares: {p['stock']})\n\n"
        
    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)

# 📈 SHARE MARKET COMMANDS
async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup(update)
    txt = "📈 **LIVE MARKET** 📉\n\n"
    for s in STOCKS:
        p = get_current_price(s)
        txt += f"🏢 **{s}**: ₹{p}\n"
    txt += "\n`/buy BTC 1` | `/sell BTC 1`\n`/portfolio` - Check Shares"
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup(update)
    user = update.effective_user
    try:
        sym = context.args[0].upper(); qty = int(context.args[1])
        if sym not in STOCKS or qty <= 0: return
        price = get_current_price(sym); cost = price * qty
        if get_balance(user.id) < cost: 
            m = await update.message.reply_text("❌ Low Balance!"); context.job_queue.run_once(delete_job, 5, chat_id=m.chat_id, data=m.message_id); return
        
        update_balance(user.id, -cost)
        users_col.update_one({"_id": user.id}, {"$inc": {f"portfolio.{sym}": qty}})
        await update.message.reply_text(f"✅ Bought {qty} {sym} for ₹{cost}")
    except: pass

async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup(update)
    user = update.effective_user
    try:
        sym = context.args[0].upper(); qty = int(context.args[1])
        u = users_col.find_one({"_id": user.id})
        curr_qty = u.get("portfolio", {}).get(sym, 0)
        
        if curr_qty < qty: 
            m = await update.message.reply_text("❌ Not enough shares!"); context.job_queue.run_once(delete_job, 5, chat_id=m.chat_id, data=m.message_id); return
        
        price = get_current_price(sym); val = price * qty
        users_col.update_one({"_id": user.id}, {"$inc": {f"portfolio.{sym}": -qty, "balance": val}})
        await update.message.reply_text(f"📉 Sold {qty} {sym} for ₹{val}")
    except: pass

async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup(update)
    u = users_col.find_one({"_id": update.effective_user.id})
    port = u.get("portfolio", {})
    txt = "💼 **PORTFOLIO**\n\n"
    total = 0
    for s, q in port.items():
        if q > 0:
            val = get_current_price(s) * q
            total += val
            txt += f"🔹 {s}: {q} (₹{val})\n"
    txt += f"\n💰 Shares Value: ₹{total}"
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

# 💸 PAY COMMAND (DM FIX)
async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup(update)
    if not update.message.reply_to_message: return
    try:
        amt = int(context.args[0])
        sender = update.effective_user; receiver = update.message.reply_to_message.from_user
        if sender.id == receiver.id or amt <= 0 or receiver.is_bot: return
        if get_balance(sender.id) < amt:
            m = await update.message.reply_text("❌ Low Balance!")
            context.job_queue.run_once(delete_job, 5, chat_id=m.chat_id, data=m.message_id)
            return
        
        get_user(receiver.id, receiver.first_name)
        update_balance(sender.id, -amt); update_balance(receiver.id, amt)
        
        await update.message.reply_text(f"✅ **Paid!**\n{sender.first_name} ➡️ {receiver.first_name}\nAmount: ₹{amt}", parse_mode=ParseMode.MARKDOWN)
        
        # DM Notification (Try-Except block to prevent crash)
        try:
            await context.bot.send_message(chat_id=receiver.id, text=f"🔔 **Money Received!**\nFrom: {sender.first_name}\nAmount: ₹{amt}")
        except: pass
    except: pass

# 🎮 BET & GAME SYSTEM
async def bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup(update)
    user = update.effective_user
    try: amt = int(context.args[0])
    except: m = await update.message.reply_text("Use: `/bet 100`"); context.job_queue.run_once(delete_job, 5, chat_id=m.chat_id, data=m.message_id); return
    
    if amt < 10: return
    if get_balance(user.id) < amt: m = await update.message.reply_text("❌ Gareeb!"); context.job_queue.run_once(delete_job, 5, chat_id=m.chat_id, data=m.message_id); return

    kb = [[InlineKeyboardButton("🟢 1 Bomb", callback_data=f"sel_1_{amt}_{user.id}"), InlineKeyboardButton("🟡 3 Bombs", callback_data=f"sel_3_{amt}_{user.id}")],
          [InlineKeyboardButton("🔴 5 Bombs", callback_data=f"sel_5_{amt}_{user.id}"), InlineKeyboardButton("💀 10 Bombs", callback_data=f"sel_10_{amt}_{user.id}")],
          [InlineKeyboardButton("❌ Cancel", callback_data=f"close_{user.id}")]]
    await update.message.reply_text(f"⚙️ **Select Difficulty**\nBet: ₹{amt}", reply_markup=InlineKeyboardMarkup(kb))

async def game_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data
    if d == "noop": await q.answer(); return
    parts = d.split("_")
    act = parts[0]

    # SELECT DIFFICULTY
    if act == "sel":
        mines = int(parts[1]); amt = int(parts[2]); owner = int(parts[3])
        if q.from_user.id != owner: await q.answer("Not your game!", show_alert=True); return
        if get_balance(owner) < amt: await q.answer("Low Balance!", show_alert=True); await q.message.delete(); return
        
        update_balance(owner, -amt)
        grid = create_board(mines)
        active_games[f"{owner}"] = {"grid": grid, "revealed": [], "bet": amt, "user_id": owner, "mines": mines}
        await q.edit_message_text(f"💣 Mines ({mines})\n💰 Bet: {amt}", reply_markup=get_keyboard(active_games[f"{owner}"]))
        return

    owner = int(parts[-1]); clicker = q.from_user.id
    if act == "close": 
        if f"{owner}" in active_games: del active_games[f"{owner}"]
        await q.message.delete(); return
    
    if clicker != owner: await q.answer("Not your game!", show_alert=True); return
    if f"{owner}" not in active_games: await q.answer("Expired", show_alert=True); return
    
    game = active_games[f"{owner}"]
    mults = BOMB_CONFIG.get(game["mines"], BOMB_CONFIG[3])

    if act == "cashout":
        if not game["revealed"]: await q.answer("Open 1 box!", show_alert=True); return
        m = mults[len(game["revealed"])-1]
        win = int(game["bet"] * m)
        update_balance(owner, win)
        del active_games[f"{owner}"]
        await q.edit_message_text(f"🤑 Won: ₹{win}\n🗑 Deleting...", reply_markup=get_keyboard(game, True))
        context.job_queue.run_once(delete_job, 30, chat_id=q.message.chat_id, data=q.message.message_id)
        return

    if act == "click":
        idx = int(parts[1])
        if game["grid"][idx] == 1:
            del active_games[f"{owner}"]
            game["revealed"].append(idx)
            await q.edit_message_text(f"💥 BOOM! Lost ₹{game['bet']}\n🗑 Deleting...", reply_markup=get_keyboard(game, True))
            context.job_queue.run_once(delete_job, 30, chat_id=q.message.chat_id, data=q.message.message_id)
        else:
            if idx not in game["revealed"]: game["revealed"].append(idx)
            safe = 16 - game["mines"]
            if len(game["revealed"]) == safe:
                m = mults[-1]
                win = int(game["bet"] * m)
                update_balance(owner, win)
                del active_games[f"{owner}"]
                await q.edit_message_text(f"👑 JACKPOT! Won ₹{win}")
                context.job_queue.run_once(delete_job, 30, chat_id=q.message.chat_id, data=q.message.message_id)
            else:
                next_m = mults[len(game["revealed"])] if len(game["revealed"]) < len(mults) else mults[-1]
                await q.edit_message_text(f"💎 Safe! Next: {next_m}x", reply_markup=get_keyboard(game))

# --- OTHER UTILS ---
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup(update)
    await update.message.reply_text(f"💳 Balance: ₹{get_balance(update.effective_user.id)}")

async def loan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup(update)
    u = get_user(update.effective_user.id, update.effective_user.first_name)
    if u["loan"] > 0: return
    try: a = int(context.args[0])
    except: return
    if a > MAX_LOAN: return
    users_col.update_one({"_id": u["_id"]}, {"$inc": {"balance": a}, "$set": {"loan": int(a*1.1)}})
    await update.message.reply_text(f"✅ Loan: ₹{a}")

async def payloan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup(update)
    u = users_col.find_one({"_id": update.effective_user.id})
    if u["balance"] < u["loan"]: await update.message.reply_text("❌ Low Balance"); return
    users_col.update_one({"_id": u["_id"]}, {"$inc": {"balance": -u["loan"]}, "$set": {"loan": 0}})
    await update.message.reply_text("✅ Loan Paid")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup(update)
    await update.message.reply_text("📚 `/bet`, `/market`, `/top`, `/pay`, `/shop`, `/balance`", parse_mode=ParseMode.MARKDOWN)

# --- MAIN ---
def main():
    keep_alive()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("bet", bet))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("pay", pay))
    app.add_handler(CommandHandler("top", leaderboard)) # Fixed Logic
    
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("portfolio", portfolio))
    
    app.add_handler(CommandHandler("loan", loan))
    app.add_handler(CommandHandler("payloan", payloan))
    
    app.add_handler(CallbackQueryHandler(game_cb))
    
    print("🚀 Bot Started (Bug Free + Net Worth Leaderboard)...")
    app.run_polling()

if __name__ == "__main__":
    main()
    
