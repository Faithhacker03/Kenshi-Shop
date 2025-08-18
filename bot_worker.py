# -*- coding: utf-8 -*-
import time
import os
from deta import Deta
from app import bot, load_data_from_deta, orders, ADMIN_TELEGRAM_CHAT_ID

# --- Deta.sh Configuration ---
try:
    deta = Deta(os.environ.get('DETA_PROJECT_KEY'))
    orders_db = deta.Base("orders")
except Exception as e:
    print(f"FATAL: Deta Project Key not found or invalid. Bot cannot start. Error: {e}")
    deta = None
    exit()

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Welcome to Kenshi Shop! To link an order, please find your unique /claim command on the order page of our website.")

@bot.message_handler(func=lambda message: message.text and message.text.lower().startswith('/claim'))
def claim_order(message):
    try:
        claim_code = message.text.split()[1].upper().strip()
        # Find the order by claim code (we iterate since we don't have a direct map for this)
        order_id = next((oid for oid, odata in orders.items() if odata.get('claim_code') == claim_code), None)

        if order_id:
            order = orders[order_id]
            updates = {
                'buyer_chat_id': message.chat.id,
                'buyer_username': message.from_user.username
            }
            # Update the order in Deta Base
            orders_db.update(updates, key=order_id)
            # Update local cache
            order.update(updates)

            bot.reply_to(message, f"✅ Success! Your Telegram account has been linked to the order for '{order['product_name']}'.\n\nYou will receive your download link here as soon as the admin approves your payment.")
            if ADMIN_TELEGRAM_CHAT_ID:
                admin_msg = (f"🔗 **Order Linked**\n\n"
                             f"**Product:** {order['product_name']}\n"
                             f"**Claim Code:** {claim_code}\n"
                             f"**Buyer:** @{message.from_user.username or 'N/A'}")
                bot.send_message(ADMIN_TELEGRAM_CHAT_ID, admin_msg, parse_mode='Markdown')
        else:
            bot.reply_to(message, "❌ Error: Invalid claim code. Please copy the command exactly from your order page.")
    except Exception as e:
        print(f"Error in claim_order: {e}")
        bot.reply_to(message, "An unexpected error occurred.")

def run_bot():
    print("Starting Telegram bot listener...")
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            print(f"Bot polling failed with error: {e}. Restarting in 10 seconds.")
            time.sleep(10)

if __name__ == '__main__':
    print("Loading data for bot worker...")
    load_data_from_deta()
    run_bot()