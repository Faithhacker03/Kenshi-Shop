# -*- coding: utf-8 -*-
"""
Kenshi Shop - Final Professional Version (Render-Ready)

Features:
- ... (all existing features)
- NEW: Production-ready configuration for Render.
- NEW: All persistent data is stored on a mounted disk.
- NEW: A dedicated route to serve files from the persistent disk.
"""

import os
import uuid
import re
import zipfile
import time
import requests
import telebot
import json
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, abort, send_from_directory, flash
)
from werkzeug.utils import secure_filename

# ==============================================================================
# FLASK APP SETUP & CONFIGURATION
# ==============================================================================
app = Flask(__name__)

# --- Core Config ---
# RENDER DEPLOYMENT: The DATA_FOLDER is now the primary path for ALL persistent storage.
# Render will mount a persistent disk at the path specified in the DATA_FOLDER_PATH env var.
DATA_FOLDER = os.environ.get('DATA_FOLDER_PATH', 'data/')

app.config.update(
    SECRET_KEY=os.environ.get('SECRET_KEY', 'your-super-secret-key-that-no-one-will-guess'),
    DATA_FOLDER=DATA_FOLDER,
    # ALL PERSISTENT FOLDERS ARE NOW SUBDIRECTORIES OF THE MAIN DATA_FOLDER
    UPLOAD_FOLDER=os.path.join(DATA_FOLDER, 'static/uploads/'),
    SECURE_FILES_FOLDER=os.path.join(DATA_FOLDER, 'secure_files/'),
    TEMP_DOWNLOAD_FOLDER=os.path.join(DATA_FOLDER, 'temp_downloads/'),
    RECEIPT_FOLDER=os.path.join(DATA_FOLDER, 'static/receipts/'),
    MAX_CONTENT_LENGTH=16 * 1024 * 1024
)

# --- Admin & Payment Config (Loaded from environment variables for security) ---
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', "admin")
PAYMENT_DETAILS = {"gcash": "0912-345-6789", "paymaya": "0998-765-4321"}

# --- TELEGRAM BOT CONFIGURATION (Loaded from environment variables) ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
ADMIN_TELEGRAM_CHAT_ID = os.environ.get('ADMIN_TELEGRAM_CHAT_ID')

# ==============================================================================
# DATA STORAGE & OTHER SETUP
# ==============================================================================
products, orders, slug_to_id_map = {}, {}, {}
currency_cache = {'rate': 58.0, 'last_updated': 0}
claim_codes = {}

# Initialize bot only if the token is provided
bot = None
if TELEGRAM_BOT_TOKEN:
    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
else:
    print("Warning: TELEGRAM_BOT_TOKEN not set. Telegram features will be disabled.")


# Create all necessary folders on startup
for folder_key in [
    'UPLOAD_FOLDER', 'SECURE_FILES_FOLDER', 'TEMP_DOWNLOAD_FOLDER', 'RECEIPT_FOLDER'
]:
    os.makedirs(app.config[folder_key], exist_ok=True)

# JSON files are also stored in the main data folder
PRODUCTS_FILE = os.path.join(app.config['DATA_FOLDER'], 'products.json')
ORDERS_FILE = os.path.join(app.config['DATA_FOLDER'], 'orders.json')


# ==============================================================================
# DATA PERSISTENCE FUNCTIONS
# ==============================================================================
def save_data():
    """Saves products and orders to JSON files."""
    try:
        with open(PRODUCTS_FILE, 'w') as f:
            json.dump(products, f, indent=4)
        with open(ORDERS_FILE, 'w') as f:
            json.dump(orders, f, indent=4)
        print("Data saved successfully.")
    except Exception as e:
        print(f"ERROR: Could not save data. Reason: {e}")


def load_data():
    """Loads products and orders from JSON files at startup."""
    global products, orders, slug_to_id_map, claim_codes
    try:
        if os.path.exists(PRODUCTS_FILE):
            with open(PRODUCTS_FILE, 'r') as f:
                products = json.load(f)
                for pid, pdata in products.items():
                    if 'slug' in pdata:
                        slug_to_id_map[pdata['slug']] = pid
        if os.path.exists(ORDERS_FILE):
            with open(ORDERS_FILE, 'r') as f:
                orders = json.load(f)
                for oid, odata in orders.items():
                    if odata.get('status') in ['unpaid', 'pending'] and 'claim_code' in odata:
                        claim_codes[odata['claim_code']] = oid
        print("Data loaded successfully.")
    except Exception as e:
        print(f"ERROR: Could not load data. Reason: {e}")

# ==============================================================================
# HELPER FUNCTIONS & MIDDLEWARE
# ==============================================================================
def get_usd_to_php_rate():
    """Fetches and caches the USD to PHP exchange rate."""
    global currency_cache
    one_hour = 3600
    now = time.time()
    if now - currency_cache['last_updated'] > one_hour:
        try:
            response = requests.get('https://api.exchangerate-api.com/v4/latest/USD')
            response.raise_for_status()
            data = response.json()
            currency_cache['rate'] = data['rates']['PHP']
            currency_cache['last_updated'] = now
            print(f"Currency rate updated: 1 USD = {currency_cache['rate']} PHP")
        except requests.exceptions.RequestException as e:
            print(f"Warning: Could not fetch currency rate. Error: {e}")
    return currency_cache['rate']


def create_slug(product_name, existing_product_id=None):
    """
    Creates a unique, URL-friendly slug.
    Now accepts an optional product_id to ignore self-collision during edits.
    """
    s = product_name.lower().strip()
    s = re.sub(r'[^a-z0-9-]', '-', s)
    s = re.sub(r'-+', '-', s).strip('-')
    if not s:
        s = "product"

    colliding_id = slug_to_id_map.get(s)
    if colliding_id is not None and colliding_id != existing_product_id:
        return f"{s}-{str(uuid.uuid4())[:4]}"
    
    return s


@app.before_request
def check_admin_auth():
    """Protects admin-only routes."""
    admin_paths = ['/admin/dashboard', '/admin/add', '/admin/approve', '/admin/manage_products', '/admin/edit', '/admin/delete']
    if any(request.path.startswith(p) for p in admin_paths):
        if 'admin_logged_in' not in session:
            return redirect(url_for('admin_login'))


# NEW ROUTE: To serve files (images, receipts) from the persistent data folder
@app.route('/data/<path:filename>')
def serve_data_file(filename):
    """Serves files from the persistent disk."""
    return send_from_directory(app.config['DATA_FOLDER'], filename)


# ==============================================================================
# USER & INTEGRATED ADMIN ROUTES
# ==============================================================================
@app.route('/')
def index():
    """Displays the categorized homepage."""
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
    return render_template(
        'index.html',
        categorized_products=categorized,
        php_rate=get_usd_to_php_rate()
    )


@app.route('/proofs')
def proofs():
    """Displays a page with proof of successful transactions."""
    proof_orders = sorted(
        [o for o in orders.values() if o.get('is_proof') and o.get('status') == 'completed' and o.get('receipt_filename')],
        key=lambda x: x.get('timestamp', 0), reverse=True
    )
    return render_template('proofs.html', proofs=proof_orders)


@app.route('/product/<slug>', methods=['GET', 'POST'])
def product_page(slug):
    """Displays a single product page and initiates an order."""
    product_id = slug_to_id_map.get(slug)
    if not product_id:
        abort(404)

    product = products.get(product_id)
    if not product or product['status'] != 'available':
        abort(404)

    if request.method == 'POST':
        order_id = str(uuid.uuid4())
        orders[order_id] = {
            'id': order_id, 'product_id': product['id'], 'product_name': product['name'],
            'price': product['price'], 'payment_method': request.form['payment_method'],
            'status': 'unpaid', 'receipt_filename': None, 'buyer_chat_id': None,
            'buyer_username': None, 'timestamp': time.time(), 'is_proof': False # New field
        }
        claim_code = f"CLAIM-{order_id.split('-')[0].upper()}"
        claim_codes[claim_code] = order_id
        orders[order_id]['claim_code'] = claim_code
        product['status'] = 'pending'
        save_data()
        return redirect(url_for('order_status', order_id=order_id))

    return render_template('product.html', product=product, php_rate=get_usd_to_php_rate())


@app.route('/order/<order_id>')
def order_status(order_id):
    """Displays the customer's private order status page."""
    order = orders.get(order_id)
    if not order:
        abort(404)
    return render_template(
        'order_status.html',
        order=order, payment_details=PAYMENT_DETAILS,
        php_rate=get_usd_to_php_rate(), bot_username="KenshiShop_Bot"
    )


@app.route('/submit_payment/<order_id>', methods=['POST'])
def submit_payment(order_id):
    """Handles the payment receipt upload and notifies admin."""
    order = orders.get(order_id)
    if not order or order['status'] != 'unpaid':
        abort(404)

    receipt_image = request.files.get('receipt_image')
    if receipt_image and receipt_image.filename != '':
        filename = secure_filename(f"{order_id}_{receipt_image.filename}")
        # Save to the correct persistent folder
        receipt_image.save(os.path.join(app.config['RECEIPT_FOLDER'], filename))
        # Store relative path for the new serving route
        order['receipt_filename'] = os.path.join('static/receipts', filename)
        order['status'] = 'pending'
        save_data()
        
        if bot and ADMIN_TELEGRAM_CHAT_ID:
            try:
                admin_message = (
                    f"🔔 **New Order for Review**\n\n"
                    f"A payment receipt has been uploaded for:\n"
                    f"**{order['product_name']}**\n\n"
                    f"Please go to your dashboard to review and approve."
                )
                bot.send_message(ADMIN_TELEGRAM_CHAT_ID, admin_message, parse_mode='Markdown')
            except Exception as e:
                print(f"TELEGRAM ADMIN NOTIFICATION FAILED for order {order_id}. Error: {e}")

    return redirect(url_for('order_status', order_id=order_id))


@app.route('/download/<token>')
def download_file(token):
    """Serves the final downloadable ZIP file."""
    order = next((o for o in orders.values() if o.get('download_token') == token), None)
    if not order:
        abort(404, "Invalid or expired download link.")
    product = products.get(order['product_id'])
    if not product:
        abort(404, "The product for this order could not be found.")
    
    download_filename = f"{order['id']}.zip"
    if order['status'] == 'approved':
        order['status'] = 'completed'
        save_data()

    return send_from_directory(
        app.config['TEMP_DOWNLOAD_FOLDER'],
        download_filename, as_attachment=True
    )

# ==============================================================================
# ADMIN-SPECIFIC ROUTES
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
    pending_orders = sorted(
        [o for o in orders.values() if o['status'] == 'pending'],
        key=lambda x: x.get('timestamp', 0), reverse=True
    )
    return render_template('admin/dashboard.html', orders=pending_orders)

@app.route('/admin/manage_products')
def manage_products():
    all_products = sorted(list(products.values()), key=lambda x: x.get('name', ''))
    return render_template('admin/manage_products.html', products=all_products)

@app.route('/admin/add', methods=['GET', 'POST'])
def add_product():
    if request.method == 'POST':
        product_id = str(uuid.uuid4())
        name = request.form['name']
        slug = create_slug(name)
        category = request.form.get('category')
        
        new_product = {
            'id': product_id, 'slug': slug, 'name': name,
            'price': request.form.get('price'), 'status': 'available',
            'category': category
        }
        
        image = request.files.get('image')
        if image and image.filename != '':
            img_filename = secure_filename(f"{product_id}_{image.filename}")
            # Save to the correct persistent folder
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], img_filename))
            # Store relative path for the new serving route
            new_product['image_filename'] = os.path.join('static/uploads', img_filename)
        else:
            flash("Error: A product image is required.", "error")
            return redirect(url_for('add_product'))

        description = request.form.get('description', '')
        freebies_text = request.form.get('bonus_freebies', '')
        new_product['bonus_freebies'] = [line.strip() for line in freebies_text.splitlines() if line.strip()]

        if category == 'codm':
            new_product['sub_category'] = request.form.get('sub_category')
        elif category == 'web_checker':
            checker_type = request.form.get('checker_type')
            new_product['checker_type'] = checker_type
            if checker_type == 'website_link':
                new_product['website_link'] = request.form.get('website_link')
                new_product['expiration_days'] = request.form.get('expiration_days')
                new_product['script_filename'] = None
                if not description:
                    description = f"Website Access for {new_product['name']}.\nExpires in {new_product['expiration_days']} day(s)."

        if new_product.get('checker_type') != 'website_link':
            script_zip = request.files.get('script')
            if script_zip and script_zip.filename != '':
                if not description:
                    try:
                        with zipfile.ZipFile(script_zip.stream, 'r') as zf:
                            if 'features.txt' in zf.namelist():
                                description = zf.read('features.txt').decode('utf-8', errors='ignore')
                    except Exception as e:
                        print(f"Warning: Could not read features.txt. Error: {e}")
                        description = "No feature list found in ZIP."
                    script_zip.stream.seek(0)
                
                s_name = secure_filename(name).lower().replace('_', '-')
                ext = os.path.splitext(script_zip.filename)[1]
                s_script_filename = f"{product_id}_{s_name}{ext}"
                script_zip.save(os.path.join(app.config['SECURE_FILES_FOLDER'], s_script_filename))
                new_product['script_filename'] = s_script_filename
            else:
                 flash("Error: A product file (ZIP) is required for this product type.", "error")
                 return redirect(url_for('add_product'))

        new_product['description'] = description
        products[product_id] = new_product
        slug_to_id_map[slug] = product_id
        save__data()
        flash(f"Successfully added '{name}'!", "success")
        return redirect(url_for('manage_products'))

    return render_template('admin/add_product.html')

@app.route('/admin/edit/<product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    product = products.get(product_id)
    if not product:
        abort(404)

    if request.method == 'POST':
        original_slug = product.get('slug')
        product['name'] = request.form.get('name')
        product['description'] = request.form.get('description')
        product['price'] = request.form.get('price')
        product['status'] = request.form.get('status')
        freebies_text = request.form.get('bonus_freebies', '')
        product['bonus_freebies'] = [line.strip() for line in freebies_text.splitlines() if line.strip()]
        
        new_slug = create_slug(product['name'], existing_product_id=product_id)
        if new_slug != original_slug:
            product['slug'] = new_slug
            if original_slug and original_slug in slug_to_id_map:
                del slug_to_id_map[original_slug]
            slug_to_id_map[new_slug] = product['id']

        save_data()
        flash(f"Successfully updated '{product['name']}'!", "success")
        return redirect(url_for('manage_products'))

    return render_template('admin/edit_product.html', product=product)

@app.route('/admin/delete/<product_id>', methods=['POST'])
def delete_product(product_id):
    """Deletes a product and its associated files."""
    product = products.get(product_id)
    if not product:
        flash("Product not found.", "error")
        return redirect(url_for('manage_products'))

    try:
        product_name = product['name']
        
        # 1. Delete files from server storage
        if product.get('image_filename'):
            # Note: image_filename now contains a relative path e.g., 'static/uploads/file.jpg'
            # We need the full path from the DATA_FOLDER base.
            image_path = os.path.join(app.config['DATA_FOLDER'], product['image_filename'])
            if os.path.exists(image_path):
                os.remove(image_path)
        
        if product.get('script_filename'):
            script_path = os.path.join(app.config['SECURE_FILES_FOLDER'], product['script_filename'])
            if os.path.exists(script_path):
                os.remove(script_path)

        # 2. Delete from data dictionaries
        slug = product.get('slug')
        if slug and slug in slug_to_id_map:
            del slug_to_id_map[slug]
        
        del products[product_id]

        # 3. Save the changes to the JSON file
        save_data()
        flash(f"Successfully deleted '{product_name}'.", "success")

    except Exception as e:
        print(f"Error deleting product {product_id}: {e}")
        flash("An error occurred while deleting the product.", "error")

    return redirect(url_for('manage_products'))

@app.route('/admin/approve/<order_id>', methods=['POST'])
def approve_order(order_id):
    order = orders.get(order_id)
    if not (order and order['status'] == 'pending'):
        return redirect(url_for('admin_dashboard'))

    product = products.get(order['product_id'])
    if not product:
        abort(404, "Product associated with the order not found.")

    order['is_proof'] = request.form.get('mark_as_proof') == 'on'
    
    product['status'] = 'available' # This should be set back to available
    zip_path = os.path.join(app.config['TEMP_DOWNLOAD_FOLDER'], f"{order_id}.zip")

    try:
        with zipfile.ZipFile(zip_path, 'w') as zf:
            if product.get('checker_type') == 'website_link':
                delivery_content = (f"Thank you for your purchase of '{product['name']}'!\n\n"
                                  f"Here is your website link:\n{product.get('website_link', 'N/A')}\n\n"
                                  f"Your access will expire in {product.get('expiration_days', 'N/A')} day(s).\n")
                zf.writestr('instructions.txt', delivery_content.encode('utf-8'))
            elif product.get('script_filename'):
                file_path = os.path.join(app.config['SECURE_FILES_FOLDER'], product['script_filename'])
                if os.path.exists(file_path):
                    zf.write(file_path, arcname=os.path.basename(file_path))

            if product.get('bonus_freebies'):
                content = "Your Bonuses:\n" + "\n".join(f"- {item}" for item in product['bonus_freebies'])
                zf.writestr('BONUS_FREEBIES.txt', content.encode('utf-8'))
    except Exception as e:
        print(f"ERROR creating ZIP for order {order_id}: {e}")
        product['status'] = 'pending' # Revert status on failure
        return "Error creating ZIP file.", 500

    order['status'], order['download_token'] = 'approved', str(uuid.uuid4())
    save_data()

    if bot and ADMIN_TELEGRAM_CHAT_ID:
        if order.get('buyer_chat_id'):
            try:
                link = url_for('download_file', token=order['download_token'], _external=True)
                buyer_msg = (f"✅ **Your order has been approved!**\n\n"
                             f"Thank you for purchasing **{order['product_name']}**.\n\n"
                             f"Here is your secure download link:\n{link}")
                bot.send_message(order['buyer_chat_id'], buyer_msg, parse_mode='Markdown')
                buyer_contact = f"@{order['buyer_username']}" if order.get('buyer_username') else f"Chat ID: {order['buyer_chat_id']}"
                admin_msg = (f"🚀 **Order Approved & Delivered!**\n\n"
                             f"**Product:** {order['product_name']}\n"
                             f"**Buyer:** {buyer_contact}\n"
                             f"The file has been sent automatically.")
                bot.send_message(ADMIN_TELEGRAM_CHAT_ID, admin_msg, parse_mode='Markdown')
            except Exception as e:
                print(f"TELEGRAM DELIVERY ERROR for order {order_id}: {e}")
        else:
            admin_msg = (f"⚠️ **Order Approved - Action Required!**\n\n"
                         f"**Product:** {order['product_name']}\n"
                         f"**Order ID:** {order_id}\n\n"
                         f"The buyer has not linked their Telegram account. You must send them the download link manually: "
                         f"{url_for('download_file', token=order['download_token'], _external=True)}")
            bot.send_message(ADMIN_TELEGRAM_CHAT_ID, admin_msg, parse_mode='Markdown')

    return redirect(url_for('admin_dashboard'))

# ==============================================================================
# RUN THE APPLICATION
# ==============================================================================
if __name__ == '__main__':
    load_data()
    # On Render, Gunicorn is used to run this file as the web service.
    # The bot is run in a separate background worker process via bot_worker.py.
    # The debug server is for local development only.
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)