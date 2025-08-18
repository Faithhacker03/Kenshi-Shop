# -*- coding: utf-8 -*-
"""
Kenshi Shop - Final Version for Render Deployment
"""

import os
import uuid
import re
import json
import zipfile
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, abort, flash, send_from_directory
)
from werkzeug.utils import secure_filename

# ==============================================================================
# FLASK APP SETUP & CONFIGURATION (RENDER-COMPATIBLE)
# ==============================================================================
app = Flask(__name__)

# --- Determine the base path for data storage ---
PERSISTENT_STORAGE_PATH = os.environ.get('RENDER_DISK_PATH', os.getcwd())

# --- Core Config ---
app.config.update(
    SECRET_KEY=os.environ.get('SECRET_KEY', 'a-default-secret-key-for-local-dev'),
    UPLOAD_FOLDER=os.path.join(PERSISTENT_STORAGE_PATH, 'uploads/'), # Simplified path
    SECURE_FILES_FOLDER=os.path.join(PERSISTENT_STORAGE_PATH, 'secure_files/'),
    DATA_FOLDER=os.path.join(PERSISTENT_STORAGE_PATH, 'data/'),
    MAX_CONTENT_LENGTH=16 * 1024 * 1024
)

# --- Admin & Contact Config ---
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', "admin")
TELEGRAM_CONTACT_USERNAME = "KenshiKupalBoss"

# ==============================================================================
# DATA STORAGE SETUP
# ==============================================================================
products, slug_to_id_map = {}, {}

# Create all necessary folders on startup inside the persistent storage path
for folder_key in ['UPLOAD_FOLDER', 'SECURE_FILES_FOLDER', 'DATA_FOLDER']:
    os.makedirs(app.config[folder_key], exist_ok=True)

PRODUCTS_FILE = os.path.join(app.config['DATA_FOLDER'], 'products.json')

# (The rest of your code is perfect, just adding the new route at the end)

def save_data():
    try:
        with open(PRODUCTS_FILE, 'w') as f: json.dump(products, f, indent=4)
        print("Data saved successfully.")
    except Exception as e: print(f"ERROR: Could not save data. Reason: {e}")

def load_data():
    global products, slug_to_id_map
    try:
        if os.path.exists(PRODUCTS_FILE):
            with open(PRODUCTS_FILE, 'r') as f: products = json.load(f)
            for pid, pdata in products.items():
                if 'slug' in pdata: slug_to_id_map[pdata['slug']] = pid
        print("Data loaded successfully.")
    except Exception as e: print(f"ERROR: Could not load data. Reason: {e}")

def create_slug(product_name, existing_product_id=None):
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
    admin_paths = ['/admin/dashboard', '/admin/add', '/admin/manage_products', '/admin/edit', '/admin/delete']
    if any(request.path.startswith(p) for p in admin_paths):
        if 'admin_logged_in' not in session: return redirect(url_for('admin_login'))

@app.route('/')
def index():
    available = [p for p in products.values() if p.get('status', 'available') == 'available']
    categorized = {
        'tools': [p for p in available if p.get('category') == 'tools'],
        'web_checkers': [p for p in available if p.get('category') == 'web_checker'],
        'ml_accounts': [p for p in available if p.get('category') == 'ml_account'],
        'codm_active': [p for p in available if p.get('category') == 'codm' and p.get('sub_category') == 'active'],
        'codm_semi_active': [p for p in available if p.get('category') == 'codm' and p.get('sub_category') == 'semi-active'],
        'codm_inactive': [p for p in available if p.get('category') == 'codm' and p.get('sub_category') == 'inactive'],
        'freebies': [p for p in available if p.get('category') == 'freebies']
    }
    return render_template('index.html', categorized_products=categorized, php_rate=58.0, telegram_contact=TELEGRAM_CONTACT_USERNAME)

@app.route('/proofs')
def proofs():
    return render_template('proofs.html', proofs=[])

@app.route('/product/<slug>')
def product_page(slug):
    product_id = slug_to_id_map.get(slug)
    if not product_id: abort(404)
    product = products.get(product_id)
    if not product or product.get('status', 'available') != 'available': abort(404)
    return render_template('product.html', product=product, php_rate=58.0, telegram_contact=TELEGRAM_CONTACT_USERNAME)

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form['password'] == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            flash("Login successful!", "success")
            return redirect(url_for('admin_dashboard'))
        else:
            flash("Incorrect password.", "error")
    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('index'))

@app.route('/admin/dashboard')
def admin_dashboard():
    return render_template('admin/dashboard.html')

@app.route('/admin/manage_products')
def manage_products():
    all_products = sorted(list(products.values()), key=lambda x: x.get('name', ''))
    return render_template('admin/manage_products.html', products=all_products)

@app.route('/admin/add', methods=['GET', 'POST'])
def add_product():
    if request.method == 'POST':
        product_id, name, slug = str(uuid.uuid4()), request.form['name'], create_slug(request.form['name'])
        new_product = {'id': product_id, 'slug': slug, 'name': name, 'price': request.form.get('price', '0.0'),'status': 'available','category': request.form.get('category'),'description': request.form.get('description', ''),'bonus_freebies': [line.strip() for line in request.form.get('bonus_freebies', '').splitlines() if line.strip()]}
        image = request.files.get('image')
        if image and image.filename:
            img_filename = secure_filename(f"{product_id}_{image.filename}")
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], img_filename))
            new_product['image_filename'] = img_filename
        else:
            flash("A product image is required.", "error")
            return redirect(url_for('add_product'))
        if new_product['category'] == 'codm': new_product['sub_category'] = request.form.get('sub_category')
        script_zip = request.files.get('script')
        if script_zip and script_zip.filename:
            s_name = secure_filename(name).lower().replace('_', '-')
            ext = os.path.splitext(script_zip.filename)[1] or '.zip'
            s_script_filename = f"{product_id}_{s_name}{ext}"
            script_zip.save(os.path.join(app.config['SECURE_FILES_FOLDER'], s_script_filename))
            new_product['script_filename'] = s_script_filename
        products[product_id], slug_to_id_map[slug] = new_product, product_id
        save_data()
        flash(f"Successfully added '{name}'!", "success")
        return redirect(url_for('manage_products'))
    return render_template('admin/add_product.html')

@app.route('/admin/edit/<product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    product = products.get(product_id)
    if not product: abort(404)
    if request.method == 'POST':
        original_slug = product.get('slug')
        product['name'], product['description'], product['price'], product['status'] = request.form.get('name'), request.form.get('description'), request.form.get('price'), request.form.get('status')
        product['bonus_freebies'] = [line.strip() for line in request.form.get('bonus_freebies', '').splitlines() if line.strip()]
        new_slug = create_slug(product['name'], existing_product_id=product_id)
        if new_slug != original_slug:
            product['slug'] = new_slug
            if original_slug in slug_to_id_map: del slug_to_id_map[original_slug]
            slug_to_id_map[new_slug] = product_id
        save_data()
        flash(f"Successfully updated '{product['name']}'!", "success")
        return redirect(url_for('manage_products'))
    return render_template('admin/edit_product.html', product=product)

@app.route('/admin/delete/<product_id>', methods=['POST'])
def delete_product(product_id):
    product = products.pop(product_id, None)
    if not product:
        flash("Product not found.", "error")
        return redirect(url_for('manage_products'))
    try:
        if product.get('image_filename'): os.remove(os.path.join(app.config['UPLOAD_FOLDER'], product['image_filename']))
        if product.get('script_filename'): os.remove(os.path.join(app.config['SECURE_FILES_FOLDER'], product['script_filename']))
        if product.get('slug') in slug_to_id_map: del slug_to_id_map[product.get('slug')]
        save_data()
        flash(f"Successfully deleted '{product['name']}'.", "success")
    except OSError as e:
        print(f"Error deleting file for product {product_id}: {e}")
        flash("Product data deleted, but an error occurred removing associated files.", "error")
    return redirect(url_for('manage_products'))

# ==============================================================================
#  *** NEW ROUTE TO SERVE UPLOADED IMAGES FROM THE PERSISTENT DISK ***
# ==============================================================================
@app.route('/media/<path:filename>')
def serve_upload(filename):
    """Serves a file from the UPLOAD_FOLDER on the persistent disk."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
# ==============================================================================

if __name__ == '__main__':
    load_data()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port) # debug=True for local testing