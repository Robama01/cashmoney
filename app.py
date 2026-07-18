import os
import hashlib
import secrets
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL manquante")

# ==============================================
# CONFIGURATION
# ==============================================
MEMBERSHIP_USDT = 2.10
SYSTEM_FEE = 0.37
POOL_AMOUNT = MEMBERSHIP_USDT - SYSTEM_FEE
GAINS = [0.4325, 0.3460, 0.2595, 0.2076, 0.1730, 0.1211, 0.0865, 0.0519, 0.0346, 0.0173]
WALLET_USDT = os.environ.get("WALLET_USDT", "0xE4901E78F8c92199bAfD93AD87C5a250C48199c2")
REWARD_PER_VIDEO = 0.50
DAILY_VIDEO_LIMIT = 10

# ==============================================
# BASE DE DONNÉES
# ==============================================
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE,
            password_hash TEXT,
            nom TEXT,
            parrain_id INTEGER,
            actif INTEGER DEFAULT 0,
            gains_total REAL DEFAULT 0,
            balance REAL DEFAULT 0,
            date_inscription TEXT,
            payment_hash TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS video_watches (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            video_id VARCHAR(50),
            reward REAL,
            created_at TIMESTAMP DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS admin_fees (
            id SERIAL PRIMARY KEY,
            amount REAL,
            description TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )""")
        conn.commit()

# ==============================================
# FONCTIONS
# ==============================================
def hash_pw(p):
    return hashlib.sha256(p.encode()).hexdigest()

def get_user_by_email(email):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE email=%s", (email,))
        return c.fetchone()

def get_user_by_id(uid):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE id=%s", (uid,))
        return c.fetchone()

def credit_user(user_id, amount):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (amount, user_id))
        conn.commit()

def add_admin_fee(amount, description="Frais système"):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO admin_fees (amount, description) VALUES (%s, %s)", (amount, description))
        conn.commit()

def get_upline(user_id, depth=10):
    upline = []
    current_id = user_id
    for _ in range(depth):
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT parrain_id FROM users WHERE id = %s", (current_id,))
            row = c.fetchone()
            if row and row["parrain_id"]:
                upline.append(row["parrain_id"])
                current_id = row["parrain_id"]
            else:
                upline.append(None)
                break
    while len(upline) < depth:
        upline.append(None)
    return upline

def process_payment(user_id, tx_hash):
    user = get_user_by_id(user_id)
    if user["actif"] == 1:
        return False
    add_admin_fee(SYSTEM_FEE)
    upline = get_upline(user_id)
    distributed = 0.0
    for level, sponsor_id in enumerate(upline):
        if sponsor_id and level < len(GAINS):
            credit_user(sponsor_id, GAINS[level])
            distributed += GAINS[level]
    remaining = POOL_AMOUNT - distributed
    if remaining > 0:
        add_admin_fee(remaining, "Reste non distribué")
    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET actif = 1, payment_hash = %s WHERE id = %s", (tx_hash, user_id))
        conn.commit()
    return True

def get_today_video_count(user_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM video_watches WHERE user_id = %s AND DATE(created_at) = CURRENT_DATE", (user_id,))
        row = c.fetchone()
        return row["count"] if row else 0

def log_video_watch(user_id, video_id, reward):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO video_watches (user_id, video_id, reward) VALUES (%s, %s, %s)", (user_id, video_id, reward))
        conn.commit()

# ==============================================
# DÉCORATEUR LOGIN REQUIRED
# ==============================================
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

# ==============================================
# ROUTES
# ==============================================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/inscription", methods=["GET", "POST"])
def inscription():
    ref = request.args.get("ref")
    if request.method == "POST":
        nom = request.form.get("nom", "").strip()
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        if not nom or not email or len(pw) < 6:
            flash("Champs invalides", "error")
            return render_template("inscription.html", ref=ref)
        if get_user_by_email(email):
            flash("Email déjà utilisé", "error")
            return render_template("inscription.html", ref=ref)
        pid = int(ref) if ref and ref.isdigit() else None
        with get_db() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO users (nom, email, password_hash, parrain_id, date_inscription) VALUES (%s,%s,%s,%s,%s)",
                      (nom, email, hash_pw(pw), pid, datetime.now().strftime("%Y-%m-%d %H:%M")))
            conn.commit()
        flash("Compte créé ! Connectez-vous.", "success")
        return redirect(url_for("login"))
    return render_template("inscription.html", ref=ref)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        u = get_user_by_email(email)
        if u and u["password_hash"] == hash_pw(pw):
            session["user_id"] = u["id"]
            return redirect(url_for("dashboard"))
        flash("Email ou mot de passe incorrect.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    u = get_user_by_id(session["user_id"])
    if not u:
        session.clear()
        return redirect(url_for("login"))
    return render_template("dashboard.html", user=u["nom"], balance=u.get("balance", 0), actif=u.get("actif", 0))

@app.route("/depot")
@login_required
def depot():
    u = get_user_by_id(session["user_id"])
    if u["actif"] == 1:
        flash("Vous êtes déjà actif", "info")
        return redirect(url_for("dashboard"))
    ref = hashlib.md5(f"{u['id']}{datetime.now()}".encode()).hexdigest()
    return render_template("depot.html", wallet=WALLET_USDT, montant=MEMBERSHIP_USDT, user_id=u["id"], ref=ref)

@app.route("/test_pay/<int:uid>")
@login_required
def test_pay(uid):
    if session.get("user_id") != uid:
        flash("Action non autorisée", "error")
        return redirect(url_for("dashboard"))
    result = process_payment(uid, "test_hash_" + secrets.token_hex(8))
    flash("Paiement simulé réussi !" if result else "Échec du paiement simulé.", "success" if result else "error")
    return redirect(url_for("dashboard"))

@app.route("/watch-ads")
@login_required
def watch_ads():
    user_id = session["user_id"]
    remaining = DAILY_VIDEO_LIMIT - get_today_video_count(user_id)
    return render_template("watch_ads.html", remaining=remaining, reward=REWARD_PER_VIDEO)

@app.route("/api/claim_reward", methods=["POST"])
@login_required
def claim_reward():
    user_id = session["user_id"]
    data = request.get_json()
    video_id = data.get("video_id")
    if get_today_video_count(user_id) >= DAILY_VIDEO_LIMIT:
        return jsonify({"success": False, "message": "Limite quotidienne atteinte"}), 400
    credit_user(user_id, REWARD_PER_VIDEO)
    log_video_watch(user_id, video_id, REWARD_PER_VIDEO)
    return jsonify({"success": True, "message": f"+{REWARD_PER_VIDEO} USDT crédités"})

@app.route("/hello")
def hello():
    return "<h1 style='color:green;'>✅ Code complet fonctionnel !</h1><p>Prix = 2.10 USDT | Vidéo = 0.50 USDT</p>"

# ==============================================
# LANCEMENT
# ==============================================
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
