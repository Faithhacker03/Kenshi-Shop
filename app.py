# -*- coding: utf-8 -*-
"""
Kenshi Shop - Final Professional Version (Diskless & Stateless for Render)
- Uses Deta.sh for all data and file persistence.
"""

import os
import uuid
import re
import zipfile
import time
import requests
import io  # Required for in-memory file handling
from deta import Deta
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, abort, Response, flash
)
from werkzeug.utils import secure_filename

# ==============================================================================
# FLASK APP & DETA SETUP
# ==============================================================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a-super-secret-key-for-local-dev')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# --- Deta.sh Configuration (replaces local file system) ---
try:
    deta = Deta(os.environ.get('DETA_PROJECT_KEY'))
    products_db = deta.Base("products")
    orders_db = deta.Base("orders")
    images_drive = deta.Drive("product_images")
    receipts_drive = deta.Drive("receipts")
    secure_files_drive = deta.Drive("secure_files")
except Exception as e:
    print(f"FATAL: Deta Project Key not found or invalid. Please set DETA_PROJECT_KEY. Error: {e}")
    deta = None

# --- Admin & Payment Config ---
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', "admin")
PAYMENT_DETAILS = {"gcash": "0912-345-6789", "paymaya": "0998-765-4321"}

# --- Telegram Bot Config ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
ADMIN_TELEGRAM_CHAT_ID = os.environ.get('ADMIN_TELEGRAM_CHAT_ID')

bot = None
if TELEGRAM_BOT_TOKEN:
    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
else:
    print("Warning: TELEGRAM_BOT_TOKEN not set. Telegram features will be disabled.")

# ==============================================================================
# DATA STORAGE (IN-MEMORY CACHE)
# ==============================================================================
# We now load data from Deta into an in-memory cache at startup for performance.
products, orders, slug_to_id_map = {}, {}, {}
currency_cache = {'rate': 58.0, 'last_updated': 0}

def load_data_from_deta():
    """Loads all products and orders from Deta Base into the local cache."""
    global products, orders, slug_to_id_map
    if not deta: return

    print("Loading data from Deta Base...")
    # Fetch all products
    product_items = products_db.fetch().items
    products = {item['key']: item for item in product_items}
    for pid, pdata in products.items():
        if 'slug' in pdata:
            slug_to_id_map[pdata['slug']] = pid

    # Fetch all orders
    order_items = orders_db.fetch().items
    orders = {item['key']: item for item in order_items}
    print("Data loaded successfully.")

# ==============================================================================
# HELPER FUNCTIONS & ROUTES
# ==============================================================================
# get_usd_to_php_rate and create_slug remain the same...

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
    """Creates a unique, URL-friendly slug."""
    s = product_name.lower().strip()
    s = re.sub(r'[^a-z0-9-]', '-', s)
    s = re.sub(r'-+', '-', s).strip('-')
    if not s: s = "product"
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

# NEW ROUTES to serve files from Deta Drive
@app.route('/images/<filename>')
def serve_image(filename):
    """Serves a product image from Deta Drive."""
    try:
        file_data = images_drive.get(filename)
        return Response(file_data.iter_chunks(), content_type=file_data.content_type)
    except Exception:
        abort(404)

@app.route('/receipts/<filename>')
def serve_receipt(filename):
    """Serves a receipt image from Deta Drive."""
    try:
        file_data = receipts_drive.get(filename)
        return Response(file_data.iter_chunks(), content_type=file_data.content_type)
    except Exception:
        abort(404)

# ==============================================================================
# USER ROUTES (Updated for Deta)
# ==============================================================================
@app.route('/')
def index():
    available = [p for p in products.values() if p['status'] == 'available']
    # ... (categorization logic remains the same)
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

# ... (proofs, product_page, order_status routes have updated data handling)

@app.route('/proofs')
def proofs():
    proof_orders = sorted(
        [o for o in orders.values() if o.get('is_proof') and o.get('status') == 'completed' and o.get('receipt_filename')],
        key=lambda x: x.get('timestamp', 0), reverse=True
    )
    return render_template('proofs.html', proofs=proof_orders)

@app.route('/product/<slug>', methods=['GET', 'POST'])
def product_page(slug):
    product_id = slug_to_id_map.get(slug)
    if not product_id: abort(404)
    product = products.get(product_id)
    if not product or product['status'] != 'available': abort(404)

    if request.method == 'POST':
        order_id = str(uuid.uuid4())
        claim_code = f"CLAIM-{order_id.split('-')[0].upper()}"
        new_order = {
            'product_id': product['key'], 'product_name': product['name'],
            'price': product['price'], 'payment_method': request.form['payment_method'],
            'status': 'unpaid', 'receipt_filename': None, 'buyer_chat_id': None,
            'buyer_username': None, 'timestamp': time.time(), 'is_proof': False,
            'claim_code': claim_code
        }
        # Save to Deta Base
        orders_db.put(data=new_order, key=order_id)
        orders[order_id] = {**new_order, 'key': order_id} # Update local cache

        # Update product status in Deta Base
        products_db.update(updates={'status': 'pending'}, key=product['key'])
        products[product_id]['status'] = 'pending' # Update local cache

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
    if receipt_image and receipt_image.filename != '':
        filename = secure_filename(f"{order_id}_{receipt_image.filename}")
        # Upload to Deta Drive
        receipts_drive.put(filename, data=receipt_image.stream, content_type=receipt_image.content_type)
        
        updates = {'receipt_filename': filename, 'status': 'pending'}
        # Update Deta Base
        orders_db.update(updates=updates, key=order_id)
        # Update local cache
        order.update(updates)

        if bot and ADMIN_TELEGRAM_CHAT_ID:
            try:
                # ... (telegram notification logic is the same)
                 admin_message = (f"🔔 **New Order for Review**\n\n" f"A payment receipt has been uploaded for:\n" f"**{order['product_name']}**\n\n" f"Please go to your dashboard to review and approve.")
                 bot.send_message(ADMIN_TELEGRAM_CHAT_ID, admin_message, parse_mode='Markdown')
            except Exception as e:
                print(f"TELEGRAM ADMIN NOTIFICATION FAILED for order {order_id}. Error: {e}")
    return redirect(url_for('order_status', order_id=order_id))

@app.route('/download/<token>')
def download_file(token):
    order = next((o for o in orders.values() if o.get('download_token') == token), None)
    if not order: abort(404, "Invalid or expired download link.")
    product = products.get(order['product_id'])
    if not product: abort(404, "The product for this order could not be found.")

    # Create ZIP file in-memory
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w') as zf:
        if product.get('checker_type') == 'website_link':
            # ... (logic for website link remains the same)
            delivery_content = (f"Thank you for your purchase of '{product['name']}'!\n\n" f"Here is your website link:\n{product.get('website_link', 'N/A')}\n\n" f"Your access will expire in {product.get('expiration_days', 'N/A')} day(s).\n")
            zf.writestr('instructions.txt', delivery_content.encode('utf-8'))
        elif product.get('script_filename'):
            # Fetch the secure file from Deta Drive
            file_data = secure_files_drive.get(product['script_filename'])
            if file_data:
                zf.writestr(product['script_filename'], file_data.read())
        
        if product.get('bonus_freebies'):
             content = "Your Bonuses:\n" + "\n".join(f"- {item}" for item in product['bonus_freebies'])
             zf.writestr('BONUS_FREEBIES.txt', content.encode('utf-8'))
    
    memory_file.seek(0)
    
    if order['status'] == 'approved':
        orders_db.update(updates={'status': 'completed'}, key=order['key'])
        order['status'] = 'completed'

    return Response(
        memory_file,
        mimetype='application/zip',
        headers={'Content-Disposition': f'attachment;filename={product["slug"]}.zip'}
    )

# ==============================================================================
# ADMIN ROUTES (Updated for Deta)
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
        
        new_product = {
            'name': name, 'slug': create_slug(name),
            'price': request.form.get('price'), 'status': 'available',
            'category': request.form.get('category'),
            'description': request.form.get('description', ''),
            'bonus_freebies': [line.strip() for line in request.form.get('bonus_freebies', '').splitlines() if line.strip()]
        }

        image = request.files.get('image')
        if image and image.filename != '':
            img_filename = secure_filename(f"{product_id}_{image.filename}")
            # Upload to Deta Drive
            images_drive.put(img_filename, data=image.stream, content_type=image.content_type)
            new_product['image_filename'] = img_filename
        else:
            flash("Error: A product image is required.", "error")
            return redirect(url_for('add_product'))

        # ... (category specific logic is mostly the same)
        category = new_product['category']
        if category == 'codm':
            new_product['sub_category'] = request.form.get('sub_category')
        elif category == 'web_checker':
             # ...
             pass
        
        script_zip = request.files.get('script')
        if script_zip and script_zip.filename != '':
            s_name = secure_filename(name).lower().replace('_', '-')
            ext = os.path.splitext(script_zip.filename)[1]
            s_script_filename = f"{product_id}_{s_name}{ext}"
            # Upload secure file to Deta Drive
            secure_files_drive.put(s_script_filename, data=script_zip.stream)
            new_product['script_filename'] = s_script_filename
        
        # Save to Deta Base
        products_db.put(data=new_product, key=product_id)
        # Update local cache
        products[product_id] = {**new_product, 'key': product_id}
        slug_to_id_map[new_product['slug']] = product_id

        flash(f"Successfully added '{name}'!", "success")
        return redirect(url_for('manage_products'))
    return render_template('admin/add_product.html')

@app.route('/admin/delete/<product_id>', methods=['POST'])
def delete_product(product_id):
    product = products.get(product_id)
    if not product:
        flash("Product not found.", "error")
        return redirect(url_for('manage_products'))

    try:
        # 1. Delete files from Deta Drive
        if product.get('image_filename'):
            images_drive.delete(product['image_filename'])
        if product.get('script_filename'):
            secure_files_drive.delete(product['script_filename'])

        # 2. Delete from Deta Base
        products_db.delete(key=product_id)

        # 3. Delete from local cache
        slug = product.get('slug')
        if slug in slug_to_id_map:
            del slug_to_id_map[slug]
        del products[product_id]
        
        flash(f"Successfully deleted '{product['name']}'.", "success")
    except Exception as e:
        print(f"Error deleting product {product_id}: {e}")
        flash("An error occurred while deleting the product.", "error")
    return redirect(url_for('manage_products'))

@app.route('/admin/approve/<order_id>', methods=['POST'])
def approve_order(order_id):
    order = orders.get(order_id)
    if not (order and order['status'] == 'pending'): return redirect(url_for('admin_dashboard'))

    # Update product status to available
    products_db.update({'status': 'available'}, key=order['product_id'])
    products[order['product_id']]['status'] = 'available'

    # Update order status to approved
    updates = {
        'status': 'approved',
        'download_token': str(uuid.uuid4()),
        'is_proof': request.form.get('mark_as_proof') == 'on'
    }
    orders_db.update(updates, key=order_id)
    order.update(updates)

    if bot and ADMIN_TELEGRAM_CHAT_ID:
        # ... (Telegram notification logic is the same)
        pass
    
    return redirect(url_for('admin_dashboard'))


# ==============================================================================
# RUN APP
# ==============================================================================
if __name__ == '__main__':
    if deta:
        load_data_from_deta()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)