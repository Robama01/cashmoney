import os
import hashlib
import secrets
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor

# ==============================================
# INITIALISATION DE L'APPLICATION
# ==============================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL manquante dans les variables d'environnement.")

# ==============================================
# CONFIGURATION FINANCIÈRE
# ==============================================
MEMBERSHIP_USDT = 2.10          # Prix d'adhésion
SYSTEM_FEE = 0.37               # Frais de maintenance + retraits futurs
POOL_AMOUNT = MEMBERSHIP_USDT - SYSTEM_FEE  # = 1.73

# Commissions par niveau (10 niveaux) - total = POOL_AMOUNT
GAINS = [
    0.4325,  # Niveau 1 (parent direct)
    0.3460,  # Niveau 2
    0.2595,  # Niveau 3
    0.2076,  # Niveau 4
    0.1730,  # Niveau 5
    0.1211,  # Niveau 6
    0.0865,  # Niveau 7
    0.0519,  # Niveau 8
    0.0346,  # Niveau 9
    0.0173   # Niveau 10
]

# Adresse USDT pour les paiements
WALLET_USDT = os.environ.get("WALLET_USDT", "0xE4901E78F8c92199bAfD93AD87C5a250C48199c2")

# Configuration des vidéos
REWARD_PER_VIDEO = 0.50         # 0.50 USDT par vidéo
DAILY_VIDEO_LIMIT = 10          # 10 vidéos par jour (5 USDT max/jour)

# ==============================================
# FONCTIONS BASE DE DONNÉES
# ==============================================
def get_db():
    """Retourne une connexion à la base PostgreSQL avec RealDictCursor."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    """Crée les tables et ajoute les colonnes manquantes."""
    with get_db() as conn:
        c = conn.cursor()
        
        # Table users
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
        
        # Ajouter les colonnes si elles n'existent pas (pour compatibilité)
        c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='balance'")
        if not c.fetchone():
            c.execute("ALTER TABLE users ADD COLUMN balance REAL DEFAULT 0")
        
        c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='payment_hash'")
        if not c.fetchone():
            c.execute("ALTER TABLE users ADD COLUMN payment_hash TEXT")
        
        # Table video_watches
        c.execute("""CREATE TABLE IF NOT EXISTS video_watches (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            video_id VARCHAR(50),
            reward REAL,
            created_at TIMESTAMP DEFAULT NOW()
        )""")
        
        # Table admin_fees
        c.execute("""CREATE TABLE IF NOT EXISTS admin_fees (
            id SERIAL PRIMARY KEY,
            amount REAL,
            description TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )""")
        
        conn.commit()

# ==============================================
# FONCTIONS UTILITAIRES
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
    """Ajoute un montant au solde (balance) de l'utilisateur."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (amount, user_id))
        conn.commit()

def get_user_balance(user_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE id = %s", (user_id,))
        row = c.fetchone()
        return row["balance"] if row else 0

def add_admin_fee(amount, description="Frais système"):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO admin_fees (amount, description) VALUES (%s, %s)", (amount, description))
        conn.commit()

# ==============================================
# FONCTION MATRICE (downline)
# ==============================================
def get_downline_counts(user_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, parrain_id, actif FROM users")
        all_users = c.fetchall()

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
                    if child["actif"] == 1:
                        counts[niveau - 1] += 1
                    next_level.append(child["id"])
        current_level = next_level
        if not current_level:
            break

    return counts

# ==============================================
# FONCTION DE TRAITEMENT DU PAIEMENT
# ==============================================
def get_upline(user_id, depth=10):
    """Retourne la liste des IDs des parrains jusqu'à 'depth' niveaux."""
    upline = []
    current_id = user_id
    for _ in range(depth):
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT parrain_id FROM users WHERE id = %s", (current_id,))
            row = c.fetchone()
            if row and row["parrain_id"]:
                sponsor_id = row["parrain_id"]
                upline.append(sponsor_id)
                current_id = sponsor_id
            else:
                upline.append(None)
                break
    # Compléter avec None si moins de 'depth'
    while len(upline) < depth:
        upline.append(None)
    return upline

def process_payment(user_id, transaction_hash):
    """
    Fonction appelée quand un paiement de 2.10 USDT est confirmé.
    Retourne True si tout s'est bien passé.
    """
    try:
        # Vérifier que l'utilisateur n'est pas déjà actif
        user = get_user_by_id(user_id)
        if user["actif"] == 1:
            return False  # déjà payé

        # 1. Prélever les frais système (0.37 USDT)
        add_admin_fee(SYSTEM_FEE, f"Frais pour l'utilisateur {user_id}")
        
        # 2. Récupérer l'upline (10 parrains)
        upline = get_upline(user_id, depth=10)
        distributed = 0.0
        
        # 3. Distribuer les commissions
        for level, sponsor_id in enumerate(upline):
            if sponsor_id is not None and level < len(GAINS):
                commission = GAINS[level]
                credit_user(sponsor_id, commission)
                distributed += commission
        
        # 4. S'il reste de l'argent (upline incomplète), on l'ajoute aux frais
        remaining = POOL_AMOUNT - distributed
        if remaining > 0:
            add_admin_fee(remaining, "Reste non distribué (upline incomplète)")
        
        # 5. Marquer l'utilisateur comme actif
        with get_db() as conn:
            c = conn.cursor()
            c.execute("UPDATE users SET actif = 1, payment_hash = %s WHERE id = %s", (transaction_hash, user_id))
            conn.commit()
        
        return True
    except Exception as e:
        print(f"Erreur lors du traitement du paiement : {e}")
        return False

# ==============================================
# FONCTIONS POUR LES VIDÉOS
# ==============================================
def get_today_video_count(user_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM video_watches WHERE user_id = %s AND DATE(created_at) = CURRENT_DATE", (user_id,))
        row = c.fetchone()
        return row["count"] if row else 0

def has_watched_today(user_id, video_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM video_watches WHERE user_id = %s AND video_id = %s AND DATE(created_at) = CURRENT_DATE", (user_id, video_id))
        return c.fetchone() is not None

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

    matrice = get_downline_counts(u["id"])
    lien_parrainage = request.host_url + "inscription?ref=" + str(u["id"])
    balance = u.get("balance", 0)
    actif = u.get("actif", 0)

    return render_template("dashboard.html",
                           user=u["nom"],
                           gains=u["gains_total"],
                           balance=balance,
                           actif=actif,
                           filleuls=filleuls,
                           lien_parrainage=lien_parrainage,
                           matrice=matrice)

@app.route("/depot")
@login_required
def depot():
    """Page de dépôt pour payer l'adhésion."""
    u = get_user_by_id(session["user_id"])
    if u["actif"] == 1:
        flash("Vous êtes déjà actif !", "info")
        return redirect(url_for("dashboard"))
    
    # Générer une référence de transaction (hash temporaire)
    ref = hashlib.md5(f"{u['id']}{datetime.now()}".encode()).hexdigest()
    return render_template("depot.html",
                           wallet=WALLET_USDT,
                           montant=MEMBERSHIP_USDT,
                           user_id=u["id"],
                           ref=ref)

@app.route("/admin")
def admin():
    # Page admin simple, vous pouvez la personnaliser
    return "<h1>Page Admin</h1><p>Si vous voyez ceci, la route fonctionne !</p>"

# ==============================================
# ROUTE DE TEST POUR SIMULER UN PAIEMENT
# ==============================================
@app.route("/test_pay/<int:uid>")
@login_required
def test_pay(uid):
    """Simule un paiement pour l'utilisateur 'uid'."""
    # Sécurité : seul l'utilisateur lui-même peut tester (à adapter)
    if session.get("user_id") != uid:
        flash("Vous ne pouvez pas simuler un paiement pour un autre utilisateur.", "error")
        return redirect(url_for("dashboard"))
    
    result = process_payment(uid, "test_hash_" + secrets.token_hex(8))
    if result:
        flash("✅ Paiement simulé effectué avec succès !", "success")
    else:
        flash("❌ Échec du paiement simulé (utilisateur déjà actif ou erreur).", "error")
    return redirect(url_for("dashboard"))

# ==============================================
# ROUTES POUR LES VIDÉOS PUBLICITAIRES
# ==============================================
@app.route("/watch-ads")
@login_required
def watch_ads():
    user_id = session["user_id"]
    today_watched = get_today_video_count(user_id)
    remaining = DAILY_VIDEO_LIMIT - today_watched
    return render_template("watch_ads.html",
                           remaining=remaining,
                           reward=REWARD_PER_VIDEO)

@app.route("/api/claim_reward", methods=["POST"])
@login_required
def claim_reward():
    user_id = session["user_id"]
    data = request.get_json()
    video_id = data.get("video_id")
    
    # Vérifier la limite quotidienne
    today_watched = get_today_video_count(user_id)
    if today_watched >= DAILY_VIDEO_LIMIT:
        return jsonify({"success": False, "message": "Limite quotidienne atteinte !"}), 400
    
    # Vérifier que la vidéo n'a pas déjà été regardée aujourd'hui
    if has_watched_today(user_id, video_id):
        return jsonify({"success": False, "message": "Vous avez déjà regardé cette vidéo."}), 400
    
    # Créditer l'utilisateur
    credit_user(user_id, REWARD_PER_VIDEO)
    log_video_watch(user_id, video_id, REWARD_PER_VIDEO)
    
    new_balance = get_user_balance(user_id)
    return jsonify({
        "success": True,
        "message": f"Félicitations ! +{REWARD_PER_VIDEO} USDT crédités.",
        "new_balance": new_balance
    })

# ==============================================
# POINT D'ENTRÉE - LANCEMENT DE L'APPLICATION
# ==============================================
if __name__ == "__main__":
    # Initialiser la base de données (créer les tables si elles n'existent pas)
    init_db()
    
    # Démarrer le serveur Flask
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
