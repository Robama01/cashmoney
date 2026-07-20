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

    # Migrations : ajoute les colonnes manquantes si la table existait déjà avant cette version
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_address TEXT;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS balance NUMERIC DEFAULT 0;")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();")
    cur.execute("ALTER TABLE payouts ADD COLUMN IF NOT EXISTS tx_hash TEXT;")
    cur.execute("ALTER TABLE payouts ADD COLUMN IF NOT EXISTS error_message TEXT;")

    conn.commit()
    cur.close()
    conn.close()


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

@app.route("/register", methods=["GET", "POST"])
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
<button class="btn gold" style="width:100%" onclick="doRegister()">S'inscrire</button>
</div></div>
<script>
async function doRegister(){{
  const msg = document.getElementById('msg');
  try {{
    const body = {{
      email: document.getElementById('email').value,
      password: document.getElementById('password').value,
      wallet_address: document.getElementById('wallet').value || null
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
        # Solde remis à zéro uniquement si l'envoi a réussi
        cur.execute("UPDATE users SET balance = 0 WHERE id = %s;", (user_id,))
    else:
        cur.execute(
            "INSERT INTO payouts (user_id, amount, status, error_message) VALUES (%s, %s, 'failed', %s);",
            (user_id, amount, result)
        )
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

async function loadVideos(){{
  const res = await fetch('/videos');
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
      <iframe width="100%" height="220" src="https://www.youtube.com/embed/${{v.youtube_id}}" frameborder="0" allowfullscreen></iframe>
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
    msg.innerHTML = '<div class="alert success">+' + data.reward_given + ' USDT crédité ! Nouveau solde : ' + data.new_balance + ' USDT' + (data.payout_triggered ? ' — Retrait automatique déclenché 🎉' : '') + '</div>';
    loadBalance();
  }} else {{
    msg.innerHTML = '<div class="alert error">' + (data.error || 'Erreur') + '</div>';
  }}
}}

loadBalance();
loadVideos();
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
<div class="card">
  <h2>💸 Historique des retraits</h2>
  <p style="color:#888;margin-bottom:1rem">Les retraits sont automatiques dès que ton solde atteint le seuil configuré.</p>
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

async function loadPayouts(){{
  const res = await fetch('/payouts', {{headers:{{'Authorization':'Bearer '+token}}}});
  const payouts = await res.json();
  const tbody = document.querySelector('#payoutsTable tbody');
  if(!payouts.length){{
    tbody.innerHTML = '<tr><td colspan="4" style="color:#888">Aucun retrait pour le moment.</td></tr>';
    return;
  }}
  tbody.innerHTML = payouts.map(p => `
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


@app.route("/admin/videos")
def admin_videos_page():
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ajouter une vidéo - CashMoney</title><style>{SHARED_CSS}</style></head><body>
<nav class="nav"><div class="logo">💰 CashMoney Admin</div><div><a href="/dashboard">Dashboard</a></div></nav>
<div class="box"><div class="card" style="max-width:500px;margin:2rem auto">
<h2>Ajouter une vidéo sponsorisée</h2>
<p style="color:#888;margin-bottom:1rem">⚠️ Page temporaire, sans protection. À sécuriser avant lancement public.</p>
<div id="msg"></div>
<label>ID YouTube (ex: dQw4w9WgXcQ, la partie après watch?v=)</label>
<input id="youtubeId" placeholder="dQw4w9WgXcQ">
<label>Titre</label>
<input id="title" placeholder="Titre de la vidéo">
<label>Nom du sponsor</label>
<input id="sponsor" placeholder="Nom de la marque">
<label>Récompense (en USDT)</label>
<input id="reward" type="number" step="0.01" placeholder="0.10">
<label>Durée minimum de visionnage (secondes)</label>
<input id="minSeconds" type="number" value="30">
<button class="btn gold" style="width:100%" onclick="addVideo()">Ajouter la vidéo</button>
</div></div>
<script>
async function addVideo(){{
  const body = {{
    youtube_id: document.getElementById('youtubeId').value,
    title: document.getElementById('title').value,
    sponsor_name: document.getElementById('sponsor').value,
    reward_amount: parseFloat(document.getElementById('reward').value),
    min_watch_seconds: parseInt(document.getElementById('minSeconds').value) || 30
  }};
  const res = await fetch('/videos', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}});
  const data = await res.json();
  const msg = document.getElementById('msg');
  if(data.id){{
    msg.innerHTML = '<div class="alert success">Vidéo ajoutée avec succès !</div>';
    document.getElementById('youtubeId').value = '';
    document.getElementById('title').value = '';
    document.getElementById('sponsor').value = '';
    document.getElementById('reward').value = '';
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
<title>Réinitialiser mot de passe - CashMoney</title><style>{SHARED_CSS}</style></head><body>
<nav class="nav"><div class="logo">💰 CashMoney Admin</div><div><a href="/dashboard">Dashboard</a></div></nav>
<div class="box"><div class="card" style="max-width:420px;margin:2rem auto">
<h2>Réinitialiser un mot de passe</h2>
<p style="color:#888;margin-bottom:1rem">⚠️ Page temporaire, sans protection. À supprimer avant lancement public.</p>
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
    const res = await fetch('/admin/reset-password', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}});
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
