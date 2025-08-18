# -*- coding: utf-8 -*-
"""
Kenshi Shop - Telegram Bot Worker

This script is intended to be run as a separate process on the hosting platform (e.g., a Render Background Worker).
It loads the application data and runs the Telegram bot listener.
"""
import time
from app import bot, load_data, save_data, orders, claim_codes, ADMIN_TELEGRAM_CHAT_ID

# Ensure the bot object exists before setting handlers
if not bot:
    print("FATAL: Telegram bot token not provided. Bot worker cannot start.")
    exit()

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Welcome to Kenshi Shop! To link an order, please find your unique /claim command on the order page of our website.")

@bot.message_handler(func=lambda message: message.text and message.text.lower().startswith('/claim'))
def claim_order(message):
    try:
        claim_code = message.text.split()[1].upper().strip()
        if claim_code in claim_codes:
            order_id = claim_codes[claim_code]
            order = orders.get(order_id)
            if order:
                order['buyer_chat_id'] = message.chat.id
                order['buyer_username'] = message.from_user.username
                save_data()
                bot.reply_to(message, f"✅ Success! Your Telegram account has been linked to the order for '{order['product_name']}'.\n\nYou will receive your download link here as soon as the admin approves your payment.")
                admin_msg = (f"🔗 **Order Linked**\n\n"
                             f"**Product:** {order['product_name']}\n"
                             f"**Claim Code:** {claim_code}\n"
                             f"**Buyer:** @{message.from_user.username or 'N/A'}\n\n"
                             f"The buyer is waiting for approval.")
                if ADMIN_TELEGRAM_CHAT_ID:
                    bot.send_message(ADMIN_TELEGRAM_CHAT_ID, admin_msg, parse_mode='Markdown')
            else:
                bot.reply_to(message, "❌ Error: That order could not be found. Please contact support.")
        else:
            bot.reply_to(message, "❌ Error: Invalid claim code. Please copy the command exactly from your order page.")
    except IndexError:
        bot.reply_to(message, "❌ Error: Invalid command format. Please use the format: /claim YOUR_CODE")
    except Exception as e:
        print(f"Error in claim_order: {e}")
        bot.reply_to(message, "An unexpected error occurred. Please try again.")

def run_bot():
    """Function to run the bot listener in a continuous loop."""
    print("Starting Telegram bot listener...")
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            print(f"Bot polling failed with error: {e}. Restarting in 10 seconds.")
            time.sleep(10)

if __name__ == '__main__':
    print("Loading data for bot worker...")
    load_data()
    run_bot()