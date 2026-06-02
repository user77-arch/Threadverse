# ── db.py — JSON file-based storage (no MySQL) ────────────────────────────────
import json
import os
import threading
from werkzeug.security import generate_password_hash, check_password_hash

DATA_DIR      = os.path.join(os.path.dirname(__file__), 'data')
USERS_FILE    = os.path.join(DATA_DIR, 'users.json')
PRODUCTS_FILE = os.path.join(DATA_DIR, 'products.json')
CART_FILE     = os.path.join(DATA_DIR, 'cart.json')
ORDERS_FILE   = os.path.join(DATA_DIR, 'orders.json')
WISHLIST_FILE = os.path.join(DATA_DIR, 'wishlist.json')

_lock = threading.Lock()

# ── File I/O ──────────────────────────────────────────────────────────────────

def _load(path):
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def _save(path, data):
    with _lock:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

# ── Startup ───────────────────────────────────────────────────────────────────

def run_migrations():
    """Ensure all JSON data files exist and normalize legacy formats."""
    os.makedirs(DATA_DIR, exist_ok=True)
    for path in [USERS_FILE, PRODUCTS_FILE, CART_FILE, ORDERS_FILE, WISHLIST_FILE]:
        if not os.path.exists(path):
            _save(path, [])

    # Normalize cart: ensure each item has product_id (old format used 'id')
    cart = _load(CART_FILE)
    changed = False
    for item in cart:
        if 'product_id' not in item and 'id' in item:
            item['product_id'] = item['id']
            changed = True
    if changed:
        _save(CART_FILE, cart)

    # Normalize wishlist: ensure each item has product_id
    wl = _load(WISHLIST_FILE)
    changed = False
    for item in wl:
        if 'product_id' not in item and 'id' in item:
            item['product_id'] = item['id']
            changed = True
    if changed:
        _save(WISHLIST_FILE, wl)

# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return generate_password_hash(plain)

def verify_password(plain: str, stored: str) -> bool:
    if stored and (stored.startswith('pbkdf2:') or stored.startswith('scrypt:')):
        return check_password_hash(stored, plain)
    return plain == stored  # legacy plain-text fallback

# ── Users ─────────────────────────────────────────────────────────────────────

def get_user_by_email(email):
    users = _load(USERS_FILE)
    return next((u for u in users if u.get('email', '').lower() == email.lower()), None)

def get_user_by_id(uid):
    users = _load(USERS_FILE)
    return next((u for u in users if u.get('id') == uid), None)

def create_user(uid, name, email, password, role, shop_name, created,
                contact_number=None, verification_doc=None, google_id=None):
    users = _load(USERS_FILE)
    hashed = hash_password(password) if password else ''
    users.append({
        'id': uid,
        'name': name,
        'email': email,
        'password': hashed,
        'role': role,
        'shop_name': shop_name or '',
        'created': created,
        'contact_number': contact_number or '',
        'verification_doc': verification_doc or '',
        'google_id': google_id or '',
    })
    _save(USERS_FILE, users)

def update_user(uid, **fields):
    users = _load(USERS_FILE)
    for u in users:
        if u.get('id') == uid:
            u.update(fields)
            break
    _save(USERS_FILE, users)

def get_vendors():
    """Return vendor users who have a shop_name set, sorted by shop_name."""
    users = _load(USERS_FILE)
    vendors = [u for u in users
               if u.get('role') == 'vendor' and u.get('shop_name', '').strip()]
    return sorted(vendors, key=lambda v: v.get('shop_name', '').lower())

def get_vendors_with_product_count():
    """Return vendors with a product_count field attached."""
    products = _load(PRODUCTS_FILE)
    vendors = get_vendors()
    for v in vendors:
        v = dict(v)
        v['product_count'] = sum(1 for p in products if p.get('vendor_id') == v['id'])
    # Rebuild list so mutations don't bleed into the user file cache
    result = []
    for v in vendors:
        entry = dict(v)
        entry['product_count'] = sum(1 for p in products if p.get('vendor_id') == v['id'])
        result.append(entry)
    return result

def get_or_create_google_user(google_id: str, email: str, name: str):
    import uuid, datetime as _dt
    users = _load(USERS_FILE)
    user = next((u for u in users if u.get('google_id') == google_id), None)
    if user:
        return user
    user = next((u for u in users if u.get('email', '').lower() == email.lower()), None)
    if user:
        update_user(user['id'], google_id=google_id)
        return get_user_by_id(user['id'])
    uid = 'u' + str(uuid.uuid4())[:6]
    created = _dt.datetime.now().strftime('%d %b %Y')
    create_user(uid, name, email, '', 'customer', '', created, google_id=google_id)
    return get_user_by_id(uid)

# ── Products ──────────────────────────────────────────────────────────────────

def _normalize_product(p):
    """Ensure sizes/tags are lists and price/rating are floats."""
    p = dict(p)
    if isinstance(p.get('sizes'), str):
        p['sizes'] = [s.strip() for s in p['sizes'].split(',') if s.strip()]
    elif not isinstance(p.get('sizes'), list):
        p['sizes'] = []
    if isinstance(p.get('tags'), str):
        p['tags'] = [t.strip() for t in p['tags'].split(',') if t.strip()]
    elif not isinstance(p.get('tags'), list):
        p['tags'] = []
    p['price']  = float(p.get('price') or 0)
    p['rating'] = float(p.get('rating') or 0)
    return p

def get_products(category=None, gender=None, search=None, max_price=None,
                 sort='default', vendor_id=None):
    products = [_normalize_product(p) for p in _load(PRODUCTS_FILE)]
    if category:
        products = [p for p in products if p.get('category', '').lower() == category.lower()]
    if vendor_id:
        products = [p for p in products if p.get('vendor_id') == vendor_id]
    if gender:
        products = [p for p in products
                    if p.get('gender', '').lower() in (gender.lower(), 'unisex')]
    if max_price is not None:
        products = [p for p in products if p['price'] <= max_price]
    if search:
        s = search.lower()
        products = [p for p in products if
                    s in p.get('name', '').lower() or
                    s in ' '.join(p.get('tags', [])).lower() or
                    s in p.get('description', '').lower()]
    order_key = {
        'price_asc':  lambda p: p['price'],
        'price_desc': lambda p: -p['price'],
        'rating':     lambda p: -p['rating'],
    }.get(sort, lambda p: p.get('id', 0))
    return sorted(products, key=order_key)

def get_product(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    products = _load(PRODUCTS_FILE)
    p = next((p for p in products if p.get('id') == pid), None)
    return _normalize_product(p) if p else None

def add_product(data: dict) -> int:
    products = _load(PRODUCTS_FILE)
    new_id = max((p.get('id', 0) for p in products), default=0) + 1
    data = dict(data)
    data['id'] = new_id
    data.setdefault('rating', 0)
    data.setdefault('reviews', 0)
    products.append(data)
    _save(PRODUCTS_FILE, products)
    return new_id

def update_product(product_id: int, vendor_id: str, **fields):
    products = _load(PRODUCTS_FILE)
    for p in products:
        if p.get('id') == product_id and p.get('vendor_id') == vendor_id:
            p.update(fields)
            break
    _save(PRODUCTS_FILE, products)

def delete_product(product_id: int, vendor_id: str):
    products = _load(PRODUCTS_FILE)
    products = [p for p in products
                if not (p.get('id') == product_id and p.get('vendor_id') == vendor_id)]
    _save(PRODUCTS_FILE, products)
    # Clean up cart and wishlist entries for the deleted product
    cart = [c for c in _load(CART_FILE) if c.get('product_id') != product_id]
    _save(CART_FILE, cart)
    wl = [w for w in _load(WISHLIST_FILE) if w.get('product_id') != product_id]
    _save(WISHLIST_FILE, wl)

# ── Cart ──────────────────────────────────────────────────────────────────────

def get_cart(session_id):
    cart = _load(CART_FILE)
    items = []
    for c in cart:
        if c.get('session_id') != session_id:
            continue
        item = dict(c)
        item['price'] = float(item.get('price', 0))
        # Ensure product_id and id are both set for template compatibility
        if 'product_id' not in item:
            item['product_id'] = item.get('id')
        item['id'] = item['product_id']
        items.append(item)
    return items

def cart_add(session_id, product, size):
    cart = _load(CART_FILE)
    pid = product['id']
    for item in cart:
        pid_in_item = item.get('product_id') or item.get('id')
        if item.get('session_id') == session_id and pid_in_item == pid:
            item['qty'] = item.get('qty', 1) + 1
            _save(CART_FILE, cart)
            return
    cart.append({
        'session_id':    session_id,
        'product_id':    pid,
        'name':          product['name'],
        'price':         float(product['price']),
        'qty':           1,
        'selected_size': size,
        'image':         product.get('image', ''),
    })
    _save(CART_FILE, cart)

def cart_remove(session_id, product_id):
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        pass
    cart = _load(CART_FILE)
    cart = [c for c in cart
            if not (c.get('session_id') == session_id and
                    (c.get('product_id') == product_id or c.get('id') == product_id))]
    _save(CART_FILE, cart)

def cart_update(session_id, product_id, qty):
    if qty <= 0:
        cart_remove(session_id, product_id)
        return
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        pass
    cart = _load(CART_FILE)
    for item in cart:
        pid_in_item = item.get('product_id') or item.get('id')
        if item.get('session_id') == session_id and pid_in_item == product_id:
            item['qty'] = qty
            break
    _save(CART_FILE, cart)

def cart_clear(session_id):
    cart = _load(CART_FILE)
    cart = [c for c in cart if c.get('session_id') != session_id]
    _save(CART_FILE, cart)

# ── Wishlist ──────────────────────────────────────────────────────────────────

def get_wishlist(session_id):
    wl = _load(WISHLIST_FILE)
    items = []
    for w in wl:
        if w.get('session_id') != session_id:
            continue
        item = dict(w)
        item['price'] = float(item.get('price', 0))
        if 'product_id' not in item:
            item['product_id'] = item.get('id')
        item['id'] = item['product_id']
        items.append(item)
    return items

def wishlist_toggle(session_id, product):
    wl = _load(WISHLIST_FILE)
    pid = product['id']
    for w in wl:
        pid_in_item = w.get('product_id') or w.get('id')
        if w.get('session_id') == session_id and pid_in_item == pid:
            wl = [x for x in wl
                  if not (x.get('session_id') == session_id and
                          (x.get('product_id') == pid or x.get('id') == pid))]
            _save(WISHLIST_FILE, wl)
            return False  # removed
    wl.append({
        'session_id': session_id,
        'product_id': pid,
        'name':       product['name'],
        'price':      float(product['price']),
        'image':      product.get('image', ''),
    })
    _save(WISHLIST_FILE, wl)
    return True  # added

def wishlist_remove(session_id, product_id):
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        pass
    wl = _load(WISHLIST_FILE)
    wl = [w for w in wl
          if not (w.get('session_id') == session_id and
                  (w.get('product_id') == product_id or w.get('id') == product_id))]
    _save(WISHLIST_FILE, wl)

# ── Orders ────────────────────────────────────────────────────────────────────

def create_order(order_id, user_id, items, total, status, date, shipping):
    order_items = []
    for item in items:
        product_id = item.get('product_id') or item.get('id')
        qty = int(item.get('qty', 1) or 1)
        order_items.append({
            'order_id':      order_id,
            'product_id':    product_id,
            'name':          item['name'],
            'price':         float(item['price']),
            'qty':           qty,
            'selected_size': item.get('selected_size', 'M'),
            'image':         item.get('image', ''),
        })
        # Reduce stock
        products = _load(PRODUCTS_FILE)
        for p in products:
            if p.get('id') == product_id:
                p['stock'] = max(0, int(p.get('stock', 0)) - qty)
                break
        _save(PRODUCTS_FILE, products)

    orders = _load(ORDERS_FILE)
    orders.append({
        'id':       order_id,
        'user_id':  user_id,
        'items':    order_items,
        'total':    float(total),
        'status':   status,
        'date':     date,
        'shipping': shipping,
    })
    _save(ORDERS_FILE, orders)

def get_orders_for_user(user_id):
    orders = _load(ORDERS_FILE)
    result = []
    for o in orders:
        if o.get('user_id') == user_id:
            o = dict(o)
            o['total'] = float(o.get('total', 0))
            result.append(o)
    return sorted(result, key=lambda o: o.get('date', ''), reverse=True)

def get_orders_for_vendor(vendor_id):
    products = _load(PRODUCTS_FILE)
    prod_ids = {p['id'] for p in products if p.get('vendor_id') == vendor_id}
    if not prod_ids:
        return []
    orders = _load(ORDERS_FILE)
    result = []
    for o in orders:
        vendor_items = [i for i in o.get('items', []) if i.get('product_id') in prod_ids]
        if vendor_items:
            entry = dict(o)
            entry['items'] = vendor_items
            entry['total'] = round(sum(float(i['price']) * i.get('qty', 1)
                                       for i in vendor_items), 2)
            result.append(entry)
    return result

def get_vendor_stats(vendor_id):
    products = _load(PRODUCTS_FILE)
    prod_ids = {p['id'] for p in products if p.get('vendor_id') == vendor_id}
    total_products = len(prod_ids)
    if not prod_ids:
        return total_products, 0, 0
    orders = _load(ORDERS_FILE)
    order_ids     = set()
    total_revenue = 0.0
    for o in orders:
        for item in o.get('items', []):
            if item.get('product_id') in prod_ids:
                order_ids.add(o['id'])
                total_revenue += float(item['price']) * item.get('qty', 1)
    return total_products, len(order_ids), round(total_revenue, 2)
