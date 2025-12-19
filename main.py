import os
import random
import asyncio
import pymongo
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, User
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- 1. CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
OWNER_ID = 123456789  # Apna User ID yahan daal

# --- 2. DATABASE CONNECTION ---
try:
    client = pymongo.MongoClient(MONGO_URL)
    db = client["CasinoBot"]
    users_col = db["users"]
    print("✅ Database Connected!")
except Exception as e:
    print(f"❌ DB Error: {e}")

# --- 3. FLASK SERVER (24/7 UPTIME) ---
app = Flask('')

@app.route('/')
def home():
    return "Mines Bot is Running! 💣"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- 4. GAME SETTINGS ---
GRID_SIZE = 4
TOTAL_BOMBS = 3
MULTIPLIERS = [1.0, 1.15, 1.4, 1.7, 2.1, 2.6, 3.2, 4.0, 5.5, 7.5, 10.0, 15.0]

# RAM me game save karenge
active_games = {} 

# --- 5. HELPER FUNCTIONS ---
def get_user(user_id, name):
    user = users_col.find_one({"_id": user_id})
    if not user:
        user = {"_id": user_id, "name": name, "balance": 1000} 
        users_col.insert_one(user)
    return user

def update_balance(user_id, amount):
    # upsert=True zaroori hai taki agar user DB me na ho to ban jaye
    users_col.update_one({"_id": user_id}, {"$inc": {"balance": amount}}, upsert=True)

def get_balance(user_id):
    user = users_col.find_one({"_id": user_id})
    return user["balance"] if user else 0

# --- 6. GAME LOGIC ---
def create_board():
    cells = [0] * (GRID_SIZE * GRID_SIZE)
    bomb_indices = random.sample(range(len(cells)), TOTAL_BOMBS)
    for idx in bomb_indices:
        cells[idx] = 1
    return cells

def get_keyboard(game_data, game_over=False):
    grid = game_data["grid"]
    revealed = game_data["revealed"]
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
                # IMPORTANT: Callback me UserID bhi daal rahe hain group security ke liye
                callback = f"click_{index}_{game_data['user_id']}"
            
            row_btns.append(InlineKeyboardButton(text, callback_data=callback))
        keyboard.append(row_btns)
    
    if not game_over:
        current_mult = MULTIPLIERS[len(revealed)]
        win_amount = int(game_data["bet"] * current_mult)
        # Cashout me bhi UserID joda
        keyboard.append([InlineKeyboardButton(f"💰 Cashout (₹{win_amount})", callback_data=f"cashout_{game_data['user_id']}")])
        
    return InlineKeyboardMarkup(keyboard)

# --- 7. USER COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Ensure user DB me hai
    get_user(user.id, user.first_name)
    
    await update.message.reply_text(
        f"👋 **Welcome {user.first_name}!**\n\n"
        "🎮 **Mines Group Bot**\n"
        "💰 Aapke paas **1000 Coins** hain!\n\n"
        "📜 **Commands:**\n"
        "`/bet 100` - Game khelo\n"
        "`/pay` - Reply karke paise bhejo\n"
        "`/balance` - Balance check\n"
        "`/top` - Leaderboard",
        parse_mode=ParseMode.MARKDOWN
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = get_balance(update.effective_user.id)
    await update.message.reply_text(f"💳 **{update.effective_user.first_name}**, Aapka Balance: ₹{bal}", parse_mode=ParseMode.MARKDOWN)

async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    update_balance(user_id, 200)
    await update.message.reply_text(f"☀️ **Daily Bonus!** ₹200 added to {update.effective_user.first_name}.", parse_mode=ParseMode.MARKDOWN)

# --- FIXED PAY COMMAND ---
async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1. Check karo ki reply kiya hai ya nahi
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Kisi ke message par **Reply** karke `/pay 100` likho.")
        return
    
    try:
        # 2. Amount aur Users nikalo
        amount = int(context.args[0])
        sender = update.effective_user
        receiver = update.message.reply_to_message.from_user
        
        # 3. Validation checks
        if sender.id == receiver.id:
            await update.message.reply_text("❌ Khud ko paise nahi bhej sakte!")
            return
            
        if receiver.is_bot:
            await update.message.reply_text("❌ Bot ko paise nahi de sakte!")
            return
            
        if amount <= 0:
            await update.message.reply_text("❌ Sahi amount daalo (e.g., 50, 100).")
            return

        sender_bal = get_balance(sender.id)
        if sender_bal < amount:
            await update.message.reply_text(f"❌ **Low Balance!** Aapke paas sirf ₹{sender_bal} hain.")
            return
        
        # 4. Transaction (Yaha galti thi pehle)
        # Receiver ko pehle DB me ensure karo (Agar naya user hai to create ho jaye)
        get_user(receiver.id, receiver.first_name)
        
        # Paise kato aur jodo
        update_balance(sender.id, -amount)
        update_balance(receiver.id, amount)
        
        # 5. Success Message (Group Tagging)
        await update.message.reply_text(
            f"✅ **Transaction Successful!**\n\n"
            f"💸 **Sender:** {sender.mention_html()}\n"
            f"💰 **Receiver:** {receiver.mention_html()}\n"
            f"💵 **Amount:** ₹{amount}",
            parse_mode=ParseMode.HTML
        )
        
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: Reply karke `/pay 100` likho.")

# --- /BET COMMAND (Renamed from /play) ---
async def bet_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        bet_amount = int(context.args[0])
    except:
        await update.message.reply_text("⚠️ **Usage:** `/bet 100`")
        return
    
    if bet_amount < 10:
        await update.message.reply_text("❌ Min Bet: ₹10")
        return

    bal = get_balance(user.id)
    if bal < bet_amount:
        await update.message.reply_text(f"❌ **Gareeb!** Tere paas sirf ₹{bal} hain.")
        return
    
    # Paisa kato
    update_balance(user.id, -bet_amount)
    
    # Game start
    grid = create_board()
    game_id = f"{user.id}"
    active_games[game_id] = {
        "grid": grid,
        "revealed": [],
        "bet": bet_amount,
        "user_id": user.id,
        "name": user.first_name
    }
    
    await update.message.reply_text(
        f"💣 **Mines Started!**\n👤 Player: {user.first_name}\n💰 Bet: ₹{bet_amount}",
        reply_markup=get_keyboard(active_games[game_id])
    )

# --- GROUP SECURE CALLBACK ---
async def game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    # noop = No Operation (Khula hua box ya bomb dikhane ke liye)
    if data == "noop":
        await query.answer()
        return

    # Data format: "action_index_userid" (e.g., click_5_123456)
    parts = data.split("_")
    action = parts[0]
    
    # Security Check: Kya ye wahi user hai jisne game start kiya?
    game_owner_id = int(parts[-1])
    clicker_id = query.from_user.id
    
    if clicker_id != game_owner_id:
        await query.answer("❌ Ye aapka game nahi hai! Apna `/bet` lagao.", show_alert=True)
        return

    game_id = f"{game_owner_id}"
    
    if game_id not in active_games:
        await query.answer("❌ Game Expired!", show_alert=True)
        return
    
    game = active_games[game_id]
    
    if action == "cashout":
        revealed_count = len(game["revealed"])
        if revealed_count == 0:
            await query.answer("❌ 1 Box toh kholo!", show_alert=True)
            return
            
        mult = MULTIPLIERS[revealed_count]
        winnings = int(game["bet"] * mult)
        update_balance(game_owner_id, winnings)
        del active_games[game_id]
        
        await query.edit_message_text(f"🤑 **{game['name']} Won: ₹{winnings}**\n(Multiplier: {mult}x)")
        return

    if action == "click":
        index = int(parts[1])
        
        if game["grid"][index] == 1: # BOMB
            del active_games[game_id]
            game["revealed"].append(index)
            await query.edit_message_text(
                f"💥 **BOOM! {game['name']} Lost!**\n💸 ₹{game['bet']} gaye paani me.", 
                reply_markup=get_keyboard(game, game_over=True)
            )
        else: # SAFE
            if index not in game["revealed"]:
                game["revealed"].append(index)
            
            safe_cells = (GRID_SIZE * GRID_SIZE) - TOTAL_BOMBS
            
            if len(game["revealed"]) == safe_cells:
                mult = MULTIPLIERS[-1]
                winnings = int(game["bet"] * mult)
                update_balance(game_owner_id, winnings)
                del active_games[game_id]
                await query.edit_message_text(f"👑 **JACKPOT! {game['name']} Won: ₹{winnings}**")
            else:
                await query.edit_message_text(
                    f"💎 **Safe!** Next: {MULTIPLIERS[len(game['revealed'])]}x",
                    reply_markup=get_keyboard(game)
                )

# --- LEADERBOARD ---
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top_users = users_col.find().sort("balance", -1).limit(10)
    text = "🏆 **TOP 10 RICH LIST** 🏆\n\n"
    rank = 1
    for user in top_users:
        text += f"#{rank} {user['name']} : ₹{user['balance']}\n"
        rank += 1
    await update.message.reply_text(text)

# --- MAIN ---
def main():
    keep_alive()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("daily", daily))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("pay", pay))
    app.add_handler(CommandHandler("bet", bet_game)) # /bet command
    app.add_handler(CommandHandler("top", leaderboard))
    
    app.add_handler(CallbackQueryHandler(game_callback))
    
    print("🚀 Group Bot Started...")
    app.run_polling()

if __name__ == "__main__":
    main()

