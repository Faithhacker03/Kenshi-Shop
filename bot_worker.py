# -*- coding: utf-8 -*-
"""
Kenshi Shop - Standalone Telegram Bot Worker
- This script is designed to run as a separate 'worker' process on Heroku.
- It connects directly to MongoDB and handles all Telegram interactions.
- It does NOT share memory or import from the main Flask app.
"""

import os
import time
import pymongo
import telebot

# ==============================================================================
# SERVICE CONFIGURATION & INITIALIZATION
# ==============================================================================

# 1. Load all necessary environment variables
MONGO_URI = os.environ.get('MONGO_URI')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
ADMIN_TELEGRAM_CHAT_ID = os.environ.get('ADMIN_TELEGRAM_CHAT_ID')

# 2. Check if essential variables are set
if not all([MONGO_URI, TELEGRAM_BOT_TOKEN]):
    print("FATAL ERROR: MONGO_URI and TELEGRAM_BOT_TOKEN must be set in environment variables.")
    exit()

# 3. Initialize Database Connection
try:
    db_client = pymongo.MongoClient(MONGO_URI)
    db = db_client.get_default_database()
    orders_collection = db.orders
    print("Bot worker successfully connected to MongoDB Atlas.")
except Exception as e:
    print(f"FATAL ERROR: Could not connect to MongoDB. Reason: {e}")
    exit()

# 4. Initialize Telegram Bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# ==============================================================================
# TELEGRAM BOT HANDLERS
# ==============================================================================

@bot.message_handler(commands=['start'])
def send_welcome(message):
    """Handles the /start command."""
    bot.reply_to(message, "Welcome to Kenshi Shop! To link an order, please find your unique /claim command on the order page of our website.")

@bot.message_handler(func=lambda message: message.text and message.text.lower().startswith('/claim'))
def claim_order(message):
    """Handles the /claim <code> command."""
    try:
        # Extract the claim code from the message text
        claim_code = message.text.split()[1].upper().strip()
    except IndexError:
        # Handle cases where the user just types "/claim"
        bot.reply_to(message, "❌ Error: Invalid command format. Please use the format: /claim YOUR_CODE")
        return

    try:
        # Directly query the database to find the order with the matching claim code
        order = orders_collection.find_one({'claim_code': claim_code})

        if order:
            # Prepare the updates for the database
            updates = {
                'buyer_chat_id': message.chat.id,
                'buyer_username': message.from_user.username
            }
            # Atomically update the order document in MongoDB
            orders_collection.update_one({'_id': order['_id']}, {'$set': updates})
            
            # Confirm success with the user
            bot.reply_to(message, f"✅ Success! Your Telegram account has been linked to the order for '{order['product_name']}'. You will receive your file here as soon as payment is approved.")
            
            # Notify the admin, if an admin chat ID is configured
            if ADMIN_TELEGRAM_CHAT_ID:
                admin_msg = (f"🔗 **Order Linked**\n\n"
                             f"**Product:** {order['product_name']}\n"
                             f"**Buyer:** @{message.from_user.username or 'N/A'}")
                bot.send_message(ADMIN_TELEGRAM_CHAT_ID, admin_msg, parse_mode='Markdown')
        else:
            # Inform the user if the claim code is invalid
            bot.reply_to(message, "❌ Error: Invalid claim code. Please copy the command exactly from your order page.")
    except Exception as e:
        print(f"CRITICAL ERROR in claim_order: {e}")
        bot.reply_to(message, "An unexpected server error occurred. Please try again or contact support.")

def run_bot_polling():
    """A robust function to keep the bot running continuously."""
    print("Starting Telegram bot listener...")
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            # Catch exceptions from network issues or API changes
            print(f"Bot polling error: {e}. Restarting in 10 seconds.")
            time.sleep(10)

if __name__ == "__main__":
    run_bot_polling()