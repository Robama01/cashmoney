import os
import hashlib
import secrets
import time
import logging
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash
import psycopg2
from psycopg2.extras import RealDictCursor
from web3 import Web3
from web3.exceptions import TransactionNotFound
from payment_checker import start_payment_checker

# ==============================================
# CONFIGURATION
# ==============================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@cashmoney.com")
WALLET_USDT = os.environ.get("WALLET_USDT", "0xE4901E78F8c92199bAfD93AD87C5a250C48199c2")
MEMBERSHIP_USDT = 0.35
GAINS = [2000, 1000, 2000, 1000, 2000, 1000, 2000, 1000, 2000, 1000, 2000, 1000]

# Configuration des logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==============================================
# CONFIGURATION BSC (Binance Smart Chain)
# ==============================================
BSC_NETWORK = "MAINNET"  # Mettez "TESTNET" pour les tests

if BSC_NETWORK == "MAINNET":
    BSC_RPC = "https://bsc-dataseed.binance.org/"
    CHAIN_ID = 56
    USDT_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"
else:
    BSC_RPC = "https://data-seed-prebsc-1-s1.binance.org:8545/"
    CHAIN_ID = 97
    USDT_CONTRACT = "0x337610d27c682E347C9cD60BD4b3b107C9d34dDd"

SENDER_ADDRESS = os.getenv('SENDER_ADDRESS')   # à ajouter sur Render
PRIVATE_KEY = os.getenv('PRIVATE_KEY')         # à ajouter sur Render

# Connexion Web3
w3 = Web3(Web3.HTTPProvider(BSC_RPC))
if not w3.is_connected():
    logger.error("❌ Échec de connexion à BSC")

# ABI du contrat USDT
USDT_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    }
]

contract = w3.eth.contract(address=Web3.to_checksum_address(USDT_CONTRACT), abi=USDT_ABI)
decimals = contract.functions.decimals().call()
logger.info(f"ℹ️ USDT decimals: {decimals}")

# ==============================================
# FONCTIONS UTILITAIRES
# ==============================================
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        email TEXT UNIQUE,
        password_hash TEXT,
        nom TEXT,
        parrain_id INTEGER,
        actif INTEGER DEFAULT 0,
        gains_total REAL DEFAULT 0,
        wallet_sender TEXT,
        usdt_wallet TEXT,
        date_inscription TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS paiements (
        id SERIAL PRIMARY KEY,
        user_id INTEGER,
        montant REAL,
        statut TEXT DEFAULT 'en_attente',
        tx_hash TEXT UNIQUE,
        wallet_sender TEXT,
        date_confirmation TEXT,
        date TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS commissions (
        id SERIAL PRIMARY KEY,
        beneficiaire_id INTEGER,
        source_id INTEGER,
        niveau INTEGER,
        montant REAL,
        date TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS retraits (
        id SERIAL PRIMARY KEY,
        user_id INTEGER,
        montant REAL,
        wallet TEXT,
        statut TEXT DEFAULT 'en_attente',
        date TEXT
    )""")
    conn.commit()
    conn.close()

def hp(p):
    return hashlib.sha256(p.encode()).hexdigest()

def gue(email):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE email=%s", (email,))
    u = c.fetchone()
    conn.close()
    return u

def gui(uid):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id=%s", (uid,))
    u = c.fetchone()
    conn.close()
    return u

def dist(uid):
    conn = get_db()
    c = conn.cursor()
    chain = []
    cur = uid
    for _ in range(12):
        c.execute("SELECT parrain_id FROM users WHERE id=%s", (cur,))
        r = c.fetchone()
        if not r or not r["parrain_id"]:
            break
        c.execute("SELECT id, actif FROM users WHERE id=%s", (r["parrain_id"],))
        p = c.fetchone()
        if p and p["actif"] == 1:
            chain.append(p["id"])
        cur = r["parrain_id"]
    for i, pid in enumerate(chain):
        if i >= len(GAINS):
            break
        m = GAINS[i]
        c.execute("INSERT INTO commissions (beneficiaire_id, source_id, niveau, montant, date) VALUES (%s,%s,%s,%s,%s)",
                  (pid, uid, i+1, m, datetime.now().strftime("%Y-%m-%d %H:%M")))
        c.execute("UPDATE users SET gains_total=gains_total+%s WHERE id=%s", (m, pid))
    conn.commit()
    conn.close()

def lr(f):
    @wraps(f)
    def d(*a, **k):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*a, **k)
    return d

# ==============================================
# NOUVELLES FONCTIONS BSC (PAIEMENTS AUTO)
# ==============================================

def send_usdt_auto(to_address, amount_usdt):
    """
    Envoie automatiquement des USDT à une adresse.
    Retourne le hash de la transaction ou None en cas d'erreur.
    """
    if not PRIVATE_KEY or not SENDER_ADDRESS:
        logger.error("❌ Clés manquantes dans les variables d'environnement.")
        return None

    if not to_address:
        logger.error("❌ Adresse du destinataire vide.")
        return None

    try:
        to_address = Web3.to_checksum_address(to_address)
    except Exception as e:
        logger.error(f"❌ Adresse du destinataire invalide : {e}")
        return None

    if amount_usdt <= 0:
        logger.warning("⚠️ Montant = 0, transaction annulée.")
        return None

    amount_wei = int(amount_usdt * (10 ** decimals))
    logger.info(f"📦 Envoi de {amount_usdt} USDT à {to_address}")

    # Vérifier le solde du SENDER
    sender_balance_wei = contract.functions.balanceOf(SENDER_ADDRESS).call()
    sender_balance_usdt = sender_balance_wei / (10 ** decimals)
    if sender_balance_wei < amount_wei:
        logger.error(f"❌ Solde insuffisant : {sender_balance_usdt} USDT disponible, {amount_usdt} USDT requis.")
        return None

    try:
        nonce = w3.eth.get_transaction_count(SENDER_ADDRESS, 'pending')

        # Estimer le gas
        try:
            gas_estimate = contract.functions.transfer(to_address, amount_wei).estimate_gas({
                'from': SENDER_ADDRESS,
                'nonce': nonce
            })
            gas_limit = int(gas_estimate * 1.1)
        except:
            gas_limit = 100000

        gas_price = w3.eth.gas_price

        txn = contract.functions.transfer(
            to_address,
            amount_wei
        ).build_transaction({
            'chainId': CHAIN_ID,
            'gas': gas_limit,
            'gasPrice': gas_price,
            'nonce': nonce,
            'from': SENDER_ADDRESS,
        })

        signed_txn = w3.eth.account.sign_transaction(txn, private_key=PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)
        tx_hash_hex = tx_hash.hex()

        logger.info(f"✅ Transaction envoyée. Hash: {tx_hash_hex}")

        # Attendre la confirmation (max 60s)
        for i in range(12):
            time.sleep(5)
            try:
                receipt = w3.eth.get_transaction_receipt(tx_hash)
                if receipt is not None and receipt['status'] == 1:
                    logger.info(f"✅ Transaction confirmée ! Hash: {tx_hash_hex}")
                    return tx_hash_hex
                elif receipt is not None and receipt['status'] == 0:
                    logger.error(f"❌ Transaction échouée. Hash: {tx_hash_hex}")
                    return None
            except TransactionNotFound:
                continue

        logger.warning(f"⏰ Timeout: transaction {tx_hash_hex} soumise mais pas confirmée.")
        return tx_hash_hex

    except Exception as e:
        logger.error(f"❌ Erreur lors de l'envoi: {e}")
        return None


def check_and_pay_balance(user_id, seuil=5.00):
    """
    Vérifie si le solde de l'utilisateur atteint le seuil.
    Si oui, envoie automatiquement le montant total sur son wallet USDT.
    """
    conn = get_db()
    c = conn.cursor()

    # 1. Récupérer l'utilisateur
    c.execute("SELECT * FROM users WHERE id=%s", (user_id,))
    user = c.fetchone()
    if not user:
        logger.error(f"❌ Utilisateur ID {user_id} introuvable.")
        conn.close()
        return False

    # 2. Vérifier si le seuil est atteint
    if user["gains_total"] < seuil:
        logger.info(f"ℹ️ {user['email']} | Solde: {user['gains_total']}$ | Seuil: {seuil}$ - En attente.")
        conn.close()
        return False

    # 3. Vérifier que l'utilisateur a un wallet USDT
    if not user.get("usdt_wallet") or user["usdt_wallet"] == "":
        logger.warning(f"⚠️ {user['email']} a {user['gains_total']}$ mais PAS de wallet USDT.")
        conn.close()
        return False

    # 4. Préparer l'envoi
    amount_to_send = round(user["gains_total"], 2)
    logger.info(f"🚀 Déclenchement paiement pour {user['email']} | Montant: {amount_to_send}$")

    # 5. Envoyer la transaction
    tx_hash = send_usdt_auto(user["usdt_wallet"], amount_to_send)

    # 6. Traiter le résultat
    if tx_hash:
        c.execute("UPDATE users SET gains_total=0 WHERE id=%s", (user_id,))
        # Optionnel : enregistrer dans une table PaymentHistory si vous en avez une
        conn.commit()
        conn.close()
        logger.info(f"✅ Paiement de {amount_to_send}$ envoyé à {user['email']}. Solde remis à 0.")
        logger.info(f"   Transaction: {tx_hash}")
        return True
    else:
        logger.error(f"❌ Échec du paiement pour {user['email']}. Solde conservé ({user['gains_total']}$).")
        conn.close()
        return False


def process_new_user_payment(new_user_id):
    """
    Appelé automatiquement quand un nouveau filleul paie.
    Parcourt les 12 niveaux, ajoute les commissions et vérifie les seuils.
    """
    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT * FROM users WHERE id=%s", (new_user_id,))
    new_user = c.fetchone()
    if not new_user:
        logger.error(f"❌ Utilisateur ID {new_user_id} introuvable.")
        conn.close()
        return

    sponsor_id = new_user["parrain_id"]
    level = 1
    commission_base = 0.35  # correspond à votre MEMBERSHIP_USDT

    logger.info(f"🔄 Traitement paiement de {new_user['email']} (ID: {new_user_id})")

    while sponsor_id and level <= 12:
        c.execute("SELECT * FROM users WHERE id=%s", (sponsor_id,))
        sponsor = c.fetchone()
        if not sponsor:
            break

        # Calcul de la commission en FCFA (comme dans GAINS)
        # Mais on veut aussi la valeur en USD pour le seuil
        commission_fcfa = GAINS[level - 1] if level <= len(GAINS) else 0
        commission_usd = round(commission_fcfa / 600, 2)  # conversion FCFA → USD

        # Ajout au solde (en FCFA)
        c.execute("UPDATE users SET gains_total=gains_total+%s WHERE id=%s", (commission_fcfa, sponsor["id"]))
        conn.commit()

        logger.info(f"💰 +{commission_fcfa} FCFA (+{commission_usd}$) ajouté à {sponsor['email']} (Niveau {level}).")

        # Seuil = commission_usd * 10 (minimum 5$)
        seuil = max(5.00, round(commission_usd * 10))

        # Déclencher la vérification du seuil (en dollars)
        conn.close()  # on ferme avant d'appeler check_and_pay_balance qui ouvre sa propre connexion
        check_and_pay_balance(sponsor["id"], seuil)
        conn = get_db()  # on rouvre pour la suite de la boucle
        c = conn.cursor()

        sponsor_id = sponsor["parrain_id"]
        level += 1

    conn.close()
    logger.info(f"✅ Traitement terminé pour {new_user['email']}")

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
        pc = request.form.get("parrain_code", "").strip()
        if not nom or not email or not pw:
            flash("Tous les champs sont obligatoires.", "error")
            return render_template("inscription.html", ref=ref)
        if len(pw) < 6:
            flash("Mot de passe trop court.", "error")
            return render_template("inscription.html", ref=ref)
        if gue(email):
            flash("Email déjà utilisé.", "error")
            return render_template("inscription.html", ref=ref)
        pid = None
        if pc:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE id=%s", (pc,))
            p = cur.fetchone()
            conn.close()
            if p:
                pid = p["id"]
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO users (nom, email, password_hash, parrain_id, date_inscription) VALUES (%s,%s,%s,%s,%s)",
                    (nom, email, hp(pw), pid, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        conn.close()
        flash("Compte créé ! Connectez-vous.", "success")
        return redirect(url_for("login"))
    return render_template("inscription.html", ref=ref)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        u = gue(email)
        if u and u["password_hash"] == hp(pw):
            session["user_id"] = u["id"]
            return redirect(url_for("dashboard"))
        flash("Email ou mot de passe incorrect.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/dashboard")
@lr
def dashboard():
    u = gui(session["user_id"])
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as n FROM users WHERE parrain_id=%s AND actif=1", (u["id"],))
    f = c.fetchone()["n"]
    c.execute("SELECT * FROM commissions WHERE beneficiaire_id=%s ORDER BY date DESC LIMIT 10", (u["id"],))
    cm = c.fetchall()
    c.execute("SELECT * FROM paiements WHERE user_id=%s AND statut='en_attente'", (u["id"],))
    p = c.fetchone()
    conn.close()
    lien = request.host_url + "inscription?ref=" + str(u["id"])
    return render_template("dashboard.html",
                           user=u["nom"],              # nom de l'utilisateur
                           gains=u["gains_total"],     # total des gains en FCFA
                           filleuls=f,
                           commissions=cm,
                           pending=p,
                           lien_parrainage=lien,
                           wallet=WALLET_USDT,
                           membership=MEMBERSHIP_USDT,
                           gains_niveau=GAINS)

@app.route("/payer", methods=["POST"])
@lr
def payer():
    u = gui(session["user_id"])
    if u["actif"] == 1:
        flash("Compte déjà actif !", "success")
        return redirect(url_for("dashboard"))
    ws = request.form.get("wallet_sender", "").strip()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM paiements WHERE user_id=%s AND statut='en_attente'", (u["id"],))
    ex = c.fetchone()
    if not ex:
        c.execute("INSERT INTO paiements (user_id, montant, wallet_sender, date) VALUES (%s,%s,%s,%s)",
                  (u["id"], MEMBERSHIP_USDT, ws or None, datetime.now().strftime("%Y-%m-%d %H:%M")))
        if ws:
            c.execute("UPDATE users SET wallet_sender=%s WHERE id=%s", (ws, u["id"]))
        conn.commit()
    conn.close()
    flash("Paiement enregistré ! Vérification automatique en cours.", "success")
    return redirect(url_for("dashboard"))

@app.route("/admin")
def admin_login_page():
    return render_template("admin_login.html")

@app.route("/admin/login", methods=["POST"])
def admin_login():
    email = request.form.get("email")
    pw = request.form.get("password")
    if email == ADMIN_EMAIL and pw == os.environ.get("ADMIN_PASSWORD", "cashmoney2024"):
        session["admin"] = True
        return redirect(url_for("admin_dashboard"))
    flash("Identifiants incorrects.", "error")
    return redirect(url_for("admin_login_page"))

@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect(url_for("admin_login_page"))
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as n FROM users")
    total = c.fetchone()["n"]
    c.execute("SELECT COUNT(*) as n FROM users WHERE actif=1")
    actifs = c.fetchone()["n"]
    c.execute("SELECT p.*, u.nom, u.email FROM paiements p JOIN users u ON u.id=p.user_id WHERE p.statut='en_attente'")
    pp = c.fetchall()
    c.execute("SELECT SUM(gains_total) as s FROM users")
    gains = c.fetchone()["s"] or 0
    conn.close()
    return render_template("admin.html", total=total, actifs=actifs, pending_payments=pp, total_gains=int(gains))

@app.route("/admin/confirmer/<int:pid>")
def admin_confirmer(pid):
    if not session.get("admin"):
        return redirect(url_for("admin_login_page"))
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM paiements WHERE id=%s", (pid,))
    p = c.fetchone()
    if p:
        c.execute("UPDATE paiements SET statut='confirme' WHERE id=%s", (pid,))
        c.execute("UPDATE users SET actif=1 WHERE id=%s", (p["user_id"],))
        conn.commit()
        # Distribuer les commissions
        dist(p["user_id"])
        # 🔥 Déclencher les paiements automatiques pour ce nouvel utilisateur
        process_new_user_payment(p["user_id"])
    conn.close()
    flash("Confirmé et commissions distribuées !", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/rejeter/<int:pid>")
def admin_rejeter(pid):
    if not session.get("admin"):
        return redirect(url_for("admin_login_page"))
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE paiements SET statut='rejete' WHERE id=%s", (pid,))
    conn.commit()
    conn.close()
    flash("Rejeté.", "info")
    return redirect(url_for("admin_dashboard"))

@app.route("/retrait", methods=["GET", "POST"])
@lr
def retrait():
    u = gui(session["user_id"])
    if u["actif"] == 0:
        flash("Activez votre compte d'abord.", "error")
        return redirect(url_for("dashboard"))
    conn = get_db()
    c = conn.cursor()
    if request.method == "POST":
        montant = int(request.form.get("montant", 0))
        wallet = request.form.get("wallet", "").strip()
        if montant < 1000:
            flash("Montant minimum 1000 FCFA.", "error")
        elif montant > u["gains_total"]:
            flash("Solde insuffisant.", "error")
        elif not wallet:
            flash("Adresse wallet obligatoire.", "error")
        else:
            c.execute("INSERT INTO retraits (user_id, montant, wallet, statut, date) VALUES (%s,%s,%s,'en_attente',%s)",
                      (u["id"], montant, wallet, datetime.now().strftime("%Y-%m-%d %H:%M")))
            c.execute("UPDATE users SET gains_total=gains_total-%s WHERE id=%s", (montant, u["id"]))
            conn.commit()
            flash("Demande de retrait soumise ! L'admin va traiter votre demande.", "success")
            return redirect(url_for("retrait"))
    c.execute("SELECT * FROM retraits WHERE user_id=%s ORDER BY date DESC", (u["id"],))
    retraits = c.fetchall()
    conn.close()
    return render_template("retrait.html", user=u, retraits=retraits)

# ==============================================
# LANCEMENT DE L'APPLICATION
# ==============================================

# Initialiser la base de données (au premier démarrage)
init_db()

# Démarrer le vérificateur de paiements (background)
start_payment_checker(interval=60)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
