import os
import random
import asyncio
import pymongo
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
OWNER_ID = 6356015122  # Apna Telegram ID daal (Admin commands ke liye)

# --- DATABASE CONNECTION ---
try:
    client = pymongo.MongoClient(MONGO_URL)
    db = client["CasinoBot"]
    users_col = db["users"]
    print("✅ Database Connected!")
except Exception as e:
    print(f"❌ DB Error: {e}")

# --- FLASK SERVER (24/7 UPTIME) ---
app = Flask('')

@app.route('/')
def home():
    return "Mines Bot is Running! 💣"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- GAME CONSTANTS ---
GRID_SIZE = 4
TOTAL_BOMBS = 3
MULTIPLIERS = [1.0, 1.2, 1.5, 1.9, 2.4, 3.0, 4.0, 5.5, 7.5, 10.0, 15.0]

# --- MEMORY STORAGE (Temporary Game State) ---
# Active games ko RAM me rakhenge fast experience ke liye
active_games = {} 

# --- HELPER FUNCTIONS ---
def get_user(user_id, name):
    user = users_col.find_one({"_id": user_id})
    if not user:
        user = {"_id": user_id, "name": name, "balance": 1000} # Start with 1000 coins
        users_col.insert_one(user)
    return user

def update_balance(user_id, amount):
    users_col.update_one({"_id": user_id}, {"$inc": {"balance": amount}})

def get_balance(user_id):
    user = users_col.find_one({"_id": user_id})
    return user["balance"] if user else 0

# --- GAME LOGIC ---
def create_board():
    # 0 = Safe, 1 = Bomb
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
                # Game khatam: Sab dikha do
                text = "💣" if grid[index] == 1 else "💎"
                callback = "noop"
            elif index in revealed:
                # Khula hua box
                text = "💎"
                callback = "noop"
            else:
                # Band box
                text = "⬜️"
                callback = f"click_{index}"
            
            row_btns.append(InlineKeyboardButton(text, callback_data=callback))
        keyboard.append(row_btns)
    
    # Niche Cashout Button
    if not game_over:
        current_mult = MULTIPLIERS[len(revealed)]
        win_amount = int(game_data["bet"] * current_mult)
        keyboard.append([InlineKeyboardButton(f"💰 Cashout (₹{win_amount})", callback_data="cashout")])
        
    return InlineKeyboardMarkup(keyboard)

# --- COMMAND HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, user.first_name)
    await update.message.reply_text(
        f"👋 **Welcome {user.first_name}!**\n\n"
        "🎮 **Mines Game Bot** mein swagat hai.\n"
        "💰 Aapke paas **1000 Coins** free hain!\n\n"
        "📜 **Commands:**\n"
        "/play 100 - Game khelo (100 coins ki bet)\n"
        "/balance - Paisa check karo\n"
        "/daily - Free daily coins\n"
        "/pay - Reply karke paise bhejo",
        parse_mode=ParseMode.MARKDOWN
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = get_balance(update.effective_user.id)
    await update.message.reply_text(f"💳 **Wallet Balance:** ₹{bal}", parse_mode=ParseMode.MARKDOWN)

async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Yaha cooldown logic laga sakte ho, abhi ke liye simple rakha hai
    update_balance(user_id, 200)
    await update.message.reply_text("☀️ **Daily Bonus!** ₹200 added.", parse_mode=ParseMode.MARKDOWN)

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Usage: Message par reply karke /pay 500
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Kisi user ke message par **Reply** karke `/pay amount` likhein.")
        return
    
    try:
        amount = int(context.args[0])
        sender_id = update.effective_user.id
        receiver_id = update.message.reply_to_message.from_user.id
        
        if sender_id == receiver_id:
            await update.message.reply_text("❌ Khud ko paise nahi bhej sakte!")
            return
            
        if amount <= 0:
            await update.message.reply_text("❌ Valid amount daalo.")
            return

        sender_bal = get_balance(sender_id)
        if sender_bal < amount:
            await update.message.reply_text(f"❌ **Low Balance!** Aapke paas sirf ₹{sender_bal} hain.")
            return
        
        # Transaction
        update_balance(sender_id, -amount)
        # Receiver ko DB me check karo/add karo
        get_user(receiver_id, update.message.reply_to_message.from_user.first_name)
        update_balance(receiver_id, amount)
        
        await update.message.reply_text(f"✅ **Transfer Successful!**\n₹{amount} sent to {update.message.reply_to_message.from_user.first_name}.")
        
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Usage: `/pay 100` (Reply karte hue)")

async def play_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        bet_amount = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ **Usage:** `/play 100` (Amount likho)")
        return
    
    if bet_amount < 10:
        await update.message.reply_text("❌ Minimum bet is ₹10")
        return

    bal = get_balance(user_id)
    if bal < bet_amount:
        await update.message.reply_text(f"❌ **Gareeb!** Tere paas sirf ₹{bal} hain.")
        return
    
    # Paisa kato
    update_balance(user_id, -bet_amount)
    
    # Game State Create Karo
    grid = create_board()
    game_id = f"{user_id}"
    active_games[game_id] = {
        "grid": grid,
        "revealed": [],
        "bet": bet_amount,
        "user_id": user_id
    }
    
    await update.message.reply_text(
        f"💣 **Mines Started!**\n💰 Bet: ₹{bet_amount}\n⚠️ Mines: {TOTAL_BOMBS}",
        reply_markup=get_keyboard(active_games[game_id])
    )

async def game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    game_id = f"{user_id}"
    
    if game_id not in active_games:
        await query.answer("❌ Game expire ho gaya hai!", show_alert=True)
        return
    
    data = query.data
    game = active_games[game_id]
    
    if data == "cashout":
        revealed_count = len(game["revealed"])
        if revealed_count == 0:
            await query.answer("❌ Kam se kam 1 box toh kholo!", show_alert=True)
            return
            
        mult = MULTIPLIERS[revealed_count]
        winnings = int(game["bet"] * mult)
        update_balance(user_id, winnings)
        
        del active_games[game_id]
        await query.edit_message_text(f"🤑 **CASHOUT SUCCESSFUL!**\n\nWon: ₹{winnings}\nMultiplier: {mult}x")
        return

    if data.startswith("click_"):
        index = int(data.split("_")[1])
        
        if game["grid"][index] == 1:
            # BOMB PHATA 💥
            del active_games[game_id]
            # Grid dikhao (Game over mode)
            game["revealed"].append(index) # Show clicked mine
            await query.edit_message_text(
                "💥 **BOOM! Khel Khatam!**\n\nAapke paise dub gaye. 😭", 
                reply_markup=get_keyboard(game, game_over=True)
            )
        else:
            # DIAMOND 💎
            game["revealed"].append(index)
            
            # Check Max Win (Agar saare diamonds dhund liye)
            safe_cells = (GRID_SIZE * GRID_SIZE) - TOTAL_BOMBS
            if len(game["revealed"]) == safe_cells:
                # Jackpot
                mult = MULTIPLIERS[-1]
                winnings = int(game["bet"] * mult)
                update_balance(user_id, winnings)
                del active_games[game_id]
                await query.edit_message_text(f"👑 **JACKPOT! YOU WON MAX PRIZE!**\n\nWon: ₹{winnings}")
            else:
                # Continue Game
                await query.edit_message_text(
                    f"💎 **Safe!** Multiplier: {MULTIPLIERS[len(game['revealed'])]}x",
                    reply_markup=get_keyboard(game)
                )

# --- MAIN ---
def main():
    keep_alive()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("daily", daily))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("pay", pay))
    app.add_handler(CommandHandler("play", play_game))
    app.add_handler(CallbackQueryHandler(game_callback))
    
    print("Bot is ready...")
    app.run_polling()

if __name__ == "__main__":
    main()
  
