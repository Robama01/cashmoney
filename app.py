import os
import bcrypt
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify
from flask_jwt_extended import (
    JWTManager, create_access_token,
    jwt_required, get_jwt_identity
)
from web3 import Web3

# ---------- CONFIG BLOCKCHAIN (USDT BEP20 / Binance Smart Chain) ----------

BSC_RPC_URL = os.environ.get("BSC_RPC_URL", "https://bsc-dataseed.binance.org/")
PAYOUT_WALLET_PRIVATE_KEY = os.environ.get("PAYOUT_WALLET_PRIVATE_KEY")  # ⚠️ jamais en dur ici
USDT_BEP20_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"  # adresse officielle USDT sur BSC

ERC20_ABI = [
    {
        "constant": False,
        "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
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

w3 = Web3(Web3.HTTPProvider(BSC_RPC_URL))
usdt_contract = w3.eth.contract(address=Web3.to_checksum_address(USDT_BEP20_CONTRACT), abi=ERC20_ABI)


def send_usdt_bep20(to_address: str, amount_usdt: float):
    """
    Envoie amount_usdt en USDT (BEP20) vers to_address depuis le wallet de paiement.
    Retourne (success: bool, tx_hash_or_error: str)
    """
    if not PAYOUT_WALLET_PRIVATE_KEY:
        return False, "PAYOUT_WALLET_PRIVATE_KEY non configurée"

    try:
        account = w3.eth.account.from_key(PAYOUT_WALLET_PRIVATE_KEY)
        to_checksum = Web3.to_checksum_address(to_address)

        # USDT BEP20 utilise 18 décimales
        amount_wei = int(amount_usdt * (10 ** 18))

        nonce = w3.eth.get_transaction_count(account.address)
        gas_price = w3.eth.gas_price

        tx = usdt_contract.functions.transfer(to_checksum, amount_wei).build_transaction({
            "chainId": 56,  # BSC mainnet
            "gas": 100000,
            "gasPrice": gas_price,
            "nonce": nonce,
        })

        signed_tx = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

        return True, tx_hash.hex()

    except Exception as e:
        return False, str(e)

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

# Change cette clé secrète sur Render (variable JWT_SECRET_KEY), ne la laisse jamais en dur en prod
app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY", "change-moi-absolument")
jwt = JWTManager(app)

# Seuil de retrait automatique (en FCFA, ou l'unité que tu utilises)
WITHDRAWAL_THRESHOLD = float(os.environ.get("WITHDRAWAL_THRESHOLD", 1000))


def get_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            wallet_address TEXT,
            balance NUMERIC DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id SERIAL PRIMARY KEY,
            youtube_id TEXT NOT NULL,
            title TEXT NOT NULL,
            sponsor_name TEXT,
            reward_amount NUMERIC NOT NULL,
            min_watch_seconds INTEGER DEFAULT 30,
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS watch_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            video_id INTEGER REFERENCES videos(id),
            watched_seconds INTEGER NOT NULL,
            reward_given NUMERIC NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (user_id, video_id)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payouts (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            amount NUMERIC NOT NULL,
            status TEXT DEFAULT 'pending',
            tx_hash TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    conn.commit()
    cur.close()
    conn.close()


# ---------- AUTHENTIFICATION ----------

@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")
    wallet_address = data.get("wallet_address")

    if not email or not password:
        return jsonify({"error": "email et password requis"}), 400

    if wallet_address and not Web3.is_address(wallet_address):
        return jsonify({"error": "wallet_address invalide (doit être une adresse BEP20 valide)"}), 400

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (email, password_hash, wallet_address) VALUES (%s, %s, %s) RETURNING id, email;",
            (email, password_hash, wallet_address)
        )
        user = cur.fetchone()
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"error": "Cet email est déjà utilisé"}), 409
    finally:
        cur.close()
        conn.close()

    token = create_access_token(identity=user["id"])
    return jsonify({"user": user, "access_token": token}), 201


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = %s;", (email,))
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401

    token = create_access_token(identity=user["id"])
    return jsonify({"access_token": token, "balance": float(user["balance"])})


# ---------- VIDÉOS ----------

@app.route("/videos", methods=["GET"])
def list_videos():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM videos WHERE active = TRUE ORDER BY created_at DESC;")
    videos = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(videos)


@app.route("/videos", methods=["POST"])
def add_video():
    # ⚠️ À sécuriser plus tard avec un rôle "admin" avant la mise en production
    data = request.get_json()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO videos (youtube_id, title, sponsor_name, reward_amount, min_watch_seconds)
           VALUES (%s, %s, %s, %s, %s) RETURNING *;""",
        (data.get("youtube_id"), data.get("title"), data.get("sponsor_name"),
         data.get("reward_amount"), data.get("min_watch_seconds", 30))
    )
    video = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return jsonify(video), 201


# ---------- VISIONNAGE & GAINS ----------

@app.route("/watch", methods=["POST"])
@jwt_required()
def watch_video():
    user_id = get_jwt_identity()
    data = request.get_json()
    video_id = data.get("video_id")
    watched_seconds = data.get("watched_seconds", 0)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM videos WHERE id = %s AND active = TRUE;", (video_id,))
    video = cur.fetchone()
    if not video:
        cur.close()
        conn.close()
        return jsonify({"error": "Vidéo introuvable"}), 404

    if watched_seconds < video["min_watch_seconds"]:
        cur.close()
        conn.close()
        return jsonify({
            "error": f"Il faut regarder au moins {video['min_watch_seconds']} secondes"
        }), 400

    try:
        cur.execute(
            """INSERT INTO watch_logs (user_id, video_id, watched_seconds, reward_given)
               VALUES (%s, %s, %s, %s);""",
            (user_id, video_id, watched_seconds, video["reward_amount"])
        )
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"error": "Tu as déjà été récompensé pour cette vidéo"}), 409

    cur.execute(
        "UPDATE users SET balance = balance + %s WHERE id = %s RETURNING balance;",
        (video["reward_amount"], user_id)
    )
    new_balance = cur.fetchone()["balance"]
    conn.commit()

    # Déclenche un retrait automatique si le seuil est atteint
    payout_triggered = False
    if float(new_balance) >= WITHDRAWAL_THRESHOLD:
        trigger_payout(cur, user_id, float(new_balance))
        conn.commit()
        payout_triggered = True

    cur.close()
    conn.close()

    return jsonify({
        "reward_given": float(video["reward_amount"]),
        "new_balance": float(new_balance),
        "payout_triggered": payout_triggered
    })


def trigger_payout(cur, user_id, amount):
    """
    Envoie automatiquement l'USDT (BEP20) vers le wallet de l'utilisateur,
    enregistre le résultat, et remet le solde à zéro si l'envoi réussit.
    """
    cur.execute("SELECT wallet_address FROM users WHERE id = %s;", (user_id,))
    user = cur.fetchone()
    wallet_address = user["wallet_address"] if user else None

    if not wallet_address:
        cur.execute(
            "INSERT INTO payouts (user_id, amount, status, error_message) VALUES (%s, %s, 'failed', %s);",
            (user_id, amount, "Aucune adresse wallet enregistrée pour cet utilisateur")
        )
        return

    success, result = send_usdt_bep20(wallet_address, amount)

    if success:
        cur.execute(
            "INSERT INTO payouts (user_id, amount, status, tx_hash) VALUES (%s, %s, 'sent', %s);",
            (user_id, amount, result)
        )
        cur.execute("UPDATE users SET balance = 0 WHERE id = %s;", (user_id,))
    else:
        cur.execute(
            "INSERT INTO payouts (user_id, amount, status, error_message) VALUES (%s, %s, 'failed', %s);",
            (user_id, amount, result)
        )


# ---------- SOLDE & HISTORIQUE ----------

@app.route("/balance", methods=["GET"])
@jwt_required()
def get_balance():
    user_id = get_jwt_identity()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE id = %s;", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return jsonify({"balance": float(user["balance"])})


@app.route("/payouts", methods=["GET"])
@jwt_required()
def list_payouts():
    user_id = get_jwt_identity()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM payouts WHERE user_id = %s ORDER BY created_at DESC;", (user_id,))
    payouts = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(payouts)


@app.route("/")
def index():
    return jsonify({"status": "ok", "message": "cashmoney watch-to-earn API en ligne"})


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
