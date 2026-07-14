import os
import hashlib
import secrets
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL manquante")

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
            date_inscription TEXT
        )""")
        conn.commit()

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

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

# =========================================================
# FONCTION MATRICE (version Python pur, sans CTE)
# =========================================================
def get_downline_counts(user_id):
    """Compte les filleuls actifs jusqu'au niveau 12."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, parrain_id, actif FROM users")
        all_users = c.fetchall()

    # Construire un dictionnaire : parrain_id -> [liste de ses filleuls]
    parrain_map = {}
    for u in all_users:
        pid = u["parrain_id"]
        if pid not in parrain_map:
            parrain_map[pid] = []
        parrain_map[pid].append(u)

    counts = [0] * 12
    current_level = [user_id]

    for niveau in range(1, 13):
        next_level = []
        for uid in current_level:
            if uid in parrain_map:
                for child in parrain_map[uid]:
                    if child["actif"] == 1:        # on compte seulement les actifs
                        counts[niveau - 1] += 1
                    next_level.append(child["id"])
        current_level = next_level
        if not current_level:
            break

    return counts

# =========================================================
# ROUTES
# =========================================================
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
            flash("Champs invalides.", "error")
            return render_template("inscription.html", ref=ref)
        if get_user_by_email(email):
            flash("Email déjà utilisé.", "error")
            return render_template("inscription.html", ref=ref)
        pid = None
        if ref and ref.isdigit():
            pid = int(ref)
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

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as n FROM users WHERE parrain_id=%s AND actif=1", (u["id"],))
        filleuls = c.fetchone()["n"]

    # On calcule la matrice
    matrice = get_downline_counts(u["id"])

    lien_parrainage = request.host_url + "inscription?ref=" + str(u["id"])

    return render_template("dashboard.html",
                           user=u["nom"],
           @app.route('/admin')
def admin():
    return "<h1>Page Admin</h1><p>Si vous voyez ceci, la route fonctionne !</p>"                gains=u["gains_total"],
                           filleuls=filleuls,
                           lien_parrainage=lien_parrainage,
                           matrice=matrice)   # <-- on envoie bien la matrice
    

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
