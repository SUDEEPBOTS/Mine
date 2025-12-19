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
OWNER_ID = 6356015122 

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
def home(): return "Mines Bot is Running! 💣"
def run(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): t = Thread(target=run); t.start()

# --- 4. GAME & SHOP SETTINGS ---
GRID_SIZE = 4
BOMB_CONFIG = {
    1:  [1.01, 1.08, 1.15, 1.25, 1.40, 1.55, 1.75, 2.0, 2.5, 3.0, 4.0, 5.0], 
    3:  [1.10, 1.25, 1.45, 1.75, 2.15, 2.65, 3.30, 4.2, 5.5, 7.5, 10.0, 15.0], 
    5:  [1.30, 1.65, 2.20, 3.00, 4.20, 6.00, 9.00, 14.0, 22.0, 35.0, 50.0],    
    10: [2.50, 4.50, 9.00, 18.0, 40.0, 80.0]                                   
}
active_games = {} 
MAX_LOAN = 5000
LOAN_INTEREST = 0.10

# 🛍️ SHOP ITEMS LIST
SHOP_ITEMS = {
    "vip":   {"name": "👑 VIP Player", "price": 10000},
    "🎖️":   {"name": "🎖️VIP 2",    "price": 50000},
    "king":  {"name": "🦁 Lion King",  "price": 100000},
    "god":   {"name": "⚡ God Mode",   "price": 500000},
    "hacker":{"name": "👨‍💻 Hacker",    "price": 1000000}
}

# --- 5. HELPER FUNCTIONS ---
def get_user(user_id, name):
    user = users_col.find_one({"_id": user_id})
    if not user:
        user = {
            "_id": user_id, 
            "name": name, 
            "balance": 1000, 
            "loan": 0, 
            "redeemed_codes": [],
            "titles": [] # New Field for Shop
        } 
        users_col.insert_one(user)
    return user

def update_balance(user_id, amount):
    users_col.update_one({"_id": user_id}, {"$inc": {"balance": amount}}, upsert=True)

def get_balance(user_id):
    user = users_col.find_one({"_id": user_id})
    return user["balance"] if user else 0

async def delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    try: await context.bot.delete_message(chat_id=job.chat_id, message_id=job.data)
    except: pass

# --- 6. GAME LOGIC ---
def create_board(mines_count):
    cells = [0] * (GRID_SIZE * GRID_SIZE)
    bomb_indices = random.sample(range(len(cells)), mines_count)
    for idx in bomb_indices: cells[idx] = 1
    return cells

def get_keyboard(game_data, game_over=False):
    grid = game_data["grid"]
    revealed = game_data["revealed"]
    user_id = game_data["user_id"]
    mines = game_data["mines"]
    multipliers = BOMB_CONFIG.get(mines, BOMB_CONFIG[3])
    
    keyboard = []
    for row in range(GRID_SIZE):
        row_btns = []
        for col in range(GRID_SIZE):
            index = row * GRID_SIZE + col
            if game_over:
                text = "💣" if grid[index] == 1 else "💎"
                callback = "noop"
            elif index in revealed:
                text = "💎"
                callback = "noop"
            else:
                text = "🟦"
                callback = f"click_{index}_{user_id}"
            row_btns.append(InlineKeyboardButton(text, callback_data=callback))
        keyboard.append(row_btns)
    
    if not game_over:
        if len(revealed) > 0:
            current_mult = multipliers[len(revealed) - 1]
        else:
            current_mult = 1.0
        win_amount = int(game_data["bet"] * current_mult)
        
        keyboard.append([InlineKeyboardButton(f"💰 Cashout (₹{win_amount})", callback_data=f"cashout_{user_id}")])
        keyboard.append([InlineKeyboardButton("❌ Quit", callback_data=f"close_{user_id}")])
    else:
        keyboard.append([InlineKeyboardButton("❌ Close Message", callback_data=f"close_{user_id}")])
    return InlineKeyboardMarkup(keyboard)

# --- 7. COMMAND HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = get_user(user.id, user.first_name)
    
    # Show Titles
    titles = " | ".join(data.get("titles", []))
    if not titles: titles = "No Titles"
    
    await update.message.reply_text(
        f"👋 **Welcome {user.first_name}!**\n\n"
        f"🏷 **Titles:** {titles}\n"
        f"💰 **Balance:** ₹{data['balance']}\n\n"
        "🎮 Use `/bet 100` to Play\n"
        "🛒 Use `/shop` to Buy Titles",
        parse_mode=ParseMode.MARKDOWN
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📚 **COMMAND LIST**\n\n"
        "🎮 `/bet 100` - Game Khelo\n"
        "🛒 `/shop` - Titles Khareedo\n"
        "🎒 `/myitems` - Apni Inventory Dekho\n"
        "💳 `/balance` - Paisa Dekho\n"
        "🎁 `/redeem <code>` - Promo Code\n"
        "💸 `/pay <amount>` - Transfer Money\n"
        "🏦 `/loan <amount>` - Take Loan\n"
        "🔙 `/payloan` - Repay Loan"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# --- SHOP COMMANDS ---
async def shop_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = []
    
    # Generate Shop Buttons
    for key, item in SHOP_ITEMS.items():
        btn_text = f"{item['name']} - ₹{item['price']}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"buy_{key}_{user.id}")])
    
    keyboard.append([InlineKeyboardButton("❌ Close Shop", callback_data=f"close_{user.id}")])
    
    await update.message.reply_text(
        "🛒 **VIP SHOP**\n\nCoins kharch karo aur Titles paao!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def my_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = get_user(user.id, user.first_name)
    titles = data.get("titles", [])
    
    if not titles:
        await update.message.reply_text("🎒 **Inventory Empty!**\n`/shop` se kuch khareedo gareeb! 😂", parse_mode=ParseMode.MARKDOWN)
    else:
        items_list = "\n".join([f"✅ {t}" for t in titles])
        await update.message.reply_text(f"🎒 **YOUR TITLES:**\n\n{items_list}", parse_mode=ParseMode.MARKDOWN)

# --- OTHER COMMANDS ---
async def bet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try: bet_amount = int(context.args[0])
    except: await update.message.reply_text("⚠️ Usage: `/bet 100`"); return
    if bet_amount < 10: await update.message.reply_text("Min Bet: ₹10"); return
    bal = get_balance(user.id)
    if bal < bet_amount: await update.message.reply_text(f"❌ Low Balance: ₹{bal}"); return

    keyboard = [
        [InlineKeyboardButton("🟢 1 Bomb", callback_data=f"select_1_{bet_amount}_{user.id}"), InlineKeyboardButton("🟡 3 Bombs", callback_data=f"select_3_{bet_amount}_{user.id}")],
        [InlineKeyboardButton("🔴 5 Bombs", callback_data=f"select_5_{bet_amount}_{user.id}"), InlineKeyboardButton("💀 10 Bombs", callback_data=f"select_10_{bet_amount}_{user.id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"close_{user.id}")]
    ]
    await update.message.reply_text(f"⚙️ **Difficulty Select**\nBet: ₹{bet_amount}", reply_markup=InlineKeyboardMarkup(keyboard))

async def take_loan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user(user.id, user.first_name)
    if u.get("loan", 0) > 0: await update.message.reply_text("❌ Loan Active!"); return
    try: amt = int(context.args[0])
    except: return
    if amt > MAX_LOAN: return
    repay = int(amt + (amt*LOAN_INTEREST))
    users_col.update_one({"_id": user.id}, {"$inc": {"balance": amt}, "$set": {"loan": repay}})
    await update.message.reply_text(f"✅ Loan Taken: ₹{amt}")

async def pay_loan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = users_col.find_one({"_id": user.id})
    if u.get("balance", 0) < u.get("loan", 0): await update.message.reply_text("❌ Low Balance"); return
    users_col.update_one({"_id": user.id}, {"$inc": {"balance": -u["loan"]}, "$set": {"loan": 0}})
    await update.message.reply_text("✅ Loan Repaid!")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"💳 Balance: ₹{get_balance(update.effective_user.id)}")

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message: return
    try:
        amt = int(context.args[0])
        sender = update.effective_user; receiver = update.message.reply_to_message.from_user
        if sender.id == receiver.id or amt <= 0: return
        if get_balance(sender.id) < amt: return
        get_user(receiver.id, receiver.first_name)
        update_balance(sender.id, -amt); update_balance(receiver.id, amt)
        await update.message.reply_text(f"✅ Sent ₹{amt}")
    except: pass

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: return
    message = " ".join(context.args)
    users = users_col.find({})
    for user in users:
        try: await context.bot.send_message(chat_id=user["_id"], text=f"📢 **ANNOUNCEMENT**\n\n{message}", parse_mode=ParseMode.MARKDOWN)
        except: pass
    await update.message.reply_text("✅ Broadcast Sent.")

async def create_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        code_name = context.args[0]; amount = int(context.args[1]); limit = int(context.args[2])
        codes_col.insert_one({"code": code_name, "amount": amount, "limit": limit, "redeemed_by": []})
        await update.message.reply_text(f"✅ Code Created: {code_name}")
    except: pass

async def redeem_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try: code_name = context.args[0]
    except: return
    code_data = codes_col.find_one({"code": code_name})
    if not code_data or len(code_data["redeemed_by"]) >= code_data["limit"] or user_id in code_data["redeemed_by"]:
        await update.message.reply_text("❌ Invalid or Used Code"); return
    update_balance(user_id, code_data["amount"])
    codes_col.update_one({"code": code_name}, {"$push": {"redeemed_by": user_id}})
    await update.message.reply_text(f"🎉 Redeemed ₹{code_data['amount']}!")

async def add_money(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == OWNER_ID:
        try: update_balance(int(context.args[0]), int(context.args[1])); await update.message.reply_text("Done")
        except: pass

async def take_money(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == OWNER_ID:
        try: update_balance(int(context.args[0]), -int(context.args[1])); await update.message.reply_text("Done")
        except: pass

# --- CALLBACKS (GAME + SHOP) ---
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data == "noop": await query.answer(); return
    parts = data.split("_")
    action = parts[0]

    # --- SHOP LOGIC ---
    if action == "buy":
        item_key = parts[1]
        buyer_id = int(parts[2])
        
        if query.from_user.id != buyer_id: await query.answer("Apna shop kholo!", show_alert=True); return
        
        item = SHOP_ITEMS.get(item_key)
        if not item: return

        # Check Balance & Ownership
        user_data = users_col.find_one({"_id": buyer_id})
        if item["name"] in user_data.get("titles", []):
            await query.answer("⚠️ Ye pehle se hai tumhare paas!", show_alert=True); return
            
        if user_data["balance"] < item["price"]:
            await query.answer(f"❌ Paisa nahi hai! Need ₹{item['price']}", show_alert=True); return
            
        # Purchase
        update_balance(buyer_id, -item["price"])
        users_col.update_one({"_id": buyer_id}, {"$push": {"titles": item["name"]}})
        await query.answer(f"🎉 Purchased: {item['name']}", show_alert=True)
        await query.edit_message_text(f"✅ **Bought:** {item['name']}\nInventory check karo: `/myitems`", parse_mode=ParseMode.MARKDOWN)
        return

    # --- GAME LOGIC ---
    if action == "select":
        mines = int(parts[1]); bet = int(parts[2]); owner = int(parts[3])
        if query.from_user.id != owner: await query.answer("Not your game!", show_alert=True); return
        if get_balance(owner) < bet: await query.answer("No Money!", show_alert=True); await query.message.delete(); return
        update_balance(owner, -bet)
        grid = create_board(mines)
        active_games[f"{owner}"] = {"grid": grid, "revealed": [], "bet": bet, "user_id": owner, "mines": mines}
        await query.edit_message_text(f"💣 Mines ({mines})\n💰 Bet: {bet}", reply_markup=get_keyboard(active_games[f"{owner}"]))
        return

    owner = int(parts[-1]); clicker = query.from_user.id
    if action == "close": 
        if f"{owner}" in active_games: del active_games[f"{owner}"]
        await query.message.delete(); return
    
    if clicker != owner: await query.answer("Not your game!", show_alert=True); return
        
    game_id = f"{owner}"
    if game_id not in active_games: await query.answer("Expired", show_alert=True); return
    game = active_games[game_id]
    multipliers = BOMB_CONFIG.get(game["mines"], BOMB_CONFIG[3])

    if action == "cashout":
        if not game["revealed"]: await query.answer("Open 1 box!", show_alert=True); return
        mult = multipliers[len(game["revealed"])-1]
        win = int(game["bet"]*mult)
        update_balance(owner, win)
        del active_games[game_id]
        await query.edit_message_text(f"🤑 Won: ₹{win}\n🗑 Deleting...", reply_markup=get_keyboard(game, True))
        context.job_queue.run_once(delete_message_job, 30, chat_id=query.message.chat_id, data=query.message.message_id)
        return

    if action == "click":
        idx = int(parts[1])
        if game["grid"][idx] == 1:
            del active_games[game_id]
            game["revealed"].append(idx)
            await query.edit_message_text(f"💥 BOOM! Lost ₹{game['bet']}\n🗑 Deleting...", reply_markup=get_keyboard(game, True))
            context.job_queue.run_once(delete_message_job, 30, chat_id=query.message.chat_id, data=query.message.message_id)
        else:
            if idx not in game["revealed"]: game["revealed"].append(idx)
            safe = (GRID_SIZE*GRID_SIZE) - game["mines"]
            if len(game["revealed"]) == safe:
                mult = multipliers[-1] if len(game["revealed"])-1 < len(multipliers) else multipliers[-1]
                win = int(game["bet"]*mult)
                update_balance(owner, win)
                del active_games[game_id]
                await query.edit_message_text(f"👑 JACKPOT! Won ₹{win}")
                context.job_queue.run_once(delete_message_job, 30, chat_id=query.message.chat_id, data=query.message.message_id)
            else:
                next_mult = multipliers[len(game["revealed"])] if len(game["revealed"]) < len(multipliers) else multipliers[-1]
                await query.edit_message_text(f"💎 Safe! Next: {next_mult}x", reply_markup=get_keyboard(game))

# --- MAIN ---
def main():
    keep_alive()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("bet", bet_menu))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("pay", pay))
    
    # Shop Features
    app.add_handler(CommandHandler("shop", shop_menu))
    app.add_handler(CommandHandler("myitems", my_items))

    app.add_handler(CommandHandler("cast", broadcast))       
    app.add_handler(CommandHandler("code", create_code))     
    app.add_handler(CommandHandler("redeem", redeem_code))   
    
    app.add_handler(CommandHandler("loan", take_loan))
    app.add_handler(CommandHandler("payloan", pay_loan))
    app.add_handler(CommandHandler("add", add_money))
    app.add_handler(CommandHandler("take", take_money))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    print("🚀 Bot Started with SHOP & All Features...")
    app.run_polling()

if __name__ == "__main__":
    main()
                
