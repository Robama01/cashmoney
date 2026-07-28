import os
import secrets
import hashlib
from datetime import datetime
from functools import wraps
from decimal import Decimal, ROUND_HALF_UP

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL manquante")

TWOPLACES = Decimal("0.01")

def D(value):
    return Decimal(str(value)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)

MEMBERSHIP_USDT = D("2.10")
SYSTEM_FEE = D("0.37")
POOL_AMOUNT = MEMBERSHIP_USDT - SYSTEM_FEE

GAINS = [
    D("0.4325"), D("0.3460"), D("0.2595"), D("0.2076"), D("0.1730"),
    D("0.1211"), D("0.0865"), D("0.0519"), D("0.0346"), D("0.0173")
]

WALLET_USDT = os.environ.get("WALLET_USDT", "0xE4901E78F8c92199bAfD93Ad87C5a250C48199c2")
REWARD_PER_VIDEO = D("0.50")
DAILY_VIDEO_LIMIT = 10

SPONSORSHIP_PLAN = [
    {"niveau": 1, "nom": "Débutant", "membres": 2, "gain_invite": D("1.73"), "gains_totaux": D("3.46"), "mise_a_niveau": D("0.87"), "profit": D("2.59")},
    {"niveau": 2, "nom": "Influenceur", "membres": 4, "gain_invite": D("0.87"), "gains_totaux": D("3.48"), "mise_a_niveau": D("1.73"), "profit": D("1.75")},
    {"niveau": 3, "nom": "Achiever", "membres": 8, "gain_invite": D("1.73"), "gains_totaux": D("13.84"), "mise_a_niveau": D("3.46"), "profit": D("10.38")},
    {"niveau": 4, "nom": "Ambassadeur", "membres": 16, "gain_invite": D("3.46"), "gains_totaux": D("55.36"), "mise_a_niveau": D("6.92"), "profit": D("48.44")},
    {"niveau": 5, "nom": "Pionnier", "membres": 32, "gain_invite": D("6.92"), "gains_totaux": D("221.44"), "mise_a_niveau": D("13.84"), "profit": D("207.60")},
    {"niveau": 6, "nom": "Mentor", "membres": 64, "gain_invite": D("13.84"), "gains_totaux": D("885.76"), "mise_a_niveau": D("27.68"), "profit": D("858.08")},
    {"niveau": 7, "nom": "Champion", "membres": 128, "gain_invite": D("27.68"), "gains_totaux": D("3543.04"), "mise_a_niveau": D("55.36"), "profit": D("3487.68")},
    {"niveau": 8, "nom": "Director", "membres": 256, "gain_invite": D("55.36"), "gains_totaux": D("14172.16"), "mise_a_niveau": D("110.72"), "profit": D("14061.44")},
    {"niveau": 9, "nom": "Titan", "membres": 512, "gain_invite": D("110.72"), "gains_totaux": D("56688.64"), "mise_a_niveau": D("221.44"), "profit": D("56467.20")},
    {"niveau": 10, "nom": "Icon", "membres": 1024, "gain_invite": D("221.44"), "gains_totaux": D("226754.56"), "mise_a_niveau": D("442.88"), "profit": D("226311.68")},
]

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
            gains_total NUMERIC(18, 2) DEFAULT 0,
            balance NUMERIC(18, 2) DEFAULT 0,
            date_inscription TEXT,
            payment_hash TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS video_watches (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            video_id VARCHAR(50),
            reward NUMERIC(18, 2),
            created_at TIMESTAMP DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS admin_fees (
            id SERIAL PRIMARY KEY,
            amount NUMERIC(18, 2),
            description TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )""")
        conn.commit()
    app.logger.info("Base initialisée")

def hash_pw(p):
    return generate_password_hash(p)

def legacy_sha256(p):
    return hashlib.sha256(p.encode()).hexdigest()

def verify_and_migrate_password(user, password):
    stored = user["password_hash"] or ""
    if stored.startswith(("pbkdf2:", "scrypt:", "bcrypt:")):
        return check_password_hash(stored, password), None
    if stored == legacy_sha256(password):
        return True, hash_pw(password)
    return False, None

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
    amount = D(amount)
    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET balance = COALESCE(balance, 0) + %s WHERE id = %s", (amount, user_id))
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
    with get_db() as conn:
        try:
            c = conn.cursor()
            c.execute("SELECT * FROM users WHERE id=%s FOR UPDATE", (user_id,))
            user = c.fetchone()
            if not user or int(user["actif"]) == 1:
                conn.rollback()
                return False

            c.execute("INSERT INTO admin_fees (amount, description) VALUES (%s, %s)", (SYSTEM_FEE, "Frais système"))

            upline = get_upline(user_id)
            distributed = D("0.00")
            for level, sponsor_id in enumerate(upline):
                if sponsor_id and level < len(GAINS):
                    c.execute("UPDATE users SET balance = COALESCE(balance, 0) + %s WHERE id=%s", (GAINS[level], sponsor_id))
                    distributed += GAINS[level]

            remaining = POOL_AMOUNT - distributed
            if remaining > 0:
                c.execute("INSERT INTO admin_fees (amount, description) VALUES (%s, %s)", (remaining, "Reste non distribué"))

            c.execute("UPDATE users SET actif = 1, payment_hash = %s WHERE id = %s", (tx_hash, user_id))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            app.logger.exception("Erreur process_payment user_id=%s", user_id)
            raise

def get_today_video_count(user_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM video_watches WHERE user_id = %s AND DATE(created_at) = CURRENT_DATE", (user_id,))
        row = c.fetchone()
        return row["count"] if row else 0

def log_video_watch(user_id, video_id, reward):
    reward = D(reward)
    with get_db() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO video_watches (user_id, video_id, reward) VALUES (%s, %s, %s)", (user_id, video_id, reward))
        conn.commit()

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

@app.before_request
def log_request_info():
    app.logger.info("Request %s %s", request.method, request.path)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/plan")
def plan():
    total_gains = sum(item["gains_totaux"] for item in SPONSORSHIP_PLAN)
    return render_template("plan.html", plan=SPONSORSHIP_PLAN, total_gains=total_gains)

@app.route("/inscription", methods=["GET", "POST"])
def inscription():
    ref = request.args.get("ref")
    try:
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
                c.execute(
                    "INSERT INTO users (nom, email, password_hash, parrain_id, date_inscription) VALUES (%s,%s,%s,%s,%s)",
                    (nom, email, hash_pw(pw), pid, datetime.now().strftime("%Y-%m-%d %H:%M"))
                )
                conn.commit()
            return redirect(url_for("login"))
        return render_template("inscription.html", ref=ref)
    except Exception:
        app.logger.exception("Erreur inscription")
        flash("Erreur interne lors de l'inscription.", "error")
        return render_template("inscription.html", ref=ref), 500

@app.route("/login", methods=["GET", "POST"])
def login():
    try:
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            pw = request.form.get("password", "").strip()
            if not email or not pw:
                flash("Email et mot de passe obligatoires.", "error")
                return render_template("login.html")
            u = get_user_by_email(email)
            if not u:
                flash("Email ou mot de passe incorrect.", "error")
                return render_template("login.html")

            ok, new_hash = verify_and_migrate_password(u, pw)
            if ok:
                if new_hash:
                    with get_db() as conn:
                        c = conn.cursor()
                        c.execute("UPDATE users SET password_hash=%s WHERE id=%s", (new_hash, u["id"]))
                        conn.commit()
                session["user_id"] = u["id"]
                return redirect(url_for("dashboard"))

            flash("Email ou mot de passe incorrect.", "error")
            return render_template("login.html")

        return render_template("login.html")
    except Exception:
        app.logger.exception("Erreur login")
        flash("Erreur interne lors de la connexion.", "error")
        return render_template("login.html"), 500

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    try:
        u = get_user_by_id(session["user_id"])
        if not u:
            session.clear()
            return redirect(url_for("login"))
        return render_template("dashboard.html", user=u["nom"], balance=u.get("balance", 0), actif=u.get("actif", 0))
    except Exception:
        app.logger.exception("Erreur dashboard")
        return render_template("error.html", message="Erreur interne"), 500

@app.route("/depot")
@login_required
def depot():
    try:
        u = get_user_by_id(session["user_id"])
        if u["actif"] == 1:
            flash("Vous êtes déjà actif", "info")
            return redirect(url_for("dashboard"))
        ref = secrets.token_hex(16)
        return render_template("depot.html", wallet=WALLET_USDT, montant=MEMBERSHIP_USDT, user_id=u["id"], ref=ref)
    except Exception:
        app.logger.exception("Erreur depot")
        return render_template("error.html", message="Erreur interne"), 500

@app.route("/test_pay/<int:uid>")
@login_required
def test_pay(uid):
    try:
        if session.get("user_id") != uid:
            flash("Action non autorisée", "error")
            return redirect(url_for("dashboard"))
        result = process_payment(uid, "test_hash_" + secrets.token_hex(8))
        flash("Paiement simulé réussi !" if result else "Échec du paiement simulé.", "success" if result else "error")
        return redirect(url_for("dashboard"))
    except Exception:
        app.logger.exception("Erreur test_pay")
        flash("Erreur interne pendant le paiement simulé.", "error")
        return redirect(url_for("dashboard"))

@app.route("/watch-ads")
@login_required
def watch_ads():
    try:
        user_id = session["user_id"]
        remaining = DAILY_VIDEO_LIMIT - get_today_video_count(user_id)
        videos = [
            {"id": "video_1", "title": "Publicité 1", "description": "Regarde cette vidéo pour gagner 0.50 USDT"},
            {"id": "video_2", "title": "Publicité 2", "description": "Regarde cette vidéo pour gagner 0.50 USDT"},
            {"id": "video_3", "title": "Publicité 3", "description": "Regarde cette vidéo pour gagner 0.50 USDT"},
        ]
        return render_template("watch_ads.html", remaining=remaining, reward=REWARD_PER_VIDEO, videos=videos)
    except Exception:
        app.logger.exception("Erreur watch_ads")
        return render_template("error.html", message="Erreur interne"), 500

@app.route("/api/claim_reward", methods=["POST"])
@login_required
def claim_reward():
    try:
        user_id = session["user_id"]
        data = request.get_json(force=True)
        video_id = data.get("video_id")
        if get_today_video_count(user_id) >= DAILY_VIDEO_LIMIT:
            return jsonify({"success": False, "message": "Limite quotidienne atteinte"}), 400
        credit_user(user_id, REWARD_PER_VIDEO)
        log_video_watch(user_id, video_id, REWARD_PER_VIDEO)
        return jsonify({"success": True, "message": f"+{REWARD_PER_VIDEO} USDT crédités"})
    except Exception:
        app.logger.exception("Erreur claim_reward")
        return jsonify({"success": False, "message": "Erreur interne"}), 500

@app.errorhandler(404)
def not_found(error):
    return render_template("error.html", message="Page introuvable"), 404

@app.errorhandler(500)
def server_error(error):
    return render_template("error.html", message="Erreur interne du serveur"), 500

@app.route("/hello")
def hello():
    return render_template("index.html")

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
