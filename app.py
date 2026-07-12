import os
import sqlite3
import hashlib
import secrets
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from payment_checker import start_payment_checker

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

DB_PATH = "cashmoney.db"
ADMIN_ID_WEB = os.environ.get("ADMIN_EMAIL", "admin@cashmoney.com")
WALLET_USDT = os.environ.get("WALLET_USDT", "0xE4901E78F8c92199bAfD93AD87C5a250C48199c2")
MEMBERSHIP_USDT = 0.35
GAINS_NIVEAU = [2000, 1000, 2000, 1000, 2000, 1000, 2000, 1000, 2000, 1000, 2000, 1000]

# â”€â”€â”€ BASE DE DONNÃ‰ES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            email TEXT UNIQUE,
            password_hash TEXT,
            nom TEXT,
            username TEXT,
            parrain_id INTEGER,
            actif INTEGER DEFAULT 0,
            gains_total REAL DEFAULT 0,
            wallet_sender TEXT,
            date_inscription TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS paiements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            montant REAL,
            statut TEXT DEFAULT 'en_attente',
            tx_hash TEXT UNIQUE,
            wallet_sender TEXT,
            date_confirmation TEXT,
            date TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS commissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            beneficiaire_id INTEGER,
            source_id INTEGER,
            niveau INTEGER,
            montant REAL,
            date TEXT
        )
    """)
    conn.commit()
    conn.close()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_user_by_email(email):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    return user

def get_user_by_id(user_id):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return user

def get_parrain_chain(user_id, niveaux=12):
    chain = []
    conn = get_db()
    current_id = user_id
    for _ in range(niveaux):
        row = conn.execute("SELECT parrain_id FROM users WHERE id=?", (current_id,)).fetchone()
        if not row or not row["parrain_id"]:
            break
        parrain = conn.execute("SELECT id, actif FROM users WHERE id=?", (row["parrain_id"],)).fetchone()
        if parrain and parrain["actif"] == 1:
            chain.append(parrain["id"])
        current_id = row["parrain_id"]
    conn.close()
    return chain

def distribuer_commissions(nouveau_user_id):
    chain = get_parrain_chain(nouveau_user_id)
    conn = get_db()
    for i, parrain_id in enumerate(chain):
        if i >= len(GAINS_NIVEAU):
            break
        montant = GAINS_NIVEAU[i]
        conn.execute("""
            INSERT INTO commissions (beneficiaire_id, source_id, niveau, montant, date)
            VALUES (?, ?, ?, ?, ?)
        """, (parrain_id, nouveau_user_id, i+1, montant, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.execute("UPDATE users SET gains_total = gains_total + ? WHERE id=?", (montant, parrain_id))
    conn.commit()
    conn.close()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# â”€â”€â”€ ROUTES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/inscription", methods=["GET", "POST"])
def inscription():
    ref = request.args.get("ref")
    if request.method == "POST":
        nom = request.form.get("nom", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        parrain_code = request.form.get("parrain_code", "").strip()

        if not nom or not email or not password:
            flash("Tous les champs sont obligatoires.", "error")
            return render_template("inscription.html", ref=ref)

        if len(password) < 6:
            flash("Le mot de passe doit contenir au moins 6 caractÃ¨res.", "error")
            return render_template("inscription.html", ref=ref)

        existing = get_user_by_email(email)
        if existing:
            flash("Cet email est dÃ©jÃ  utilisÃ©.", "error")
            return render_template("inscription.html", ref=ref)

        parrain_id = None
        if parrain_code:
            conn = get_db()
            parrain = conn.execute("SELECT id FROM users WHERE id=?", (parrain_code,)).fetchone()
            conn.close()
            if parrain:
                parrain_id = parrain["id"]

        conn = get_db()
        conn.execute("""
            INSERT INTO users (nom, email, password_hash, parrain_id, date_inscription)
            VALUES (?, ?, ?, ?, ?)
        """, (nom, email, hash_password(password), parrain_id, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        conn.close()

        flash("Compte crÃ©Ã© ! Connectez-vous et activez votre compte.", "success")
        return redirect(url_for("login"))

    return render_template("inscription.html", ref=ref)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_user_by_email(email)
        if user and user["password_hash"] == hash_password(password):
            session["user_id"] = user["id"]
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
    user = get_user_by_id(session["user_id"])
    conn = get_db()
    filleuls = conn.execute(
        "SELECT COUNT(*) as n FROM users WHERE parrain_id=? AND actif=1", (user["id"],)
    ).fetchone()["n"]
    commissions = conn.execute(
        "SELECT * FROM commissions WHERE beneficiaire_id=? ORDER BY date DESC LIMIT 10", (user["id"],)
    ).fetchall()
    pending = conn.execute(
        "SELECT * FROM paiements WHERE user_id=? AND statut='en_attente'", (user["id"],)
    ).fetchone()
    conn.close()
    lien_parrainage = request.host_url + "inscription?ref=" + str(user["id"])
    return render_template("dashboard.html",
        user=user, filleuls=filleuls, commissions=commissions,
        pending=pending, lien_parrainage=lien_parrainage,
        wallet=WALLET_USDT, membership=MEMBERSHIP_USDT,
        gains_niveau=GAINS_NIVEAU
    )

@app.route("/payer", methods=["POST"])
@login_required
def payer():
    user = get_user_by_id(session["user_id"])
    if user["actif"] == 1:
        flash("Votre compte est dÃ©jÃ  actif !", "success")
        return redirect(url_for("dashboard"))
    wallet_sender = request.form.get("wallet_sender", "").strip()
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM paiements WHERE user_id=? AND statut='en_attente'", (user["id"],)
    ).fetchone()
    if not existing:
        conn.execute("""
            INSERT INTO paiements (user_id, montant, wallet_sender, date)
            VALUES (?, ?, ?, ?)
        """, (user["id"], MEMBERSHIP_USDT, wallet_sender or None,
              datetime.now().strftime("%Y-%m-%d %H:%M")))
        if wallet_sender:
            conn.execute("UPDATE users SET wallet_sender=? WHERE id=?", (wallet_sender, user["id"]))
        conn.commit()
    conn.close()
    flash("Paiement enregistrÃ© ! La vÃ©rification est automatique â€” votre compte sera activÃ© dans 1-2 minutes aprÃ¨s rÃ©ception.", "success")
    return redirect(url_for("dashboard"))

# â”€â”€â”€ ADMIN â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.route("/admin")
def admin_login_page():
    return render_template("admin_login.html")

@app.route("/admin/login", methods=["POST"])
def admin_login():
    email = request.form.get("email")
    password = request.form.get("password")
    admin_pass = os.environ.get("ADMIN_PASSWORD", "cashmoney2024")
    if email == ADMIN_ID_WEB and password == admin_pass:
        session["admin"] = True
        return redirect(url_for("admin_dashboard"))
    flash("Identifiants incorrects.", "error")
    return redirect(url_for("admin_login_page"))

@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect(url_for("admin_login_page"))
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as n FROM users").fetchone()["n"]
    actifs = conn.execute("SELECT COUNT(*) as n FROM users WHERE actif=1").fetchone()["n"]
    pending_payments = conn.execute("""
        SELECT p.*, u.nom, u.email FROM paiements p
        JOIN users u ON u.id = p.user_id
        WHERE p.statut='en_attente'
    """).fetchall()
    total_gains = conn.execute("SELECT SUM(gains_total) as s FROM users").fetchone()["s"] or 0
    conn.close()
    return render_template("admin.html",
        total=total, actifs=actifs,
        pending_payments=pending_payments, total_gains=int(total_gains)
    )

@app.route("/admin/confirmer/<int:paiement_id>")
def admin_confirmer(paiement_id):
    if not session.get("admin"):
        return redirect(url_for("admin_login_page"))
    conn = get_db()
    p = conn.execute("SELECT * FROM paiements WHERE id=?", (paiement_id,)).fetchone()
    if p:
        conn.execute("UPDATE paiements SET statut='confirme' WHERE id=?", (paiement_id,))
        conn.execute("UPDATE users SET actif=1 WHERE id=?", (p["user_id"],))
        conn.commit()
        distribuer_commissions(p["user_id"])
    conn.close()
    flash("Paiement confirmÃ© et commissions distribuÃ©es.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/rejeter/<int:paiement_id>")
def admin_rejeter(paiement_id):
    if not session.get("admin"):
        return redirect(url_for("admin_login_page"))
    conn = get_db()
    conn.execute("UPDATE paiements SET statut='rejete' WHERE id=?", (paiement_id,))
    conn.commit()
    conn.close()
    flash("Paiement rejetÃ©.", "info")
    return redirect(url_for("admin_dashboard"))

# â”€â”€â”€ MAIN â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def create_app():
    init_db()
    start_payment_checker(interval=60)
    return app

if __name__ == "__main__":
    create_app()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
