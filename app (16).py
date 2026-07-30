import os
import bcrypt
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify
from flask_jwt_extended import (
    JWTManager, create_access_token,
    jwt_required, get_jwt_identity, verify_jwt_in_request
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
REFERRAL_BONUS = float(os.environ.get("REFERRAL_BONUS", 0.10))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")


def send_telegram_message(chat_id, text):
    import urllib.request
    import urllib.parse

    if not TELEGRAM_BOT_TOKEN:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }).encode()

    try:
        urllib.request.urlopen(url, data=data, timeout=10)
        return True
    except Exception:
        return False


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
            investment_amount NUMERIC DEFAULT 0,
            maintenance_fee NUMERIC DEFAULT 0,
            remaining_budget NUMERIC DEFAULT 0,
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

    # Migrations : ajoute les colonnes manquantes si la table existait déjà avant cette version
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_address TEXT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS balance NUMERIC DEFAULT 0;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();")
    cur.execute("ALTER TABLE payouts ADD COLUMN IF NOT EXISTS tx_hash TEXT;")
    cur.execute("ALTER TABLE payouts ADD COLUMN IF NOT EXISTS error_message TEXT;")
    cur.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS investment_amount NUMERIC DEFAULT 0;")
    cur.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS maintenance_fee NUMERIC DEFAULT 0;")
    cur.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS remaining_budget NUMERIC DEFAULT 0;")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS premium_payments (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            tx_hash TEXT UNIQUE NOT NULL,
            amount NUMERIC NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS blockchain_scan_state (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)

    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_premium BOOLEAN DEFAULT FALSE;")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            expires_at TIMESTAMP NOT NULL,
            used BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS facebook_link TEXT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code TEXT UNIQUE;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by INTEGER REFERENCES users(id);")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_bonus_paid BOOLEAN DEFAULT FALSE;")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS telegram_link_codes (
            code TEXT PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            expires_at TIMESTAMP NOT NULL,
            used BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_id BIGINT UNIQUE;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_username TEXT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_id BIGINT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_link_code TEXT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_link_code_expires TIMESTAMP;")

    # Génère un code de parrainage pour les comptes créés avant cette fonctionnalité
    cur.execute("UPDATE users SET referral_code = 'CM' || id WHERE referral_code IS NULL;")
    cur.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS premium_only BOOLEAN DEFAULT FALSE;")
    cur.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS video_url TEXT;")
    cur.execute("""
        UPDATE videos SET video_url = 'https://www.youtube.com/watch?v=' || youtube_id
        WHERE video_url IS NULL AND youtube_id IS NOT NULL;
    """)

    conn.commit()
    cur.close()
    conn.close()


from functools import wraps
from flask_jwt_extended import get_jwt

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SITE_URL = os.environ.get("SITE_URL", "https://cashmoney-xjbs.onrender.com")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")


def send_telegram_message(chat_id, text):
    import requests as _requests

    if not TELEGRAM_BOT_TOKEN:
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        _requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
        return True
    except Exception:
        return False


def send_email(to_email: str, subject: str, body: str):
    import smtplib
    from email.mime.text import MIMEText

    if not SMTP_USER or not SMTP_PASSWORD:
        print(f"[EMAIL] Échec : SMTP_USER ou SMTP_PASSWORD non configuré (destinataire prévu : {to_email})", flush=True)
        return False, "SMTP non configuré côté serveur"

    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = to_email

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, [to_email], msg.as_string())

        print(f"[EMAIL] Envoyé avec succès à {to_email}", flush=True)
        return True, "Email envoyé"
    except Exception as e:
        print(f"[EMAIL] ERREUR lors de l'envoi à {to_email} : {e}", flush=True)
        return False, str(e)


def admin_required(f):
    @wraps(f)
    @jwt_required()
    def wrapper(*args, **kwargs):
        claims = get_jwt()
        if not claims.get("is_admin"):
            return jsonify({"error": "Accès réservé aux administrateurs"}), 403
        return f(*args, **kwargs)
    return wrapper


ADMIN_GUARD_JS = """
<script>
const adminToken = localStorage.getItem('cm_admin_token');
if(!adminToken){ window.location.href = '/admin/login'; }
</script>
"""

TRANSFER_EVENT_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex()
PREMIUM_PRICE_USDT = float(os.environ.get("PREMIUM_PRICE_USDT", 0.90))

# Adresse qui reçoit les paiements premium : par défaut celle dérivée du wallet de paiement
PREMIUM_RECEIVE_ADDRESS = os.environ.get("PREMIUM_RECEIVE_ADDRESS")
if not PREMIUM_RECEIVE_ADDRESS and PAYOUT_WALLET_PRIVATE_KEY:
    PREMIUM_RECEIVE_ADDRESS = w3.eth.account.from_key(PAYOUT_WALLET_PRIVATE_KEY).address


def address_to_topic(address: str) -> str:
    """Convertit une adresse en format topic (32 bytes) pour filtrer les logs."""
    return "0x" + "0" * 24 + address.lower().replace("0x", "")


def poll_premium_payments():
    """
    Tourne en arrière-plan : scanne régulièrement la blockchain BSC pour détecter
    automatiquement les paiements Premium reçus, sans aucune action de l'utilisateur.
    """
    import time as _time

    min_amount_wei = int(PREMIUM_PRICE_USDT * (10 ** 18))
    to_topic = address_to_topic(PREMIUM_RECEIVE_ADDRESS)

    while True:
        try:
            conn = get_connection()
            cur = conn.cursor()

            cur.execute("SELECT value FROM blockchain_scan_state WHERE key = 'last_block';")
            row = cur.fetchone()
            latest_block = w3.eth.block_number

            if row:
                from_block = int(row["value"]) + 1
            else:
                from_block = max(0, latest_block - 5000)

            if from_block > latest_block:
                cur.close()
                conn.close()
                _time.sleep(30)
                continue

            to_block = min(latest_block, from_block + 2000)

            logs = w3.eth.get_logs({
                "fromBlock": from_block,
                "toBlock": to_block,
                "address": Web3.to_checksum_address(USDT_BEP20_CONTRACT),
                "topics": [TRANSFER_EVENT_TOPIC, None, to_topic]
            })

            for log in logs:
                value = int.from_bytes(log["data"], "big") if isinstance(log["data"], (bytes, bytearray)) else int(log["data"], 16)
                if value < min_amount_wei:
                    continue

                sender = "0x" + log["topics"][1].hex()[-40:]
                tx_hash = log["transactionHash"].hex()

                cur.execute(
                    "SELECT id, is_premium FROM users WHERE LOWER(wallet_address) = LOWER(%s);",
                    (sender,)
                )
                user = cur.fetchone()

                if user and not user["is_premium"]:
                    try:
                        cur.execute(
                            "INSERT INTO premium_payments (user_id, tx_hash, amount) VALUES (%s, %s, %s);",
                            (user["id"], tx_hash, value / (10 ** 18))
                        )
                        cur.execute("UPDATE users SET is_premium = TRUE WHERE id = %s;", (user["id"],))
                        conn.commit()
                    except psycopg2.errors.UniqueViolation:
                        conn.rollback()

            cur.execute(
                "INSERT INTO blockchain_scan_state (key, value) VALUES ('last_block', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = %s;",
                (str(to_block), str(to_block))
            )
            conn.commit()
            cur.close()
            conn.close()

        except Exception:
            pass

        _time.sleep(15)


@app.route("/profile/wallet", methods=["POST"])
@jwt_required()
def update_wallet():
    user_id = int(get_jwt_identity())
    data = request.get_json()
    wallet_address = data.get("wallet_address")

    if not wallet_address or not Web3.is_address(wallet_address):
        return jsonify({"error": "Adresse wallet invalide"}), 400

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET wallet_address = %s WHERE id = %s;", (wallet_address, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})


@app.route("/premium", methods=["GET"])
def premium_page():
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Premium - CashMoney</title><style>{SHARED_CSS}</style></head><body>
{NAV_HTML}
<div class="box">
<div class="card" style="max-width:500px;margin:0 auto">
<h2>⭐ Passer Premium</h2>
<p style="color:#aaa;margin-bottom:1rem">Débloque l'accès aux vidéos exclusives, mieux rémunérées.</p>
<div id="msg"></div>

<label>1. Ton adresse wallet USDT (BEP20) — celle depuis laquelle tu vas payer</label>
<input id="myWallet" placeholder="0x...">
<p style="color:#888;font-size:.85rem;margin-bottom:.8rem">⚠️ Sauvegarde bien ta phrase de 12 mots. Cette adresse sera aussi celle utilisée pour tes retraits — une perte d'accès rend les fonds définitivement irrécupérables.</p>
<button class="btn outline" style="width:100%;margin-bottom:1.5rem" onclick="saveWallet()">Enregistrer mon adresse</button>

<div class="alert info">
  2. Envoie exactement <strong>{PREMIUM_PRICE_USDT} USDT (BEP20)</strong> depuis cette adresse vers :<br>
  <span class="copy" onclick="copyText('{PREMIUM_RECEIVE_ADDRESS}')">{PREMIUM_RECEIVE_ADDRESS}</span>
</div>

<p style="color:#888;text-align:center">3. C'est tout ! La détection est automatique — pas besoin de cliquer sur quoi que ce soit. Ton compte passera Premium tout seul dès que le paiement sera détecté (généralement en 1-2 minutes).</p>
<p id="statusText" style="text-align:center;margin-top:1rem;color:#f0b429">⏳ En attente d'un paiement...</p>
</div>
</div>
<script>
function copyText(t){{navigator.clipboard.writeText(t);alert('Adresse copiée !');}}
const token = localStorage.getItem('cm_token');

async function saveWallet(){{
  const msg = document.getElementById('msg');
  try {{
    const res = await fetch('/profile/wallet', {{
      method:'POST',
      headers:{{'Content-Type':'application/json','Authorization':'Bearer '+token}},
      body: JSON.stringify({{wallet_address: document.getElementById('myWallet').value}})
    }});
    const data = await res.json();
    if(data.success){{
      msg.innerHTML = '<div class="alert success">Adresse enregistrée. Envoie maintenant ton paiement depuis celle-ci.</div>';
    }} else {{
      msg.innerHTML = '<div class="alert error">' + (data.error || 'Erreur') + '</div>';
    }}
  }} catch(e) {{
    msg.innerHTML = '<div class="alert error">Erreur serveur.</div>';
  }}
}}

async function checkStatus(){{
  try {{
    const res = await fetch('/premium/status', {{headers:{{'Authorization':'Bearer '+token}}}});
    const data = await res.json();
    if(data.is_premium){{
      document.getElementById('statusText').innerHTML = '🎉 Tu es maintenant Premium !';
      document.getElementById('statusText').style.color = '#2ecc71';
    }}
  }} catch(e) {{}}
}}
setInterval(checkStatus, 10000);
checkStatus();
</script></body></html>"""


@app.route("/premium/status", methods=["GET"])
@jwt_required()
def premium_status():
    user_id = int(get_jwt_identity())
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_premium FROM users WHERE id = %s;", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return jsonify({"is_premium": bool(user["is_premium"]) if user else False})


@app.route("/profile/facebook", methods=["POST"])
@jwt_required()
def update_facebook_link():
    user_id = int(get_jwt_identity())
    data = request.get_json()
    link = data.get("facebook_link", "").strip()

    if not link or ("facebook.com" not in link and "fb.com" not in link):
        return jsonify({"error": "Merci de fournir un lien Facebook valide (facebook.com/...)"}), 400

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET facebook_link = %s WHERE id = %s;", (link, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})


@app.route("/reseau", methods=["GET"])
def reseau_page():
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Réseau - CashMoney</title><style>{SHARED_CSS}</style></head><body>
{NAV_HTML}
<div class="box">
<div class="card">
  <h2>🔗 Partage ta page Facebook</h2>
  <div id="msg"></div>
  <label>Lien de ta page Facebook</label>
  <input id="fbLink" placeholder="https://facebook.com/tapage">
  <button class="btn gold" style="width:100%" onclick="saveLink()">Ajouter mon lien</button>
</div>
<div class="card">
  <h2>Pages partagées par la communauté</h2>
  <div id="linksList">Chargement...</div>
</div>
</div>
<script>
const token = localStorage.getItem('cm_token');

async function saveLink(){{
  const msg = document.getElementById('msg');
  try {{
    const res = await fetch('/profile/facebook', {{
      method:'POST',
      headers:{{'Content-Type':'application/json','Authorization':'Bearer '+token}},
      body: JSON.stringify({{facebook_link: document.getElementById('fbLink').value}})
    }});
    const data = await res.json();
    if(data.success){{
      msg.innerHTML = '<div class="alert success">Lien ajouté !</div>';
      document.getElementById('fbLink').value = '';
      loadLinks();
    }} else {{
      msg.innerHTML = '<div class="alert error">' + (data.error || 'Erreur') + '</div>';
    }}
  }} catch(e) {{
    msg.innerHTML = '<div class="alert error">Erreur serveur.</div>';
  }}
}}

async function loadLinks(){{
  const res = await fetch('/reseau/list');
  const links = await res.json();
  const list = document.getElementById('linksList');
  if(!links.length){{
    list.innerHTML = '<p style="color:#888">Aucun lien partagé pour le moment.</p>';
    return;
  }}
  list.innerHTML = links.map(l => `
    <div style="padding:.8rem 0;border-bottom:1px solid #222250">
      <a href="${{l}}" target="_blank" style="color:#f0b429">${{l}}</a>
    </div>
  `).join('');
}}

loadLinks();
</script></body></html>"""


@app.route("/reseau/list", methods=["GET"])
def reseau_list():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT facebook_link FROM users WHERE facebook_link IS NOT NULL ORDER BY created_at DESC;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([r["facebook_link"] for r in rows])


SHARED_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:sans-serif;background:#0a0a1a;color:#e0e0e0;min-height:100vh}
.nav{background:#111130;padding:1rem 2rem;display:flex;justify-content:space-between;border-bottom:2px solid #f0b429}
.logo{font-size:1.5rem;font-weight:bold;color:#f0b429}
.nav a{color:#e0e0e0;text-decoration:none;margin-left:1rem}
.nav a:hover{color:#f0b429}
.box{max-width:1100px;margin:0 auto;padding:2rem 1rem}
.card{background:#111130;border-radius:12px;padding:1.5rem;margin-bottom:1.5rem;border:1px solid #222250}
.btn{display:inline-block;padding:.7rem 1.5rem;border-radius:8px;border:none;cursor:pointer;font-size:1rem;text-decoration:none}
.gold{background:#f0b429;color:#000;font-weight:bold}
.outline{background:transparent;border:2px solid #f0b429;color:#f0b429}
.ok{background:#27ae60;color:#fff}
.ko{background:#e74c3c;color:#fff}
.alert{padding:1rem;border-radius:8px;margin-bottom:1rem}
.success{background:#1a3a2a;border:1px solid #27ae60;color:#2ecc71}
.error{background:#3a1a1a;border:1px solid #e74c3c;color:#e74c3c}
.info{background:#1a2a3a;border:1px solid #3498db;color:#3498db}
input{width:100%;padding:.8rem;border-radius:8px;border:1px solid #333360;background:#0a0a1a;color:#e0e0e0;font-size:1rem;margin-bottom:1rem}
label{display:block;margin-bottom:.3rem;color:#aaa;font-size:.9rem}
h1,h2,h3{color:#f0b429;margin-bottom:1rem}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
.g3{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem}
.stat{background:#111130;border-radius:12px;padding:1.2rem;text-align:center;border:1px solid #222250}
.val{font-size:2rem;font-weight:bold;color:#f0b429}
.lbl{color:#888;font-size:.9rem;margin-top:.3rem}
table{width:100%;border-collapse:collapse}
th,td{padding:.8rem;text-align:left;border-bottom:1px solid #222250}
th{color:#f0b429}
@media(max-width:600px){.g2,.g3{grid-template-columns:1fr}}
"""

NAV_HTML = """
<nav class="nav">
<div class="logo">💰 CashMoney</div>
<div id="navLinks">
<a href="/dashboard">Dashboard</a>
<a href="/reseau">🔗 Réseau</a>
<a href="/premium">⭐ Premium</a>
<a href="/retrait">💸 Retrait</a>
<a href="#" onclick="logout()">Déconnexion</a>
</div>
</nav>
<script>
if(!localStorage.getItem('cm_token')){
  window.location.href = '/login';
}
function logout(){
  localStorage.removeItem('cm_token');
  window.location.href = '/login';
}
</script>
"""


# ---------- AUTHENTIFICATION ----------

@app.route("/login/forgot", methods=["GET", "POST"])
def login_forgot():
    if request.method == "GET":
        return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mot de passe oublié - CashMoney</title><style>{SHARED_CSS}</style></head><body>
<nav class="nav"><div class="logo">💰 CashMoney</div><div>
<a href="/login">Connexion</a><a href="/register">S'inscrire</a></div></nav>
<div class="box"><div class="card" style="max-width:420px;margin:2rem auto">
<h2>Mot de passe oublié</h2>
<div id="msg"></div>
<label>Ton email</label><input id="email" type="email" placeholder="ton@email.com">
<button class="btn gold" style="width:100%" onclick="sendReset()">Recevoir le lien de réinitialisation</button>
</div></div>
<script>
async function sendReset(){{
  const msg = document.getElementById('msg');
  try {{
    const res = await fetch('/login/forgot', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{email: document.getElementById('email').value}})
    }});
    const data = await res.json();
    msg.innerHTML = '<div class="alert success">Si ce compte existe, un email avec un lien de réinitialisation vient d\\'être envoyé. Vérifie ta boîte mail (et les spams).</div>';
  }} catch(e) {{
    msg.innerHTML = '<div class="alert error">Erreur serveur.</div>';
  }}
}}
</script></body></html>"""

    data = request.get_json()
    email = data.get("email")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE email = %s;", (email,))
    user = cur.fetchone()

    if not user:
        print(f"[EMAIL] Demande de reset pour un email inconnu : {email}", flush=True)

    if user:
        import secrets as _secrets
        import datetime as _datetime

        token = _secrets.token_urlsafe(32)
        expires_at = _datetime.datetime.utcnow() + _datetime.timedelta(hours=1)

        cur.execute(
            "INSERT INTO password_reset_tokens (token, user_id, expires_at) VALUES (%s, %s, %s);",
            (token, user["id"], expires_at)
        )
        conn.commit()

        reset_link = f"{SITE_URL}/login/reset?token={token}"
        send_email(
            email,
            "Réinitialisation de ton mot de passe CashMoney",
            f"Clique sur ce lien pour choisir un nouveau mot de passe (valable 1 heure) :\n\n{reset_link}\n\n"
            f"Si tu n'es pas à l'origine de cette demande, ignore simplement cet email."
        )

    cur.close()
    conn.close()

    # Toujours la même réponse, qu'un compte existe ou non (sécurité : ne pas révéler les emails inscrits)
    return jsonify({"success": True})


@app.route("/login/reset", methods=["GET", "POST"])
def login_reset():
    if request.method == "GET":
        token = request.args.get("token", "")
        return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Nouveau mot de passe - CashMoney</title><style>{SHARED_CSS}</style></head><body>
<nav class="nav"><div class="logo">💰 CashMoney</div></nav>
<div class="box"><div class="card" style="max-width:420px;margin:2rem auto">
<h2>Choisir un nouveau mot de passe</h2>
<div id="msg"></div>
<input type="hidden" id="token" value="{token}">
<label>Nouveau mot de passe</label><input id="password" type="password" placeholder="••••••••">
<button class="btn gold" style="width:100%" onclick="doReset()">Valider</button>
</div></div>
<script>
async function doReset(){{
  const msg = document.getElementById('msg');
  try {{
    const res = await fetch('/login/reset', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{
        token: document.getElementById('token').value,
        password: document.getElementById('password').value
      }})
    }});
    const data = await res.json();
    if(data.success){{
      msg.innerHTML = '<div class="alert success">Mot de passe changé ! <a href="/login" style="color:#2ecc71">Se connecter</a></div>';
    }} else {{
      msg.innerHTML = '<div class="alert error">' + (data.error || 'Erreur') + '</div>';
    }}
  }} catch(e) {{
    msg.innerHTML = '<div class="alert error">Erreur serveur.</div>';
  }}
}}
</script></body></html>"""

    data = request.get_json()
    token = data.get("token")
    new_password = data.get("password")

    if not token or not new_password:
        return jsonify({"error": "token et password requis"}), 400

    import datetime as _datetime

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM password_reset_tokens WHERE token = %s AND used = FALSE AND expires_at > %s;",
        (token, _datetime.datetime.utcnow())
    )
    reset_row = cur.fetchone()

    if not reset_row:
        cur.close()
        conn.close()
        return jsonify({"error": "Lien invalide ou expiré. Refais une demande."}), 400

    new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    cur.execute("UPDATE users SET password_hash = %s WHERE id = %s;", (new_hash, reset_row["user_id"]))
    cur.execute("UPDATE password_reset_tokens SET used = TRUE WHERE token = %s;", (token,))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"success": True})
def register():
    if request.method == "GET":
        return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Inscription - CashMoney</title><style>{SHARED_CSS}</style></head><body>
<nav class="nav"><div class="logo">💰 CashMoney</div><div>
<a href="/login">Connexion</a><a href="/register">S'inscrire</a></div></nav>
<div class="box"><div class="card" style="max-width:420px;margin:2rem auto">
<h2>Créer un compte</h2>
<div id="msg"></div>
<label>Email</label><input id="email" type="email" placeholder="ton@email.com">
<label>Mot de passe</label><input id="password" type="password" placeholder="••••••••">
<label>Adresse wallet USDT (BEP20, optionnel)</label><input id="wallet" placeholder="0x...">
<p style="color:#888;font-size:.85rem;margin-bottom:1rem">⚠️ C'est vers cette adresse que tes gains seront envoyés. Garde ta phrase de 12 mots (seed phrase) en sécurité — une perte d'accès à ce wallet rend les fonds irrécupérables, même pour nous.</p>
<button class="btn gold" style="width:100%" onclick="doRegister()">S'inscrire</button>
</div></div>
<script>
async function doRegister(){{
  const msg = document.getElementById('msg');
  try {{
    const urlParams = new URLSearchParams(window.location.search);
    const body = {{
      email: document.getElementById('email').value,
      password: document.getElementById('password').value,
      wallet_address: document.getElementById('wallet').value || null,
      ref: urlParams.get('ref') || null
    }};
    const res = await fetch('/register', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}});
    const data = await res.json();
    if(data.access_token){{
      localStorage.setItem('cm_token', data.access_token);
      window.location.href = '/dashboard';
    }} else {{
      msg.innerHTML = '<div class="alert error">' + (data.error || 'Erreur inconnue') + '</div>';
    }}
  }} catch(e) {{
    msg.innerHTML = '<div class="alert error">Erreur serveur, réessaie dans quelques secondes.</div>';
  }}
}}
</script></body></html>"""

    data = request.get_json()
    email = data.get("email")
    password = data.get("password")
    wallet_address = data.get("wallet_address")

    if not email or not password:
        return jsonify({"error": "email et password requis"}), 400

    if wallet_address and not Web3.is_address(wallet_address):
        return jsonify({"error": "wallet_address invalide (doit être une adresse BEP20 valide)"}), 400

    referral_code_used = data.get("ref")
    referred_by_id = None
    if referral_code_used:
        conn_ref = get_connection()
        cur_ref = conn_ref.cursor()
        cur_ref.execute("SELECT id FROM users WHERE referral_code = %s;", (referral_code_used,))
        referrer = cur_ref.fetchone()
        cur_ref.close()
        conn_ref.close()
        if referrer:
            referred_by_id = referrer["id"]

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (email, password_hash, wallet_address, referred_by) VALUES (%s, %s, %s, %s) RETURNING id, email;",
            (email, password_hash, wallet_address, referred_by_id)
        )
        user = cur.fetchone()
        # Génère un code de parrainage unique basé sur l'id
        my_referral_code = f"CM{user['id']}"
        cur.execute("UPDATE users SET referral_code = %s WHERE id = %s;", (my_referral_code, user["id"]))
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"error": "Cet email est déjà utilisé"}), 409
    finally:
        cur.close()
        conn.close()

    token = create_access_token(identity=str(user["id"]))
    return jsonify({"user": user, "access_token": token}), 201


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Connexion - CashMoney</title><style>{SHARED_CSS}</style></head><body>
<nav class="nav"><div class="logo">💰 CashMoney</div><div>
<a href="/login">Connexion</a><a href="/register">S'inscrire</a></div></nav>
<div class="box"><div class="card" style="max-width:420px;margin:2rem auto">
<h2>Connexion</h2>
<div id="msg"></div>
<label>Email</label><input id="email" type="email" placeholder="ton@email.com">
<label>Mot de passe</label><input id="password" type="password" placeholder="••••••••">
<button class="btn gold" style="width:100%" onclick="doLogin()">Se connecter</button>
<p style="text-align:center;margin-top:1rem"><a href="/login/forgot" style="color:#f0b429">Mot de passe oublié ?</a></p>
</div></div>
<script>
async function doLogin(){{
  const msg = document.getElementById('msg');
  try {{
    const body = {{
      email: document.getElementById('email').value,
      password: document.getElementById('password').value
    }};
    const res = await fetch('/login', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}});
    const data = await res.json();
    if(data.access_token){{
      localStorage.setItem('cm_token', data.access_token);
      window.location.href = '/dashboard';
    }} else {{
      msg.innerHTML = '<div class="alert error">' + (data.error || 'Erreur inconnue') + '</div>';
    }}
  }} catch(e) {{
    msg.innerHTML = '<div class="alert error">Erreur serveur : ce compte a peut-être un problème technique. Essaie de créer un nouveau compte.</div>';
  }}
}}
</script></body></html>"""

    data = request.get_json()
    email = data.get("email")
    password = data.get("password")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = %s;", (email,))
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user:
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401

    try:
        password_valid = bcrypt.checkpw(password.encode(), user["password_hash"].encode())
    except ValueError:
        return jsonify({"error": "Ce compte a un problème technique (ancien format de mot de passe). Crée un nouveau compte."}), 500

    if not password_valid:
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401

    token = create_access_token(identity=str(user["id"]))
    return jsonify({"access_token": token, "balance": float(user["balance"])})


# ---------- VIDÉOS ----------

@app.route("/videos", methods=["GET"])
def list_videos():
    is_premium = False
    try:
        verify_jwt_in_request(optional=True)
        user_id = get_jwt_identity()
        if user_id:
            conn_check = get_connection()
            cur_check = conn_check.cursor()
            cur_check.execute("SELECT is_premium FROM users WHERE id = %s;", (int(user_id),))
            row = cur_check.fetchone()
            cur_check.close()
            conn_check.close()
            is_premium = bool(row["is_premium"]) if row else False
    except Exception:
        is_premium = False

    conn = get_connection()
    cur = conn.cursor()
    if is_premium:
        cur.execute("SELECT * FROM videos WHERE active = TRUE ORDER BY created_at DESC;")
    else:
        cur.execute("SELECT * FROM videos WHERE active = TRUE AND premium_only = FALSE ORDER BY created_at DESC;")
    videos = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(videos)


@app.route("/videos", methods=["POST"])
@admin_required
def add_video():
    data = request.get_json()

    investment_amount = float(data.get("investment_amount", 0) or 0)
    maintenance_fee = float(data.get("maintenance_fee", 0) or 0)
    reward_amount = float(data.get("reward_amount", 0) or 0)

    if investment_amount > 0:
        remaining_budget = investment_amount - maintenance_fee
        if remaining_budget < 0:
            return jsonify({"error": "Les frais d'entretien ne peuvent pas dépasser le montant investi"}), 400
    else:
        remaining_budget = 0

    if reward_amount <= 0:
        return jsonify({"error": "reward_amount (récompense par vue) doit être supérieur à 0"}), 400

    video_url = data.get("video_url")
    if not video_url:
        return jsonify({"error": "video_url requis (lien YouTube, TikTok, Facebook ou Instagram)"}), 400

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO videos (video_url, title, sponsor_name, reward_amount, min_watch_seconds, investment_amount, maintenance_fee, remaining_budget, premium_only)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *;""",
        (video_url, data.get("title"), data.get("sponsor_name"),
         reward_amount, data.get("min_watch_seconds", 30), investment_amount, maintenance_fee, remaining_budget,
         bool(data.get("premium_only", False)))
    )
    video = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return jsonify(video), 201


@app.route("/admin/maintenance-total", methods=["GET"])
@admin_required
def maintenance_total():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(maintenance_fee), 0) AS total FROM videos;")
    result = cur.fetchone()
    cur.close()
    conn.close()
    return jsonify({"total_maintenance_collected": float(result["total"])})


# ---------- VISIONNAGE & GAINS ----------

@app.route("/watch", methods=["POST"])
@jwt_required()
def watch_video():
    user_id = int(get_jwt_identity())
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

    # Vérifie qu'il reste assez de budget sponsor pour cette vue
    if float(video["remaining_budget"]) < float(video["reward_amount"]):
        cur.execute("UPDATE videos SET active = FALSE WHERE id = %s;", (video_id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"error": "Budget sponsor épuisé pour cette vidéo"}), 410

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

    # Décrémente le budget restant de la vidéo, désactive si épuisé
    cur.execute(
        "UPDATE videos SET remaining_budget = remaining_budget - %s WHERE id = %s RETURNING remaining_budget;",
        (video["reward_amount"], video_id)
    )
    new_video_budget = cur.fetchone()["remaining_budget"]
    if float(new_video_budget) < float(video["reward_amount"]):
        cur.execute("UPDATE videos SET active = FALSE WHERE id = %s;", (video_id,))

    cur.execute(
        "UPDATE users SET balance = balance + %s WHERE id = %s RETURNING balance;",
        (video["reward_amount"], user_id)
    )
    new_balance = cur.fetchone()["balance"]
    conn.commit()

    # Bonus de parrainage : versé une seule fois au parrain, quand SON filleul
    # regarde sa toute première vidéo avec succès (évite les faux comptes)
    cur.execute("SELECT COUNT(*) AS c FROM watch_logs WHERE user_id = %s;", (user_id,))
    watch_count = cur.fetchone()["c"]

    if watch_count == 1:
        cur.execute("SELECT referred_by, referral_bonus_paid FROM users WHERE id = %s;", (user_id,))
        referral_info = cur.fetchone()
        if referral_info and referral_info["referred_by"] and not referral_info["referral_bonus_paid"]:
            cur.execute(
                "UPDATE users SET balance = balance + %s WHERE id = %s;",
                (REFERRAL_BONUS, referral_info["referred_by"])
            )
            cur.execute("UPDATE users SET referral_bonus_paid = TRUE WHERE id = %s;", (user_id,))
            conn.commit()

    # Si le seuil est atteint, crée une demande de retrait EN ATTENTE DE CONFIRMATION
    # (pas d'envoi automatique immédiat, pour laisser à l'utilisateur une chance de
    # vérifier/corriger son adresse wallet avant que l'argent ne parte réellement)
    payout_pending = False
    if float(new_balance) >= WITHDRAWAL_THRESHOLD:
        cur.execute(
            "SELECT id FROM payouts WHERE user_id = %s AND status = 'awaiting_confirmation';",
            (user_id,)
        )
        already_pending = cur.fetchone()

        if not already_pending:
            cur.execute(
                "INSERT INTO payouts (user_id, amount, status) VALUES (%s, %s, 'awaiting_confirmation');",
                (user_id, float(new_balance))
            )
            conn.commit()
            payout_pending = True

            cur.execute("SELECT telegram_id FROM users WHERE id = %s;", (user_id,))
            tg = cur.fetchone()
            if tg and tg["telegram_id"]:
                send_telegram_message(
                    tg["telegram_id"],
                    f"💰 Ton solde a atteint le seuil de retrait ({float(new_balance)} USDT) !\n\n"
                    f"Va sur {SITE_URL}/retrait pour vérifier ton adresse wallet et confirmer l'envoi."
                )

    cur.close()
    conn.close()

    return jsonify({
        "reward_given": float(video["reward_amount"]),
        "new_balance": float(new_balance),
        "payout_pending": payout_pending
    })


def trigger_payout(cur, payout_id, user_id, amount):
    """
    Envoie réellement l'USDT (BEP20) vers le wallet de l'utilisateur, après confirmation,
    et met à jour la ligne de payout existante (au lieu d'en créer une nouvelle).
    """
    cur.execute("SELECT wallet_address FROM users WHERE id = %s;", (user_id,))
    user = cur.fetchone()
    wallet_address = user["wallet_address"] if user else None

    if not wallet_address:
        cur.execute(
            "UPDATE payouts SET status = 'failed', error_message = %s WHERE id = %s;",
            ("Aucune adresse wallet enregistrée pour cet utilisateur", payout_id)
        )
        return False

    success, result = send_usdt_bep20(wallet_address, amount)

    if success:
        cur.execute(
            "UPDATE payouts SET status = 'sent', tx_hash = %s WHERE id = %s;",
            (result, payout_id)
        )
        cur.execute("UPDATE users SET balance = 0 WHERE id = %s;", (user_id,))
        return True
    else:
        cur.execute(
            "UPDATE payouts SET status = 'failed', error_message = %s WHERE id = %s;",
            (result, payout_id)
        )
        return False


@app.route("/payout/confirm", methods=["POST"])
@jwt_required()
def payout_confirm():
    user_id = int(get_jwt_identity())

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM payouts WHERE user_id = %s AND status = 'awaiting_confirmation' ORDER BY created_at DESC LIMIT 1;",
        (user_id,)
    )
    payout = cur.fetchone()

    if not payout:
        cur.close()
        conn.close()
        return jsonify({"error": "Aucun retrait en attente de confirmation"}), 404

    success = trigger_payout(cur, payout["id"], user_id, float(payout["amount"]))
    conn.commit()
    cur.close()
    conn.close()

    if success:
        return jsonify({"success": True, "message": "Paiement envoyé avec succès"})
    else:
        return jsonify({"error": "L'envoi a échoué. Vérifie ton adresse wallet et réessaie."}), 400
        # Le solde N'EST PAS remis à zéro si l'envoi a échoué (l'utilisateur garde son droit au paiement)


# ---------- SOLDE & HISTORIQUE ----------

@app.route("/balance", methods=["GET"])
@jwt_required()
def get_balance():
    user_id = int(get_jwt_identity())
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
    user_id = int(get_jwt_identity())
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM payouts WHERE user_id = %s ORDER BY created_at DESC;", (user_id,))
    payouts = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(payouts)


@app.route("/referral/info", methods=["GET"])
@jwt_required()
def referral_info():
    user_id = int(get_jwt_identity())
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT referral_code FROM users WHERE id = %s;", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user or not user["referral_code"]:
        return jsonify({"error": "Code de parrainage introuvable"}), 404

    referral_link = f"{SITE_URL}/register?ref={user['referral_code']}"
    return jsonify({"referral_code": user["referral_code"], "referral_link": referral_link})


@app.route("/dashboard")
def dashboard_page():
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard - CashMoney</title><style>{SHARED_CSS}</style></head><body>
{NAV_HTML}
<div class="box">
<div id="msg"></div>
<div class="g2">
  <div class="stat"><div class="val" id="balanceVal">...</div><div class="lbl">Solde (USDT)</div></div>
  <div class="stat"><div class="val" id="videoCount">...</div><div class="lbl">Vidéos disponibles</div></div>
</div>
<div class="card">
  <h2>🎁 Ton lien de parrainage</h2>
  <p style="color:#aaa;margin-bottom:.8rem">Gagne un bonus quand un ami s'inscrit avec ton lien et regarde sa première vidéo.</p>
  <div class="copy" id="referralLinkBox" onclick="copyReferralLink()">Chargement...</div>
</div>
<div class="card">
  <h2>🤖 Lier Telegram</h2>
  <p style="color:#aaa;margin-bottom:.8rem">Reçois ton solde et ton lien de parrainage directement dans Telegram.</p>
  <div id="telegramMsg"></div>
  <button class="btn outline" style="width:100%" onclick="generateTelegramCode()">Lier mon compte Telegram</button>
</div>
<div class="card">
  <h2>📺 Regardez des vidéos et gagnez des USDT</h2>
  <div id="videoList">Chargement...</div>
</div>
</div>
<script>
const token = localStorage.getItem('cm_token');

async function loadBalance(){{
  const res = await fetch('/balance', {{headers:{{'Authorization':'Bearer '+token}}}});
  const data = await res.json();
  document.getElementById('balanceVal').textContent = (data.balance !== undefined ? data.balance : '—');
}}

function getEmbedHtml(videoUrl){{
  if(!videoUrl) return '<p style="color:#888">Lien vidéo manquant</p>';

  // YouTube
  let m = videoUrl.match(/(?:youtube\\.com\\/watch\\?v=|youtu\\.be\\/)([a-zA-Z0-9_-]+)/);
  if(m){{
    return `<iframe width="100%" height="220" src="https://www.youtube.com/embed/${{m[1]}}" frameborder="0" allowfullscreen></iframe>`;
  }}

  // TikTok
  m = videoUrl.match(/tiktok\\.com\\/.*\\/video\\/(\\d+)/);
  if(m){{
    return `<iframe width="100%" height="500" src="https://www.tiktok.com/embed/v2/${{m[1]}}" frameborder="0" allowfullscreen></iframe>`;
  }}

  // Facebook
  if(videoUrl.includes('facebook.com') || videoUrl.includes('fb.watch')){{
    const encoded = encodeURIComponent(videoUrl);
    return `<iframe width="100%" height="280" src="https://www.facebook.com/plugins/video.php?href=${{encoded}}&show_text=false" frameborder="0" allowfullscreen></iframe>`;
  }}

  // Instagram
  if(videoUrl.includes('instagram.com')){{
    const cleanUrl = videoUrl.split('?')[0].replace(/\\/$/, '');
    return `<iframe width="100%" height="480" src="${{cleanUrl}}/embed" frameborder="0" allowfullscreen></iframe>`;
  }}

  return `<p style="color:#888">Plateforme non reconnue. <a href="${{videoUrl}}" target="_blank" style="color:#f0b429">Voir la vidéo</a></p>`;
}}

async function loadVideos(){{
  const res = await fetch('/videos', {{headers:{{'Authorization':'Bearer '+token}}}});
  const videos = await res.json();
  document.getElementById('videoCount').textContent = videos.length;
  const list = document.getElementById('videoList');
  if(videos.length === 0){{
    list.innerHTML = '<p style="color:#888">Aucune vidéo disponible pour le moment.</p>';
    return;
  }}
  list.innerHTML = videos.map(v => `
    <div class="card" style="margin-top:1rem">
      <h3>${{v.title}}</h3>
      <p style="color:#888">Sponsor : ${{v.sponsor_name || 'N/A'}} — Récompense : ${{v.reward_amount}} USDT</p>
      ${{getEmbedHtml(v.video_url)}}
      <button class="btn gold" style="margin-top:1rem" onclick="watchVideo(${{v.id}}, ${{v.min_watch_seconds}})">J'ai regardé (min ${{v.min_watch_seconds}}s)</button>
    </div>
  `).join('');
}}

async function watchVideo(videoId, minSeconds){{
  const msg = document.getElementById('msg');
  msg.innerHTML = '<div class="alert info">Enregistrement en cours...</div>';
  const res = await fetch('/watch', {{
    method: 'POST',
    headers: {{'Content-Type':'application/json', 'Authorization':'Bearer '+token}},
    body: JSON.stringify({{video_id: videoId, watched_seconds: minSeconds}})
  }});
  const data = await res.json();
  if(data.reward_given !== undefined){{
    msg.innerHTML = '<div class="alert success">+' + data.reward_given + ' USDT crédité ! Nouveau solde : ' + data.new_balance + ' USDT' + (data.payout_pending ? ' — Seuil atteint ! Va sur Retrait pour confirmer l\\'envoi 🎉' : '') + '</div>';
    loadBalance();
  }} else {{
    msg.innerHTML = '<div class="alert error">' + (data.error || 'Erreur') + '</div>';
  }}
}}

async function loadReferralLink(){{
  const res = await fetch('/referral/info', {{headers:{{'Authorization':'Bearer '+token}}}});
  const data = await res.json();
  document.getElementById('referralLinkBox').textContent = data.referral_link || 'Erreur';
}}
function copyReferralLink(){{
  const text = document.getElementById('referralLinkBox').textContent;
  navigator.clipboard.writeText(text);
  alert('Lien copié !');
}}

async function generateTelegramCode(){{
  const msg = document.getElementById('telegramMsg');
  try {{
    const res = await fetch('/telegram/generate-code', {{
      method:'POST', headers:{{'Authorization':'Bearer '+token}}
    }});
    const data = await res.json();
    if(data.code){{
      msg.innerHTML = '<div class="alert success">Va sur Telegram et envoie : <strong>/lier ' + data.code + '</strong><br>Valable ' + data.expires_in_minutes + ' minutes.</div>';
    }} else {{
      msg.innerHTML = '<div class="alert error">' + (data.error || 'Erreur') + '</div>';
    }}
  }} catch(e) {{
    msg.innerHTML = '<div class="alert error">Erreur serveur.</div>';
  }}
}}

loadBalance();
loadVideos();
loadReferralLink();
</script></body></html>"""


@app.route("/retrait")
def retrait_page():
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Retrait - CashMoney</title><style>{SHARED_CSS}</style></head><body>
{NAV_HTML}
<div class="box">
<div class="stat" style="max-width:300px;margin-bottom:1.5rem">
  <div class="val" id="balanceVal">...</div><div class="lbl">Solde actuel (USDT)</div>
</div>
<div id="pendingCard"></div>
<div class="card">
  <h2>💸 Historique des retraits</h2>
  <p style="color:#888;margin-bottom:1rem">Un retrait se déclenche dès que ton solde atteint le seuil configuré, mais nécessite ta confirmation avant l'envoi réel.</p>
  <div class="alert info" style="font-size:.9rem">
    ⚠️ Sauvegarde toujours ta phrase de 12 mots (seed phrase) en lieu sûr. Si tu perds l'accès à ton wallet après un envoi, l'argent est <strong>définitivement perdu</strong> — personne, pas même nous, ne peut le récupérer.
  </div>
  <table id="payoutsTable">
    <thead><tr><th>Date</th><th>Montant</th><th>Statut</th><th>Détail</th></tr></thead>
    <tbody><tr><td colspan="4">Chargement...</td></tr></tbody>
  </table>
</div>
</div>
<script>
const token = localStorage.getItem('cm_token');

async function loadBalance(){{
  const res = await fetch('/balance', {{headers:{{'Authorization':'Bearer '+token}}}});
  const data = await res.json();
  document.getElementById('balanceVal').textContent = (data.balance !== undefined ? data.balance : '—');
}}

async function confirmPayout(){{
  const res = await fetch('/payout/confirm', {{method:'POST', headers:{{'Authorization':'Bearer '+token}}}});
  const data = await res.json();
  if(data.success){{
    alert('Paiement envoyé avec succès !');
  }} else {{
    alert(data.error || 'Erreur');
  }}
  loadBalance();
  loadPayouts();
}}

async function loadPayouts(){{
  const res = await fetch('/payouts', {{headers:{{'Authorization':'Bearer '+token}}}});
  const payouts = await res.json();
  const tbody = document.querySelector('#payoutsTable tbody');
  const pendingCard = document.getElementById('pendingCard');

  const pending = payouts.find(p => p.status === 'awaiting_confirmation');
  if(pending){{
    pendingCard.innerHTML = `
      <div class="card" style="border-color:#f0b429">
        <h2>⏳ Retrait en attente de confirmation</h2>
        <p style="color:#aaa;margin-bottom:1rem">Montant : <strong>${{pending.amount}} USDT</strong></p>
        <p style="color:#888;margin-bottom:1rem;font-size:.9rem">Vérifie que ton adresse wallet enregistrée est bien correcte avant de confirmer.</p>
        <button class="btn gold" style="width:100%" onclick="confirmPayout()">Confirmer et envoyer</button>
      </div>`;
  }} else {{
    pendingCard.innerHTML = '';
  }}

  const shown = payouts.filter(p => p.status !== 'awaiting_confirmation');
  if(!shown.length){{
    tbody.innerHTML = '<tr><td colspan="4" style="color:#888">Aucun retrait pour le moment.</td></tr>';
    return;
  }}
  tbody.innerHTML = shown.map(p => `
    <tr>
      <td>${{new Date(p.created_at).toLocaleString('fr-FR')}}</td>
      <td>${{p.amount}} USDT</td>
      <td>${{p.status === 'sent' ? '✅ Envoyé' : '❌ Échoué'}}</td>
      <td style="font-size:.8rem;color:#888">${{p.tx_hash || p.error_message || '—'}}</td>
    </tr>
  `).join('');
}}

loadBalance();
loadPayouts();
</script></body></html>"""


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Admin - CashMoney</title><style>{SHARED_CSS}</style></head><body>
<nav class="nav"><div class="logo">💰 CashMoney Admin</div></nav>
<div class="box"><div class="card" style="max-width:400px;margin:2rem auto">
<h2>Connexion Admin</h2>
<div id="msg"></div>
<label>Mot de passe admin</label>
<input id="password" type="password" placeholder="••••••••">
<button class="btn gold" style="width:100%" onclick="doAdminLogin()">Se connecter</button>
</div></div>
<script>
async function doAdminLogin(){{
  const msg = document.getElementById('msg');
  try {{
    const res = await fetch('/admin/login', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{password: document.getElementById('password').value}})
    }});
    const data = await res.json();
    if(data.access_token){{
      localStorage.setItem('cm_admin_token', data.access_token);
      window.location.href = '/admin/videos';
    }} else {{
      msg.innerHTML = '<div class="alert error">' + (data.error || 'Erreur') + '</div>';
    }}
  }} catch(e) {{
    msg.innerHTML = '<div class="alert error">Erreur serveur.</div>';
  }}
}}
</script></body></html>"""

    if not ADMIN_PASSWORD:
        return jsonify({"error": "ADMIN_PASSWORD non configuré côté serveur"}), 500

    data = request.get_json()
    if data.get("password") != ADMIN_PASSWORD:
        return jsonify({"error": "Mot de passe incorrect"}), 401

    token = create_access_token(identity="admin", additional_claims={"is_admin": True})
    return jsonify({"access_token": token})


@app.route("/admin/videos")
def admin_videos_page():
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ajouter une vidéo - CashMoney</title><style>{SHARED_CSS}</style></head>{ADMIN_GUARD_JS}<body>
<nav class="nav"><div class="logo">💰 CashMoney Admin</div><div><a href="/dashboard">Dashboard</a></div></nav>
<div class="box"><div class="card" style="max-width:500px;margin:2rem auto">
<h2>Ajouter une vidéo sponsorisée</h2>
<p style="color:#888;margin-bottom:1rem">⚠️ Page temporaire, sans protection. À sécuriser avant lancement public.</p>
<div id="msg"></div>
<label>Lien de la vidéo (YouTube, TikTok, Facebook ou Instagram)</label>
<input id="videoUrl" placeholder="https://youtube.com/watch?v=... ou tiktok.com/... ou fb.watch/... ou instagram.com/reel/...">
<label>Titre</label>
<input id="title" placeholder="Titre de la vidéo">
<label>Nom du sponsor</label>
<input id="sponsor" placeholder="Nom de la marque">
<label>Montant investi par le sponsor (USDT)</label>
<input id="investment" type="number" step="0.01" value="0.90" oninput="updateInfo()">
<label>Frais d'entretien du site (USDT, prélevé une fois)</label>
<input id="maintenanceFee" type="number" step="0.01" value="0.20" oninput="updateInfo()">
<label>Récompense versée à CHAQUE utilisateur qui regarde (USDT)</label>
<input id="reward" type="number" step="0.01" value="0.20" oninput="updateInfo()">
<p id="infoText" style="color:#888;margin-bottom:1rem"></p>
<label>Durée minimum de visionnage (secondes)</label>
<input id="minSeconds" type="number" value="30">
<label style="display:flex;align-items:center;gap:.5rem;cursor:pointer">
  <input type="checkbox" id="premiumOnly" style="width:auto;margin:0">
  Réservée aux utilisateurs Premium
</label>
<button class="btn gold" style="width:100%;margin-top:1rem" onclick="addVideo()">Ajouter la vidéo</button>
</div></div>
<script>
function updateInfo(){{
  const investment = parseFloat(document.getElementById('investment').value) || 0;
  const fee = parseFloat(document.getElementById('maintenanceFee').value) || 0;
  const reward = parseFloat(document.getElementById('reward').value) || 0;
  const budget = investment - fee;
  const views = reward > 0 ? Math.floor(budget / reward) : 0;
  document.getElementById('infoText').textContent =
    'Budget disponible après frais : ' + budget.toFixed(2) + ' USDT — finance environ ' + views + ' vue(s) à ' + reward.toFixed(2) + ' USDT chacune.';
}}
updateInfo();

async function addVideo(){{
  const body = {{
    video_url: document.getElementById('videoUrl').value,
    title: document.getElementById('title').value,
    sponsor_name: document.getElementById('sponsor').value,
    investment_amount: parseFloat(document.getElementById('investment').value),
    maintenance_fee: parseFloat(document.getElementById('maintenanceFee').value),
    reward_amount: parseFloat(document.getElementById('reward').value),
    min_watch_seconds: parseInt(document.getElementById('minSeconds').value) || 30,
    premium_only: document.getElementById('premiumOnly').checked
  }};
  const res = await fetch('/videos', {{method:'POST', headers:{{'Content-Type':'application/json', 'Authorization':'Bearer '+localStorage.getItem('cm_admin_token')}}, body: JSON.stringify(body)}});
  const data = await res.json();
  const msg = document.getElementById('msg');
  if(data.id){{
    msg.innerHTML = '<div class="alert success">Vidéo ajoutée ! Budget restant : ' + data.remaining_budget + ' USDT (récompense par vue : ' + data.reward_amount + ' USDT)</div>';
    document.getElementById('videoUrl').value = '';
    document.getElementById('title').value = '';
    document.getElementById('sponsor').value = '';
  }} else {{
    msg.innerHTML = '<div class="alert error">' + (data.error || 'Erreur') + '</div>';
  }}
}}
</script></body></html>"""


@app.route("/admin/reset-password", methods=["GET", "POST"])
def admin_reset_password():
    if request.method == "GET":
        return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Réinitialiser mot de passe - CashMoney</title><style>{SHARED_CSS}</style></head>{ADMIN_GUARD_JS}<body>
<nav class="nav"><div class="logo">💰 CashMoney Admin</div><div><a href="/dashboard">Dashboard</a></div></nav>
<div class="box"><div class="card" style="max-width:420px;margin:2rem auto">
<h2>Réinitialiser un mot de passe</h2>
<div id="msg"></div>
<label>Email du compte</label><input id="email" type="email" placeholder="ton@email.com">
<label>Nouveau mot de passe</label><input id="password" type="password" placeholder="••••••••">
<button class="btn gold" style="width:100%" onclick="resetPw()">Réinitialiser</button>
</div></div>
<script>
async function resetPw(){{
  const msg = document.getElementById('msg');
  try {{
    const body = {{
      email: document.getElementById('email').value,
      password: document.getElementById('password').value
    }};
    const res = await fetch('/admin/reset-password', {{
      method:'POST',
      headers:{{'Content-Type':'application/json', 'Authorization':'Bearer '+localStorage.getItem('cm_admin_token')}},
      body: JSON.stringify(body)
    }});
    const data = await res.json();
    if(data.success){{
      msg.innerHTML = '<div class="alert success">Mot de passe réinitialisé ! Tu peux maintenant te connecter avec ce nouveau mot de passe.</div>';
    }} else {{
      msg.innerHTML = '<div class="alert error">' + (data.error || 'Erreur') + '</div>';
    }}
  }} catch(e) {{
    msg.innerHTML = '<div class="alert error">Erreur serveur.</div>';
  }}
}}
</script></body></html>"""

    return _do_reset_password()


def _do_reset_password():
    from flask_jwt_extended import verify_jwt_in_request

    try:
        verify_jwt_in_request()
        claims = get_jwt()
        if not claims.get("is_admin"):
            return jsonify({"error": "Accès réservé aux administrateurs"}), 403
    except Exception:
        return jsonify({"error": "Authentification admin requise"}), 401

    data = request.get_json()
    email = data.get("email")
    new_password = data.get("password")

    if not email or not new_password:
        return jsonify({"error": "email et password requis"}), 400

    new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET password_hash = %s WHERE email = %s RETURNING id;",
        (new_hash, email)
    )
    updated = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    if not updated:
        return jsonify({"error": "Aucun compte trouvé avec cet email"}), 404

    return jsonify({"success": True})


@app.route("/debug/config", methods=["GET"])
def debug_config():
    return jsonify({
        "PAYOUT_WALLET_PRIVATE_KEY_configuree": bool(PAYOUT_WALLET_PRIVATE_KEY),
        "PREMIUM_RECEIVE_ADDRESS_calculee": PREMIUM_RECEIVE_ADDRESS,
        "DATABASE_URL_configuree": bool(DATABASE_URL),
        "ADMIN_PASSWORD_configuree": bool(ADMIN_PASSWORD),
        "JWT_SECRET_KEY_est_la_valeur_par_defaut": app.config["JWT_SECRET_KEY"] == "change-moi-absolument",
    })


@app.route("/telegram/generate-code", methods=["POST"])
@jwt_required()
def telegram_generate_code():
    import random
    import datetime as _datetime

    user_id = int(get_jwt_identity())
    code = str(random.randint(100000, 999999))
    expires_at = _datetime.datetime.utcnow() + _datetime.timedelta(minutes=5)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO telegram_link_codes (code, user_id, expires_at) VALUES (%s, %s, %s);",
        (code, user_id, expires_at)
    )
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"code": code, "expires_in_minutes": 5})


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json()

    if not update or "message" not in update:
        return jsonify({"ok": True})

    message = update["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()
    telegram_id = message["from"]["id"]
    telegram_username = message["from"].get("username", "")

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE telegram_id = %s;", (telegram_id,))
    linked_user = cur.fetchone()

    if text.startswith("/start"):
        if linked_user:
            send_telegram_message(chat_id, "✅ Ton compte est déjà lié. Utilise /solde ou /parrainage.")
        else:
            send_telegram_message(
                chat_id,
                "👋 Bienvenue sur le bot CashMoney !\n\n"
                "Pour lier ce compte Telegram à ton compte sur le site :\n"
                "1. Va sur ton dashboard cashmoney\n"
                "2. Clique sur \"Lier mon compte Telegram\"\n"
                "3. Reviens ici et envoie : /lier CODE"
            )

    elif text.startswith("/lier"):
        parts = text.split()
        if len(parts) != 2:
            send_telegram_message(chat_id, "Utilisation : /lier 123456")
        else:
            code = parts[1]
            import datetime as _datetime
            cur.execute(
                "SELECT * FROM telegram_link_codes WHERE code = %s AND used = FALSE AND expires_at > %s;",
                (code, _datetime.datetime.utcnow())
            )
            code_row = cur.fetchone()

            if not code_row:
                send_telegram_message(chat_id, "❌ Code invalide ou expiré. Génère un nouveau code sur le site.")
            else:
                try:
                    cur.execute(
                        "UPDATE users SET telegram_id = %s, telegram_username = %s WHERE id = %s;",
                        (telegram_id, telegram_username, code_row["user_id"])
                    )
                    cur.execute("UPDATE telegram_link_codes SET used = TRUE WHERE code = %s;", (code,))
                    conn.commit()
                    send_telegram_message(chat_id, "🎉 Compte lié avec succès ! Utilise /solde ou /parrainage.")
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    send_telegram_message(chat_id, "❌ Ce compte Telegram est déjà lié à un autre compte cashmoney.")

    elif text.startswith("/solde"):
        if not linked_user:
            send_telegram_message(chat_id, "Ton compte Telegram n'est pas encore lié. Envoie /start pour voir comment faire.")
        else:
            send_telegram_message(chat_id, f"💰 Ton solde actuel : {float(linked_user['balance'])} USDT")

    elif text.startswith("/parrainage"):
        if not linked_user:
            send_telegram_message(chat_id, "Ton compte Telegram n'est pas encore lié. Envoie /start pour voir comment faire.")
        else:
            link = f"{SITE_URL}/register?ref={linked_user['referral_code']}"
            send_telegram_message(chat_id, f"🎁 Ton lien de parrainage :\n{link}")

    else:
        send_telegram_message(
            chat_id,
            "Commandes disponibles :\n/start - Infos\n/lier CODE - Lier ton compte\n/solde - Voir ton solde\n/parrainage - Ton lien de parrainage"
        )

    cur.close()
    conn.close()
    return jsonify({"ok": True})


@app.route("/")
def index():
    return jsonify({"status": "ok", "message": "cashmoney watch-to-earn API en ligne"})


TEST_PAGE_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Test API cashmoney</title>
<style>
  body { font-family: sans-serif; max-width: 480px; margin: 20px auto; padding: 0 16px; background: #f5f5f7; }
  h2 { margin-top: 32px; }
  input, button { width: 100%; padding: 10px; margin: 6px 0; box-sizing: border-box; font-size: 16px; }
  button { background: #6c47ff; color: white; border: none; border-radius: 6px; cursor: pointer; }
  button:active { opacity: 0.8; }
  pre { background: #222; color: #0f0; padding: 12px; border-radius: 6px; overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
  .section { background: white; padding: 16px; border-radius: 10px; margin-bottom: 16px; }
</style>
</head>
<body>

<h1>🔧 Test API cashmoney</h1>

<div class="section">
  <label>URL de ton API</label>
  <input id="baseUrl" value="">
</div>

<div class="section">
  <h2>Inscription</h2>
  <input id="regEmail" placeholder="Email" value="test@example.com">
  <input id="regPassword" type="password" placeholder="Mot de passe" value="motdepasse123">
  <input id="regWallet" placeholder="Adresse wallet (optionnel)">
  <button onclick="register()">S'inscrire</button>
</div>

<div class="section">
  <h2>Connexion</h2>
  <input id="loginEmail" placeholder="Email" value="test@example.com">
  <input id="loginPassword" type="password" placeholder="Mot de passe" value="motdepasse123">
  <button onclick="login()">Se connecter</button>
</div>

<div class="section">
  <h2>Solde</h2>
  <button onclick="getBalance()">Voir mon solde</button>
</div>

<div class="section">
  <h2>Vidéos</h2>
  <button onclick="listVideos()">Voir les vidéos</button>
</div>

<h2>Résultat</h2>
<pre id="output">Les résultats s'afficheront ici...</pre>

<script>
let token = null;

// Pré-remplit automatiquement avec l'URL actuelle du site
document.getElementById("baseUrl").value = window.location.origin;

function show(data) {
  document.getElementById("output").textContent = JSON.stringify(data, null, 2);
}

function baseUrl() {
  return document.getElementById("baseUrl").value.replace(/\\/$/, "");
}

async function register() {
  const body = {
    email: document.getElementById("regEmail").value,
    password: document.getElementById("regPassword").value,
    wallet_address: document.getElementById("regWallet").value || null
  };
  try {
    const res = await fetch(baseUrl() + "/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (data.access_token) token = data.access_token;
    show(data);
  } catch (e) {
    show({ error: e.message });
  }
}

async function login() {
  const body = {
    email: document.getElementById("loginEmail").value,
    password: document.getElementById("loginPassword").value
  };
  try {
    const res = await fetch(baseUrl() + "/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (data.access_token) token = data.access_token;
    show(data);
  } catch (e) {
    show({ error: e.message });
  }
}

async function getBalance() {
  if (!token) return show({ error: "Connecte-toi d'abord (Inscription ou Connexion)" });
  try {
    const res = await fetch(baseUrl() + "/balance", {
      headers: { "Authorization": "Bearer " + token }
    });
    const data = await res.json();
    show(data);
  } catch (e) {
    show({ error: e.message });
  }
}

async function listVideos() {
  try {
    const res = await fetch(baseUrl() + "/videos");
    const data = await res.json();
    show(data);
  } catch (e) {
    show({ error: e.message });
  }
}
</script>

</body>
</html>
"""


@app.route("/test.html")
def test_page():
    return TEST_PAGE_HTML


init_db()

if PREMIUM_RECEIVE_ADDRESS:
    import threading
    threading.Thread(target=poll_premium_payments, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
