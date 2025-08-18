# -*- coding: utf-8 -*-
"""
Kenshi Shop - Final Professional Version (Heroku, MongoDB, Cloudinary Stack)
"""

import os
import uuid
import re
import time
import io
import pymongo
import cloudinary
import cloudinary.uploader
import cloudinary.api
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, abort, Response, flash
)
from werkzeug.utils import secure_filename
import telebot

# ==============================================================================
# FLASK APP & SERVICE CONFIGURATION
# ==============================================================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# --- MongoDB Atlas Configuration ---
MONGO_URI = os.environ.get('MONGO_URI')
db_client = pymongo.MongoClient(MONGO_URI)
db = db_client.get_default_database() # DB name is in the URI
products_collection = db.products
orders_collection = db.orders

# --- Cloudinary Configuration ---
cloudinary.config(
  cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME'),
  api_key = os.environ.get('CLOUDINARY_API_KEY'),
  api_secret = os.environ.get('CLOUDINARY_API_SECRET'),
  secure = True
)

# --- Admin, Payment, & Telegram Config ---
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD')
PAYMENT_DETAILS = {"gcash": "0912-345-6789", "paymaya": "0998-765-4321"}
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
ADMIN_TELEGRAM_CHAT_ID = os.environ.get('ADMIN_TELEGRAM_CHAT_ID')
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None

# ==============================================================================
# DATA CACHING & HELPERS
# ==============================================================================
products, orders, slug_to_id_map = {}, {}, {}
currency_cache = {'rate': 58.0, 'last_updated': 0}

def load_data_from_db():
    global products, orders, slug_to_id_map
    print("Loading data from MongoDB Atlas...")
    product_items = list(products_collection.find({}))
    products = {str(item['_id']): item for item in product_items}
    for pid, pdata in products.items():
        if 'slug' in pdata: slug_to_id_map[pdata['slug']] = pid

    order_items = list(orders_collection.find({}))
    orders = {str(item['_id']): item for item in order_items}
    print(f"Data loaded: {len(products)} products, {len(orders)} orders.")

def get_usd_to_php_rate():
    global currency_cache
    if time.time() - currency_cache['last_updated'] > 3600:
        try:
            data = requests.get('https://api.exchangerate-api.com/v4/latest/USD').json()
            currency_cache['rate'] = data['rates']['PHP']
            currency_cache['last_updated'] = time.time()
        except Exception as e:
            print(f"Could not fetch currency rate: {e}")
    return currency_cache['rate']

def create_slug(product_name, existing_product_id=None):
    s = re.sub(r'[^a-z0-9-]', '-', product_name.lower().strip()).strip('-')
    s = re.sub(r'-+', '-', s)
    if not s: s = "product"
    colliding_id = slug_to_id_map.get(s)
    if colliding_id is not None and colliding_id != existing_product_id:
        return f"{s}-{str(uuid.uuid4())[:4]}"
    return s

@app.before_request
def check_admin_auth():
    admin_paths = ['/admin/dashboard', '/admin/add', '/admin/approve', '/admin/manage_products', '/admin/edit', '/admin/delete']
    if any(request.path.startswith(p) for p in admin_paths) and 'admin_logged_in' not in session:
        return redirect(url_for('admin_login'))

# ==============================================================================
# USER ROUTES
# ==============================================================================
@app.route('/')
def index():
    available = [p for p in products.values() if p['status'] == 'available']
    categorized = {
        'tools': [p for p in available if p.get('category') == 'tools'],
        'web_checkers': [p for p in available if p.get('category') == 'web_checker'],
        'ml_accounts': [p for p in available if p.get('category') == 'ml_account'],
        'codm_active': [p for p in available if p.get('category') == 'codm' and p.get('sub_category') == 'active'],
        'codm_semi_active': [p for p in available if p.get('category') == 'codm' and p.get('sub_category') == 'semi-active'],
        'codm_inactive': [p for p in available if p.get('category') == 'codm' and p.get('sub_category') == 'inactive'],
        'freebies': [p for p in available if p.get('category') == 'freebies']
    }
    return render_template('index.html', categorized_products=categorized, php_rate=get_usd_to_php_rate())

@app.route('/proofs')
def proofs():
    proof_orders = sorted([o for o in orders.values() if o.get('is_proof') and o.get('receipt_url')], key=lambda x: x.get('timestamp', 0), reverse=True)
    return render_template('proofs.html', proofs=proof_orders)

@app.route('/product/<slug>', methods=['GET', 'POST'])
def product_page(slug):
    product_id = slug_to_id_map.get(slug)
    if not product_id or product_id not in products: abort(404)
    product = products[product_id]
    if product['status'] != 'available': abort(404)

    if request.method == 'POST':
        order_id = str(uuid.uuid4())
        new_order = {
            '_id': order_id, 'product_id': product['_id'], 'product_name': product['name'],
            'price': product['price'], 'payment_method': request.form['payment_method'], 'status': 'unpaid',
            'timestamp': time.time(), 'claim_code': f"CLAIM-{order_id.split('-')[0].upper()}"
        }
        orders_collection.insert_one(new_order)
        orders[order_id] = new_order
        products_collection.update_one({'_id': product['_id']}, {'$set': {'status': 'pending'}})
        products[product_id]['status'] = 'pending'
        return redirect(url_for('order_status', order_id=order_id))
    return render_template('product.html', product=product, php_rate=get_usd_to_php_rate())

@app.route('/order/<order_id>')
def order_status(order_id):
    order = orders.get(order_id)
    if not order: abort(404)
    return render_template('order_status.html', order=order, payment_details=PAYMENT_DETAILS, php_rate=get_usd_to_php_rate(), bot_username="KenshiShop_Bot")

@app.route('/submit_payment/<order_id>', methods=['POST'])
def submit_payment(order_id):
    order = orders.get(order_id)
    if not order or order['status'] != 'unpaid': abort(404)
    receipt_image = request.files.get('receipt_image')
    if receipt_image and receipt_image.filename:
        upload_result = cloudinary.uploader.upload(receipt_image, folder="kenshi-shop/receipts")
        updates = {
            'receipt_url': upload_result['secure_url'],
            'receipt_public_id': upload_result['public_id'],
            'status': 'pending'
        }
        orders_collection.update_one({'_id': order_id}, {'$set': updates})
        order.update(updates)
        if bot and ADMIN_TELEGRAM_CHAT_ID:
            try: bot.send_message(ADMIN_TELEGRAM_CHAT_ID, f"🔔 New payment for **{order['product_name']}**. Please review.", parse_mode='Markdown')
            except Exception as e: print(f"Telegram notification failed: {e}")
    return redirect(url_for('order_status', order_id=order_id))

@app.route('/download/<token>')
def download_file(token):
    order = orders_collection.find_one({'download_token': token})
    if not order: abort(404)
    product = products.get(order['product_id'])
    if not product: abort(404)
    if order['status'] == 'approved':
        orders_collection.update_one({'_id': order['_id']}, {'$set': {'status': 'completed'}})
        if order['_id'] in orders: orders[order['_id']]['status'] = 'completed'
    return redirect(product['script_url'])

# ==============================================================================
# ADMIN ROUTES
# ==============================================================================
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST' and request.form['password'] == ADMIN_PASSWORD:
        session['admin_logged_in'] = True
        return redirect(url_for('admin_dashboard'))
    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('index'))

@app.route('/admin/dashboard')
def admin_dashboard():
    pending_orders = sorted([o for o in orders.values() if o['status'] == 'pending'], key=lambda x: x.get('timestamp', 0), reverse=True)
    return render_template('admin/dashboard.html', orders=pending_orders)

@app.route('/admin/manage_products')
def manage_products():
    all_prods = sorted(list(products.values()), key=lambda x: x.get('name', ''))
    return render_template('admin/manage_products.html', products=all_prods)

@app.route('/admin/add', methods=['GET', 'POST'])
def add_product():
    if request.method == 'POST':
        product_id = str(uuid.uuid4())
        name = request.form['name']
        new_product = {
            '_id': product_id, 'name': name, 'slug': create_slug(name),
            'price': request.form.get('price'), 'status': 'available',
            'category': request.form.get('category'),
            'description': request.form.get('description', ''),
            'bonus_freebies': [line.strip() for line in request.form.get('bonus_freebies', '').splitlines() if line.strip()]
        }
        if new_product['category'] == 'codm': new_product['sub_category'] = request.form.get('sub_category')
        
        image = request.files.get('image')
        if image and image.filename:
            upload_result = cloudinary.uploader.upload(image, folder="kenshi-shop/products", public_id=f"img_{product_id}")
            new_product['image_url'] = upload_result['secure_url']
            new_product['image_public_id'] = upload_result['public_id']
        else:
            flash("Product image is required.", "error"); return redirect(url_for('add_product'))

        script_zip = request.files.get('script')
        if script_zip and script_zip.filename:
            upload_result = cloudinary.uploader.upload(script_zip, folder="kenshi-shop/secure_files", resource_type="raw", public_id=f"script_{product_id}")
            new_product['script_url'] = upload_result['secure_url']
            new_product['script_public_id'] = upload_result['public_id']

        products_collection.insert_one(new_product)
        products[product_id] = new_product
        slug_to_id_map[new_product['slug']] = product_id
        flash(f"Successfully added '{name}'!", "success")
        return redirect(url_for('manage_products'))
    return render_template('admin/add_product.html')

@app.route('/admin/edit/<product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    product = products.get(product_id)
    if not product: abort(404)
    if request.method == 'POST':
        original_slug = product.get('slug')
        updates = {
            'name': request.form.get('name'), 'description': request.form.get('description'),
            'price': request.form.get('price'), 'status': request.form.get('status'),
            'bonus_freebies': [line.strip() for line in request.form.get('bonus_freebies', '').splitlines() if line.strip()]
        }
        new_slug = create_slug(updates['name'], existing_product_id=product_id)
        if new_slug != original_slug:
            updates['slug'] = new_slug
            if original_slug in slug_to_id_map: del slug_to_id_map[original_slug]
            slug_to_id_map[new_slug] = product_id

        products_collection.update_one({'_id': product_id}, {'$set': updates})
        product.update(updates)
        flash(f"Successfully updated '{updates['name']}'!", "success")
        return redirect(url_for('manage_products'))
    return render_template('admin/edit_product.html', product=product)

@app.route('/admin/delete/<product_id>', methods=['POST'])
def delete_product(product_id):
    product = products.get(product_id)
    if not product: abort(404)
    try:
        if product.get('image_public_id'):
            cloudinary.uploader.destroy(product['image_public_id'])
        if product.get('script_public_id'):
            cloudinary.uploader.destroy(product['script_public_id'], resource_type="raw")
        products_collection.delete_one({'_id': product_id})
        slug = product.get('slug')
        if slug in slug_to_id_map: del slug_to_id_map[slug]
        del products[product_id]
        flash(f"Successfully deleted product.", "success")
    except Exception as e:
        flash(f"An error occurred: {e}", "error")
    return redirect(url_for('manage_products'))

@app.route('/admin/approve/<order_id>', methods=['POST'])
def approve_order(order_id):
    order = orders.get(order_id)
    if not order or order['status'] != 'pending': return redirect(url_for('admin_dashboard'))

    products_collection.update_one({'_id': order['product_id']}, {'$set': {'status': 'available'}})
    products[order['product_id']]['status'] = 'available'
    updates = {
        'status': 'approved', 'download_token': str(uuid.uuid4()),
        'is_proof': request.form.get('mark_as_proof') == 'on'
    }
    orders_collection.update_one({'_id': order_id}, {'$set': updates})
    order.update(updates)
    
    if bot:
        try:
            link = url_for('download_file', token=updates['download_token'], _external=True)
            buyer_msg = f"✅ **Order Approved!**\n\nYour download for **{order['product_name']}** is ready:\n{link}"
            admin_msg = f"🚀 **Order Approved & Delivered!**\n\n**Product:** {order['product_name']}"
            if order.get('buyer_chat_id'):
                bot.send_message(order['buyer_chat_id'], buyer_msg, parse_mode='Markdown')
                if ADMIN_TELEGRAM_CHAT_ID: bot.send_message(ADMIN_TELEGRAM_CHAT_ID, admin_msg, parse_mode='Markdown')
            else:
                if ADMIN_TELEGRAM_CHAT_ID: bot.send_message(ADMIN_TELEGRAM_CHAT_ID, f"⚠️ **Order Approved - Action Required!**\n\nBuyer for **{order['product_name']}** has not linked their Telegram. Send link manually:\n{link}", parse_mode='Markdown')
        except Exception as e:
            print(f"Telegram approval notification failed: {e}")
    return redirect(url_for('admin_dashboard'))

# ==============================================================================
# APPLICATION RUNNER
# ==============================================================================
if __name__ == '__main__':
    load_data_from_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)```

### 5. `bot_worker.py`

This self-contained script runs as a separate `worker` process on Heroku.

```python
import os
import time
import pymongo
import telebot

# --- Service Configuration ---
MONGO_URI = os.environ.get('MONGO_URI')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
ADMIN_TELEGRAM_CHAT_ID = os.environ.get('ADMIN_TELEGRAM_CHAT_ID')

# --- DB & Bot Initialization ---
db_client = pymongo.MongoClient(MONGO_URI)
db = db_client.get_default_database()
orders_collection = db.orders
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Welcome to Kenshi Shop! To link an order, find your unique /claim command on the order page of our website.")

@bot.message_handler(func=lambda message: message.text and message.text.lower().startswith('/claim'))
def claim_order(message):
    try:
        claim_code = message.text.split()[1].upper().strip()
        order = orders_collection.find_one({'claim_code': claim_code})

        if order:
            updates = {
                'buyer_chat_id': message.chat.id,
                'buyer_username': message.from_user.username
            }
            orders_collection.update_one({'_id': order['_id']}, {'$set': updates})
            bot.reply_to(message, f"✅ Success! Your Telegram account has been linked to the order for '{order['product_name']}'. You will receive your file here once payment is approved.")
            
            if ADMIN_TELEGRAM_CHAT_ID:
                admin_msg = (f"🔗 **Order Linked**\n\n"
                             f"**Product:** {order['product_name']}\n"
                             f"**Buyer:** @{message.from_user.username or 'N/A'}")
                bot.send_message(ADMIN_TELEGRAM_CHAT_ID, admin_msg, parse_mode='Markdown')
        else:
            bot.reply_to(message, "❌ Error: Invalid claim code. Please copy the command exactly from your order page.")
    except IndexError:
        bot.reply_to(message, "❌ Error: Invalid format. Please use: /claim YOUR_CODE")
    except Exception as e:
        print(f"Error in claim_order: {e}")
        bot.reply_to(message, "An unexpected error occurred. Please try again.")

def run_bot():
    print("Starting Telegram bot listener...")
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            print(f"Bot polling error: {e}. Restarting in 10 seconds.")
            time.sleep(10)

if __name__ == "__main__":
    run_bot()