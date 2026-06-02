# ── db.py — MySQL connection & helpers ────────────────────────────────────────
import pymysql
import pymysql.cursors
from werkzeug.security import generate_password_hash, check_password_hash

DB_CONFIG = {
    'host'     : 'localhost',
    'port'     : 3306,
    'user'     : 'root',          # ← change if needed
    'password' : '123456',              # ← your MySQL root password
    'database' : 'threadverse',
    'charset'  : 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
    'autocommit': True,
}

def get_conn():
    return pymysql.connect(**DB_CONFIG)

def run_migrations():
    """Safely add any missing columns to the users table on startup."""
    columns = {
        'contact_number':   'VARCHAR(20)  DEFAULT NULL',
        'verification_doc': 'VARCHAR(300) DEFAULT NULL',
        'google_id':        'VARCHAR(120) DEFAULT NULL',
        'is_verified':      'TINYINT(1)   DEFAULT 0',
    }
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Get existing columns from information schema
            cur.execute("""
                SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users'
            """)
            existing = {row['COLUMN_NAME'] for row in cur.fetchall()}
            for col, definition in columns.items():
                if col not in existing:
                    try:
                        cur.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                    except Exception:
                        pass
    finally:
        conn.close()

# ── Generic helpers ───────────────────────────────────────────────────────────

def query(sql, args=None):
    """Run SELECT, return list of dicts."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args or ())
            return cur.fetchall()
    finally:
        conn.close()

def query_one(sql, args=None):
    """Run SELECT, return first row or None."""
    rows = query(sql, args)
    return rows[0] if rows else None

def execute(sql, args=None):
    """Run INSERT/UPDATE/DELETE, return lastrowid."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args or ())
            return cur.lastrowid
    finally:
        conn.close()

# ── Domain helpers ────────────────────────────────────────────────────────────

def row_to_product(row):
    """Convert a DB row into the dict shape the templates expect."""
    if not row:
        return None
    p = dict(row)
    p['sizes'] = [s.strip() for s in (p.get('sizes') or '').split(',') if s.strip()]
    p['tags']  = [t.strip() for t in (p.get('tags')  or '').split(',') if t.strip()]
    p['price'] = float(p['price'])
    p['rating']= float(p.get('rating') or 0)
    return p

def get_products(category=None, gender=None, search=None, max_price=None,
                 sort='default', vendor_id=None):
    where, args = [], []
    if category:  where.append("category = %s");    args.append(category)
    if vendor_id: where.append("vendor_id = %s");   args.append(vendor_id)
    if gender:    where.append("gender IN (%s,'unisex')"); args.append(gender)
    if max_price: where.append("price <= %s");       args.append(max_price)
    if search:
        where.append("(name LIKE %s OR tags LIKE %s OR description LIKE %s)")
        like = f'%{search}%'
        args += [like, like, like]
    sql = "SELECT * FROM products"
    if where: sql += " WHERE " + " AND ".join(where)
    order = {'price_asc':'price ASC','price_desc':'price DESC','rating':'rating DESC'}.get(sort,'id ASC')
    sql += f" ORDER BY {order}"
    return [row_to_product(r) for r in query(sql, args)]

def get_product(pid):
    return row_to_product(query_one("SELECT * FROM products WHERE id=%s", (pid,)))

def get_user_by_email(email):
    return query_one("SELECT * FROM users WHERE email=%s", (email,))

def get_user_by_id(uid):
    return query_one("SELECT * FROM users WHERE id=%s", (uid,))

def hash_password(plain: str) -> str:
    """Return a Werkzeug hash of the plaintext password."""
    return generate_password_hash(plain)

def verify_password(plain: str, stored: str) -> bool:
    """Verify plain against stored hash; falls back to plain-text for legacy accounts."""
    if stored and (stored.startswith('pbkdf2:') or stored.startswith('scrypt:')):
        return check_password_hash(stored, plain)
    return plain == stored  # legacy plain-text

def create_user(uid, name, email, password, role, shop_name, created,
                contact_number=None, verification_doc=None, google_id=None):
    hashed = hash_password(password) if password else ''
    execute(
        """INSERT INTO users
           (id,name,email,password,role,shop_name,created,contact_number,verification_doc,google_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (uid, name, email, hashed, role, shop_name, created,
         contact_number or '', verification_doc or '', google_id or '')
    )

def get_or_create_google_user(google_id: str, email: str, name: str):
    """Find existing user by google_id or email; create one if not found."""
    import uuid, datetime as _dt
    user = query_one("SELECT * FROM users WHERE google_id=%s", (google_id,))
    if user:
        return user
    user = query_one("SELECT * FROM users WHERE email=%s", (email,))
    if user:
        execute("UPDATE users SET google_id=%s WHERE id=%s", (google_id, user['id']))
        return query_one("SELECT * FROM users WHERE id=%s", (user['id'],))
    uid = 'u' + str(uuid.uuid4())[:6]
    created = _dt.datetime.now().strftime('%d %b %Y')
    execute(
        """INSERT INTO users
           (id,name,email,password,role,shop_name,created,contact_number,verification_doc,google_id)
           VALUES (%s,%s,%s,'','customer','',       %s,    '',            '',               %s)""",
        (uid, name, email, created, google_id)
    )
    return query_one("SELECT * FROM users WHERE id=%s", (uid,))

def get_cart(session_id):
    rows = query("SELECT * FROM cart WHERE session_id=%s", (session_id,))
    for r in rows:
        r['price'] = float(r['price'])
    return rows

def cart_add(session_id, product, size):
    existing = query_one(
        "SELECT id, qty FROM cart WHERE session_id=%s AND product_id=%s",
        (session_id, product['id'])
    )
    if existing:
        execute("UPDATE cart SET qty=qty+1 WHERE id=%s", (existing['id'],))
    else:
        execute(
            "INSERT INTO cart (session_id,product_id,name,price,qty,selected_size,image) VALUES (%s,%s,%s,%s,1,%s,%s)",
            (session_id, product['id'], product['name'], product['price'], size, product.get('image',''))
        )

def cart_remove(session_id, product_id):
    execute("DELETE FROM cart WHERE session_id=%s AND product_id=%s", (session_id, product_id))

def cart_update(session_id, product_id, qty):
    if qty <= 0:
        cart_remove(session_id, product_id)
    else:
        execute("UPDATE cart SET qty=%s WHERE session_id=%s AND product_id=%s",
                (qty, session_id, product_id))

def cart_clear(session_id):
    execute("DELETE FROM cart WHERE session_id=%s", (session_id,))

def get_wishlist(session_id):
    rows = query("SELECT * FROM wishlist WHERE session_id=%s", (session_id,))
    for r in rows:
        r['price'] = float(r['price'])
    return rows

def wishlist_toggle(session_id, product):
    existing = query_one(
        "SELECT id FROM wishlist WHERE session_id=%s AND product_id=%s",
        (session_id, product['id'])
    )
    if existing:
        execute("DELETE FROM wishlist WHERE id=%s", (existing['id'],))
        return False   # removed
    else:
        execute(
            "INSERT INTO wishlist (session_id,product_id,name,price,image) VALUES (%s,%s,%s,%s,%s)",
            (session_id, product['id'], product['name'], product['price'], product.get('image',''))
        )
        return True    # added

def get_orders_for_user(user_id):
    orders = query("SELECT * FROM orders WHERE user_id=%s ORDER BY date DESC", (user_id,))
    for o in orders:
        o['total'] = float(o['total'])
        o['items'] = _get_order_items(o['id'])
        o['shipping'] = {'name': o.pop('shipping_name',''), 'address': o.pop('shipping_address',''), 'city': o.pop('shipping_city','')}
    return orders

def get_orders_for_vendor(vendor_id):
    """Return orders that contain at least one product from this vendor."""
    prod_ids = [r['id'] for r in query("SELECT id FROM products WHERE vendor_id=%s", (vendor_id,))]
    if not prod_ids:
        return []
    placeholders = ','.join(['%s'] * len(prod_ids))
    order_ids = [r['order_id'] for r in query(
        f"SELECT DISTINCT order_id FROM order_items WHERE product_id IN ({placeholders})", prod_ids
    )]
    if not order_ids:
        return []
    results = []
    for oid in order_ids:
        o = query_one("SELECT * FROM orders WHERE id=%s", (oid,))
        if not o: continue
        o['total'] = float(o['total'])
        all_items = _get_order_items(oid)
        # Only show items belonging to this vendor
        o['items'] = [i for i in all_items if i.get('product_id') in prod_ids]
        o['total']  = sum(i['price'] * i.get('qty',1) for i in o['items'])
        o['shipping'] = {'name': o.pop('shipping_name',''), 'address': o.pop('shipping_address',''), 'city': o.pop('shipping_city','')}
        results.append(o)
    return results

def _get_order_items(order_id):
    rows = query("SELECT * FROM order_items WHERE order_id=%s", (order_id,))
    for r in rows:
        r['price'] = float(r['price'])
    return rows

def create_order(order_id, user_id, items, total, status, date, shipping):
    execute(
        "INSERT INTO orders (id,user_id,total,status,date,shipping_name,shipping_address,shipping_city) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (order_id, user_id, total, status, date,
         shipping.get('name',''), shipping.get('address',''), shipping.get('city',''))
    )
    for item in items:
        # Cart rows have 'product_id'; product dicts have 'id'. Support both.
        product_id = item.get('product_id') or item.get('id')
        qty = int(item.get('qty', 1) or 1)
        execute(
            "INSERT INTO order_items (order_id,product_id,name,price,qty,selected_size,image) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (order_id, product_id, item['name'],
             item['price'], qty, item.get('selected_size', 'M'), item.get('image', ''))
        )
        # Reduce stock after successful order item insert.
        # GREATEST prevents stock from going negative.
        execute(
            "UPDATE products SET stock=GREATEST(stock-%s,0) WHERE id=%s",
            (qty, product_id)
        )

def get_vendor_stats(vendor_id):
    prods = query("SELECT id FROM products WHERE vendor_id=%s", (vendor_id,))
    prod_ids = [p['id'] for p in prods]
    total_products = len(prod_ids)
    if not prod_ids:
        return total_products, 0, 0
    placeholders = ','.join(['%s']*len(prod_ids))
    order_ids = {r['order_id'] for r in query(
        f"SELECT DISTINCT order_id FROM order_items WHERE product_id IN ({placeholders})", prod_ids
    )}
    total_orders = len(order_ids)
    if not order_ids:
        return total_products, 0, 0
    op = ','.join(['%s']*len(order_ids))
    rev_rows = query(f"SELECT price, qty FROM order_items WHERE order_id IN ({op}) AND product_id IN ({placeholders})",
                     list(order_ids) + prod_ids)
    total_revenue = sum(float(r['price']) * r.get('qty',1) for r in rev_rows)
    return total_products, total_orders, round(total_revenue, 2)
