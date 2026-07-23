
# Créer le nouveau app.py avec templates séparés et toutes les corrections

app_py = '''import os
import bcrypt
import psycopg2
import time as _time
import threading
import logging
import functools
import hashlib
import base64
import json
from datetime import datetime
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, g, render_template
from flask_jwt_extended import (
    JWTManager, create_access_token,
    jwt_required, get_jwt_identity, verify_jwt_in_request, get_jwt
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from web3 import Web3

# ---------- SENTRY ----------
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration

SENTRY_DSN = os.environ.get("SENTRY_DSN")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[FlaskIntegration()],
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", 0.1)),
        environment=os.environ.get("SENTRY_ENVIRONMENT", "development"),
        release=os.environ.get("GITHUB_SHA", "unknown")
    )

# ---------- LOGGING ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler('cashmoney.log')]
)
logger = logging.getLogger(__name__)

# ---------- REDIS CACHE ----------
import redis
from functools import wraps

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    REDIS_AVAILABLE = True
    logger.info("Redis connecté")
except Exception as e:
    logger.warning(f"Redis non disponible: {e}")
    redis_client = None
    REDIS_AVAILABLE = False

def cache_result(expire_seconds=300, key_prefix="cache"):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not REDIS_AVAILABLE:
                return f(*args, **kwargs)
            cache_key = f"{key_prefix}:{f.__name__}:{hash(str(args) + str(sorted(kwargs.items())))}"
            cached = redis_client.get(cache_key)
            if cached:
                try:
                    return json.loads(cached)
                except:
                    pass
            result = f(*args, **kwargs)
            try:
                redis_client.setex(cache_key, expire_seconds, json.dumps(result, default=str))
            except:
                pass
            return result
        return wrapper
    return decorator

def invalidate_cache(pattern="cache:*"):
    if not REDIS_AVAILABLE:
        return
    try:
        keys = redis_client.keys(pattern)
        if keys:
            redis_client.delete(*keys)
    except Exception as e:
        logger.error(f"Erreur invalidation cache: {e}")

# ---------- CONFIG BLOCKCHAIN ----------
BSC_RPC_URL = os.environ.get("BSC_RPC_URL", "https://bsc-dataseed.binance.org/")
PAYOUT_WALLET_PRIVATE_KEY = os.environ.get("PAYOUT_WALLET_PRIVATE_KEY")
USDT_BEP20_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"
USDT_DECIMALS = 18

ERC20_ABI = [
    {"constant": False, "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}
]

try:
    w3 = Web3(Web3.HTTPProvider(BSC_RPC_URL, request_kwargs={'timeout': 30}))
    if not w3.is_connected():
        raise ConnectionError("BSC RPC non disponible")
    usdt_contract = w3.eth.contract(address=Web3.to_checksum_address(USDT_BEP20_CONTRACT), abi=ERC20_ABI)
    logger.info("BSC connecté")
except Exception as e:
    logger.error(f"Erreur BSC: {e}")
    w3 = None
    usdt_contract = None

# ---------- CONFIG FLASK ----------
app = Flask(__name__, template_folder='templates')

limiter = Limiter(app=app, key_func=get_remote_address, default_limits=["200 per day", "50 per hour"])

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL requise")

JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
if not JWT_SECRET_KEY or JWT_SECRET_KEY == "change-moi-absolument":
    raise ValueError("JWT_SECRET_KEY doit être configurée avec une valeur sécurisée")

app.config["JWT_SECRET_KEY"] = JWT_SECRET_KEY
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = 86400
jwt = JWTManager(app)

WITHDRAWAL_THRESHOLD = float(os.environ.get("WITHDRAWAL_THRESHOLD", 1000))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

PREMIUM_PRICE_USDT = float(os.environ.get("PREMIUM_PRICE_USDT", 0.90))
PREMIUM_RECEIVE_ADDRESS = os.environ.get("PREMIUM_RECEIVE_ADDRESS")
if not PREMIUM_RECEIVE_ADDRESS and PAYOUT_WALLET_PRIVATE_KEY:
    try:
        PREMIUM_RECEIVE_ADDRESS = w3.eth.account.from_key(PAYOUT_WALLET_PRIVATE_KEY).address
    except:
        PREMIUM_RECEIVE_ADDRESS = None

TRANSFER_EVENT_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex()

# Verrous pour retraits
payout_locks = {}
payout_locks_lock = threading.Lock()

# ---------- MLM CONFIG ----------
MLM_ENABLED = os.environ.get("MLM_ENABLED", "true").lower() == "true"
MLM_UPGRADE_AUTO = os.environ.get("MLM_UPGRADE_AUTO", "false").lower() == "true"

MLM_LEVELS = [
    {"level": 1, "name": "Débutant", "members": 2, "gain_per_member": 1.73, "upgrade_cost": 0.87},
    {"level": 2, "name": "Influenceur", "members": 4, "gain_per_member": 0.87, "upgrade_cost": 1.73},
    {"level": 3, "name": "Achiever", "members": 8, "gain_per_member": 1.73, "upgrade_cost": 3.46},
    {"level": 4, "name": "Ambassadeur", "members": 16, "gain_per_member": 3.46, "upgrade_cost": 6.92},
    {"level": 5, "name": "Pionnier", "members": 32, "gain_per_member": 6.92, "upgrade_cost": 13.84},
    {"level": 6, "name": "Mentor", "members": 64, "gain_per_member": 13.84, "upgrade_cost": 27.68},
    {"level": 7, "name": "Champion", "members": 128, "gain_per_member": 27.68, "upgrade_cost": 55.36},
    {"level": 8, "name": "Director", "members": 256, "gain_per_member": 55.36, "upgrade_cost": 110.72},
    {"level": 9, "name": "Titan", "members": 512, "gain_per_member": 110.72, "upgrade_cost": 221.44},
    {"level": 10, "name": "Icon", "members": 1024, "gain_per_member": 221.44, "upgrade_cost": 442.88},
]

# Direct referral config
DIRECT_REFERRAL_BONUS_USDT = float(os.environ.get("DIRECT_REFERRAL_BONUS_USDT", 0.50))
DIRECT_REFERRAL_PREMIUM_BONUS_USDT = float(os.environ.get("DIRECT_REFERRAL_PREMIUM_BONUS_USDT", 1.0))
MATCHING_BONUS_PERCENT = float(os.environ.get("MATCHING_BONUS_PERCENT", 10.0))
BINARY_BONUS_PERCENT = float(os.environ.get("BINARY_BONUS_PERCENT", 5.0))
BINARY_BONUS_THRESHOLD = float(os.environ.get("BINARY_BONUS_THRESHOLD", 100.0))

# ---------- UTILS ----------
def get_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def admin_required(f):
    @functools.wraps(f)
    @jwt_required()
    def wrapper(*args, **kwargs):
        claims = get_jwt()
        if not claims.get("is_admin"):
            logger.warning(f"Accès admin refusé: {get_jwt_identity()}")
            return jsonify({"error": "Accès réservé aux administrateurs"}), 403
        return f(*args, **kwargs)
    return wrapper

def address_to_topic(address: str) -> str:
    return "0x" + "0" * 24 + address.lower().replace("0x", "")

# ---------- BLOCKCHAIN ----------
def send_usdt_bep20(to_address: str, amount_usdt: float, max_retries=3):
    if not PAYOUT_WALLET_PRIVATE_KEY:
        return False, "PAYOUT_WALLET_PRIVATE_KEY non configurée"
    if not w3 or not w3.is_connected():
        return False, "Connexion BSC indisponible"

    for attempt in range(max_retries):
        try:
            account = w3.eth.account.from_key(PAYOUT_WALLET_PRIVATE_KEY)
            to_checksum = Web3.to_checksum_address(to_address)
            amount_wei = int(amount_usdt * (10 ** USDT_DECIMALS))
            nonce = w3.eth.get_transaction_count(account.address, 'pending')
            gas_price = w3.eth.gas_price
            tx_params = {"chainId": 56, "gasPrice": gas_price, "nonce": nonce}
            estimated_gas = usdt_contract.functions.transfer(to_checksum, amount_wei).estimate_gas(tx_params)
            tx_params["gas"] = int(estimated_gas * 1.2)
            tx = usdt_contract.functions.transfer(to_checksum, amount_wei).build_transaction(tx_params)
            signed_tx = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt.status == 1:
                logger.info(f"Transaction réussie: {tx_hash.hex()}")
                return True, tx_hash.hex()
            else:
                return False, f"Transaction échouée on-chain: {tx_hash.hex()}"
        except Exception as e:
            logger.error(f"Tentative {attempt + 1}/{max_retries} échouée: {e}")
            if attempt < max_retries - 1:
                _time.sleep(2 ** attempt)
            else:
                return False, str(e)
    return False, "Toutes les tentatives ont échoué"

def trigger_payout(cur, user_id, amount):
    cur.execute("SELECT wallet_address FROM users WHERE id = %s;", (user_id,))
    user = cur.fetchone()
    wallet_address = user["wallet_address"] if user else None
    if not wallet_address:
        cur.execute("INSERT INTO payouts (user_id, amount, status, error_message) VALUES (%s, %s, 'failed', %s);",
            (user_id, amount, "Aucune adresse wallet enregistrée"))
        return
    success, result = send_usdt_bep20(wallet_address, amount)
    if success:
        cur.execute("INSERT INTO payouts (user_id, amount, status, tx_hash) VALUES (%s, %s, 'sent', %s);",
            (user_id, amount, result))
        cur.execute("UPDATE users SET balance = 0 WHERE id = %s;", (user_id,))
        logger.info(f"Retrait réussi: {amount} USDT pour user {user_id}")
    else:
        cur.execute("INSERT INTO payouts (user_id, amount, status, error_message, retry_count) VALUES (%s, %s, 'failed', %s, 1);",
            (user_id, amount, result))
        logger.error(f"Retrait échoué: {result}")

# ---------- INIT DB ----------
def init_db():
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
            wallet_address TEXT, balance NUMERIC DEFAULT 0, is_premium BOOLEAN DEFAULT FALSE,
            facebook_link TEXT, referral_code TEXT UNIQUE, referred_by INTEGER REFERENCES users(id),
            created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id SERIAL PRIMARY KEY, youtube_id TEXT NOT NULL, title TEXT NOT NULL,
            sponsor_name TEXT, reward_amount NUMERIC NOT NULL, min_watch_seconds INTEGER DEFAULT 30,
            active BOOLEAN DEFAULT TRUE, investment_amount NUMERIC DEFAULT 0, maintenance_fee NUMERIC DEFAULT 0,
            remaining_budget NUMERIC DEFAULT 0, premium_only BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS watch_logs (
            id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), video_id INTEGER REFERENCES videos(id),
            watched_seconds INTEGER NOT NULL, reward_given NUMERIC NOT NULL, ip_address TEXT, user_agent TEXT,
            created_at TIMESTAMP DEFAULT NOW(), UNIQUE (user_id, video_id)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payouts (
            id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), amount NUMERIC NOT NULL,
            status TEXT DEFAULT 'pending', tx_hash TEXT, error_message TEXT, retry_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS premium_payments (
            id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), tx_hash TEXT UNIQUE NOT NULL,
            amount NUMERIC NOT NULL, created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS blockchain_scan_state (
            key TEXT PRIMARY KEY, value TEXT
        );
    """)
    
    # MLM tables
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mlm_binary_tree (
            id SERIAL PRIMARY KEY, user_id INTEGER UNIQUE REFERENCES users(id),
            parent_id INTEGER REFERENCES users(id), left_child_id INTEGER REFERENCES users(id),
            right_child_id INTEGER REFERENCES users(id), position TEXT CHECK (position IN ('left', 'right')),
            current_level INTEGER DEFAULT 1, total_earned NUMERIC DEFAULT 0, total_members INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mlm_earnings (
            id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), from_user_id INTEGER REFERENCES users(id),
            level INTEGER NOT NULL, amount NUMERIC NOT NULL, description TEXT, created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mlm_upgrades (
            id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), old_level INTEGER NOT NULL,
            new_level INTEGER NOT NULL, cost NUMERIC NOT NULL, status TEXT DEFAULT 'completed', created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    
    # Direct referral tables
    cur.execute("""
        CREATE TABLE IF NOT EXISTS direct_referrals (
            id SERIAL PRIMARY KEY, referrer_id INTEGER REFERENCES users(id), referred_id INTEGER REFERENCES users(id) UNIQUE,
            direct_bonus NUMERIC DEFAULT 0, premium_bonus NUMERIC DEFAULT 0, matching_earned NUMERIC DEFAULT 0,
            status TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS binary_bonuses (
            id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), left_volume NUMERIC DEFAULT 0,
            right_volume NUMERIC DEFAULT 0, bonus_amount NUMERIC DEFAULT 0, calculated_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS direct_referral_stats (
            user_id INTEGER PRIMARY KEY REFERENCES users(id), total_direct_referrals INTEGER DEFAULT 0,
            total_direct_bonus NUMERIC DEFAULT 0, total_premium_bonus NUMERIC DEFAULT 0,
            total_matching_bonus NUMERIC DEFAULT 0, total_binary_bonus NUMERIC DEFAULT 0, updated_at TIMESTAMP DEFAULT NOW()
        );
    """)
    
    # Indexes
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_watch_logs_user ON watch_logs(user_id);",
        "CREATE INDEX IF NOT EXISTS idx_watch_logs_video ON watch_logs(video_id);",
        "CREATE INDEX IF NOT EXISTS idx_payouts_user ON payouts(user_id);",
        "CREATE INDEX IF NOT EXISTS idx_payouts_status ON payouts(status);",
        "CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON mlm_binary_tree(parent_id);",
        "CREATE INDEX IF NOT EXISTS idx_referrals_referred ON mlm_binary_tree(user_id);",
        "CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code);",
        "CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by);",
        "CREATE INDEX IF NOT EXISTS idx_mlm_earnings_user ON mlm_earnings(user_id);",
        "CREATE INDEX IF NOT EXISTS idx_mlm_earnings_level ON mlm_earnings(level);",
        "CREATE INDEX IF NOT EXISTS idx_direct_ref_referrer ON direct_referrals(referrer_id);",
        "CREATE INDEX IF NOT EXISTS idx_direct_ref_referred ON direct_referrals(referred_id);",
        "CREATE INDEX IF NOT EXISTS idx_binary_bonus_user ON binary_bonuses(user_id);",
    ]:
        cur.execute(idx)
    
    conn.commit()
    cur.close()
    conn.close()
    logger.info("Base de données initialisée")

# ---------- MLM FUNCTIONS ----------
def generate_referral_code(user_id: int) -> str:
    timestamp = str(int(_time.time()))
    raw = f"{user_id}-{timestamp}-{JWT_SECRET_KEY[:16]}"
    hashed = hashlib.sha256(raw.encode()).digest()
    code = base64.urlsafe_b64encode(hashed[:6]).decode().rstrip('=').upper()
    return f"CM{code}"

def assign_referral_code(user_id: int) -> str:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT referral_code FROM users WHERE id = %s;", (user_id,))
    existing = cur.fetchone()
    if existing and existing["referral_code"]:
        code = existing["referral_code"]
    else:
        for _ in range(10):
            code = generate_referral_code(user_id)
            try:
                cur.execute("UPDATE users SET referral_code = %s WHERE id = %s;", (code, user_id))
                conn.commit()
                break
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                continue
        else:
            code = None
    cur.close()
    conn.close()
    return code

def get_available_position(parent_id: int) -> tuple:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT left_child_id, right_child_id FROM mlm_binary_tree WHERE user_id = %s;", (parent_id,))
    parent = cur.fetchone()
    if not parent:
        cur.close(); conn.close()
        return None, None
    if not parent["left_child_id"]:
        cur.close(); conn.close()
        return parent_id, "left"
    elif not parent["right_child_id"]:
        cur.close(); conn.close()
        return parent_id, "right"
    
    queue = [parent_id]
    visited = set()
    while queue:
        current_id = queue.pop(0)
        if current_id in visited:
            continue
        visited.add(current_id)
        cur.execute("SELECT user_id, left_child_id, right_child_id FROM mlm_binary_tree WHERE parent_id = %s OR user_id = %s;", (current_id, current_id))
        rows = cur.fetchall()
        for row in rows:
            if row["user_id"] == current_id:
                if not row["left_child_id"]:
                    cur.close(); conn.close()
                    return current_id, "left"
                elif not row["right_child_id"]:
                    cur.close(); conn.close()
                    return current_id, "right"
                if row["left_child_id"]:
                    queue.append(row["left_child_id"])
                if row["right_child_id"]:
                    queue.append(row["right_child_id"])
    cur.close(); conn.close()
    return None, None

def place_in_binary_tree(user_id: int, referral_code: str = None) -> dict:
    if not MLM_ENABLED:
        return {"success": True, "mlm": False}
    conn = get_connection()
    cur = conn.cursor()
    try:
        parent_id, position = None, None
        if referral_code:
            cur.execute("SELECT user_id FROM mlm_binary_tree WHERE user_id = (SELECT id FROM users WHERE referral_code = %s);", (referral_code,))
            referrer = cur.fetchone()
            if referrer:
                parent_id, position = get_available_position(referrer["user_id"])
        if not parent_id:
            cur.execute("SELECT user_id FROM mlm_binary_tree WHERE parent_id IS NULL LIMIT 1;")
            root = cur.fetchone()
            if root:
                parent_id, position = get_available_position(root["user_id"])
            else:
                parent_id, position = None, None
        
        cur.execute("INSERT INTO mlm_binary_tree (user_id, parent_id, position, current_level) VALUES (%s, %s, %s, 1) RETURNING id;",
            (user_id, parent_id, position))
        tree_id = cur.fetchone()["id"]
        if parent_id and position:
            if position == "left":
                cur.execute("UPDATE mlm_binary_tree SET left_child_id = %s WHERE user_id = %s;", (user_id, parent_id))
            else:
                cur.execute("UPDATE mlm_binary_tree SET right_child_id = %s WHERE user_id = %s;", (user_id, parent_id))
        conn.commit()
        logger.info(f"User {user_id} placé dans l'arbre MLM")
        earnings_result = calculate_and_distribute_earnings(user_id)
        return {"success": True, "mlm": True, "parent_id": parent_id, "position": position, "earnings": earnings_result}
    except Exception as e:
        conn.rollback()
        logger.error(f"Erreur placement MLM: {e}")
        return {"success": False, "error": str(e)}
    finally:
        cur.close(); conn.close()

def calculate_and_distribute_earnings(new_user_id: int) -> list:
    earnings = []
    conn = get_connection()
    cur = conn.cursor()
    try:
        current_id = new_user_id
        level = 0
        while current_id and level < 10:
            cur.execute("SELECT parent_id, current_level FROM mlm_binary_tree WHERE user_id = %s;", (current_id,))
            row = cur.fetchone()
            if not row or not row["parent_id"]:
                break
            parent_id, parent_level = row["parent_id"], row["current_level"]
            level += 1
            if parent_level < level:
                break
            level_config = MLM_LEVELS[level - 1]
            gain_amount = level_config["gain_per_member"]
            cur.execute("UPDATE users SET balance = balance + %s WHERE id = %s RETURNING balance;", (gain_amount, parent_id))
            new_balance = cur.fetchone()["balance"]
            cur.execute("INSERT INTO mlm_earnings (user_id, from_user_id, level, amount, description) VALUES (%s, %s, %s, %s, %s);",
                (parent_id, new_user_id, level, gain_amount, f"Gain niveau {level} ({level_config['name']})"))
            cur.execute("UPDATE mlm_binary_tree SET total_earned = total_earned + %s, total_members = total_members + 1, updated_at = NOW() WHERE user_id = %s;",
                (gain_amount, parent_id))
            earnings.append({"user_id": parent_id, "level": level, "amount": gain_amount, "new_balance": float(new_balance)})
            current_id = parent_id
        conn.commit()
        if MLM_UPGRADE_AUTO:
            check_auto_upgrades(new_user_id)
        return earnings
    except Exception as e:
        conn.rollback()
        logger.error(f"Erreur distribution gains MLM: {e}")
        return []
    finally:
        cur.close(); conn.close()

def check_auto_upgrades(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT current_level, total_members FROM mlm_binary_tree WHERE user_id = %s;", (user_id,))
        user_mlm = cur.fetchone()
        if not user_mlm or user_mlm["current_level"] >= 10:
            return
        next_level_config = MLM_LEVELS[user_mlm["current_level"]]
        required_members = sum(l["members"] for l in MLM_LEVELS[:user_mlm["current_level"]])
        if user_mlm["total_members"] >= required_members:
            upgrade_cost = next_level_config["upgrade_cost"]
            cur.execute("SELECT balance FROM users WHERE id = %s;", (user_id,))
            balance = cur.fetchone()["balance"]
            if float(balance) >= upgrade_cost:
                cur.execute("UPDATE users SET balance = balance - %s WHERE id = %s;", (upgrade_cost, user_id))
                cur.execute("UPDATE mlm_binary_tree SET current_level = %s WHERE user_id = %s;", (user_mlm["current_level"] + 1, user_id))
                cur.execute("INSERT INTO mlm_upgrades (user_id, old_level, new_level, cost) VALUES (%s, %s, %s, %s);",
                    (user_id, user_mlm["current_level"], user_mlm["current_level"] + 1, upgrade_cost))
                conn.commit()
                logger.info(f"Upgrade auto: user {user_id} niveau {user_mlm['current_level']} -> {user_mlm['current_level'] + 1}")
    except Exception as e:
        conn.rollback()
        logger.error(f"Erreur upgrade auto: {e}")
    finally:
        cur.close(); conn.close()

# ---------- DIRECT REFERRAL FUNCTIONS ----------
def process_direct_referral(referrer_id: int, referred_id: int) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    try:
        if referrer_id == referred_id:
            return {"success": False, "error": "Auto-parrainage interdit"}
        cur.execute("SELECT id FROM direct_referrals WHERE referred_id = %s;", (referred_id,))
        if cur.fetchone():
            return {"success": False, "error": "Déjà parrainé"}
        cur.execute("UPDATE users SET balance = balance + %s WHERE id = %s RETURNING balance;", (DIRECT_REFERRAL_BONUS_USDT, referrer_id))
        new_balance = cur.fetchone()["balance"]
        cur.execute("INSERT INTO direct_referrals (referrer_id, referred_id, direct_bonus) VALUES (%s, %s, %s);",
            (referrer_id, referred_id, DIRECT_REFERRAL_BONUS_USDT))
        cur.execute("""INSERT INTO direct_referral_stats (user_id, total_direct_referrals, total_direct_bonus)
            VALUES (%s, 1, %s) ON CONFLICT (user_id) DO UPDATE SET
            total_direct_referrals = direct_referral_stats.total_direct_referrals + 1,
            total_direct_bonus = direct_referral_stats.total_direct_bonus + %s, updated_at = NOW();""",
            (referrer_id, DIRECT_REFERRAL_BONUS_USDT, DIRECT_REFERRAL_BONUS_USDT))
        conn.commit()
        logger.info(f"Parrainage direct: {referrer_id} reçoit {DIRECT_REFERRAL_BONUS_USDT} USDT")
        if float(new_balance) >= WITHDRAWAL_THRESHOLD:
            with payout_locks_lock:
                if referrer_id not in payout_locks or not payout_locks[referrer_id].locked():
                    lock = threading.Lock()
                    payout_locks[referrer_id] = lock
            if lock.acquire(blocking=False):
                try:
                    trigger_payout(cur, referrer_id, float(new_balance))
                    conn.commit()
                finally:
                    lock.release()
        return {"success": True, "bonus": DIRECT_REFERRAL_BONUS_USDT, "new_balance": float(new_balance)}
    except Exception as e:
        conn.rollback()
        logger.error(f"Erreur parrainage direct: {e}")
        return {"success": False, "error": str(e)}
    finally:
        cur.close(); conn.close()

def process_premium_direct_bonus(referrer_id: int, referred_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM direct_referrals WHERE referrer_id = %s AND referred_id = %s;", (referrer_id, referred_id))
        if not cur.fetchone():
            return
        cur.execute("SELECT premium_bonus FROM direct_referrals WHERE referrer_id = %s AND referred_id = %s;", (referrer_id, referred_id))
        ref = cur.fetchone()
        if ref and float(ref["premium_bonus"] or 0) > 0:
            return
        cur.execute("UPDATE users SET balance = balance + %s WHERE id = %s RETURNING balance;", (DIRECT_REFERRAL_PREMIUM_BONUS_USDT, referrer_id))
        new_balance = cur.fetchone()["balance"]
        cur.execute("UPDATE direct_referrals SET premium_bonus = %s WHERE referrer_id = %s AND referred_id = %s;",
            (DIRECT_REFERRAL_PREMIUM_BONUS_USDT, referrer_id, referred_id))
        cur.execute("""INSERT INTO direct_referral_stats (user_id, total_premium_bonus)
            VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET
            total_premium_bonus = direct_referral_stats.total_premium_bonus + %s, updated_at = NOW();""",
            (referrer_id, DIRECT_REFERRAL_PREMIUM_BONUS_USDT, DIRECT_REFERRAL_PREMIUM_BONUS_USDT))
        conn.commit()
        logger.info(f"Bonus Premium direct: {referrer_id} reçoit {DIRECT_REFERRAL_PREMIUM_BONUS_USDT} USDT")
        if float(new_balance) >= WITHDRAWAL_THRESHOLD:
            with payout_locks_lock:
                if referrer_id not in payout_locks or not payout_locks[referrer_id].locked():
                    lock = threading.Lock()
                    payout_locks[referrer_id] = lock
            if lock.acquire(blocking=False):
                try:
                    trigger_payout(cur, referrer_id, float(new_balance))
                    conn.commit()
                finally:
                    lock.release()
    except Exception as e:
        conn.rollback()
        logger.error(f"Erreur bonus premium direct: {e}")
    finally:
        cur.close(); conn.close()

# ---------- PREMIUM SCANNER ----------
class PremiumPaymentScanner:
    def __init__(self):
        self.running = False
        self.thread = None
        self.stop_event = threading.Event()
    
    def start(self):
        if self.running:
            return
        self.running = True
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._scan_loop, daemon=True)
        self.thread.start()
        logger.info("Scanner premium démarré")
    
    def stop(self):
        self.stop_event.set()
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Scanner premium arrêté")
    
    def _scan_loop(self):
        min_amount_wei = int(PREMIUM_PRICE_USDT * (10 ** USDT_DECIMALS))
        to_topic = address_to_topic(PREMIUM_RECEIVE_ADDRESS)
        while not self.stop_event.is_set():
            try:
                if not w3 or not w3.is_connected():
                    self.stop_event.wait(30)
                    continue
                conn = get_connection()
                cur = conn.cursor()
                cur.execute("SELECT value FROM blockchain_scan_state WHERE key = 'last_block';")
                row = cur.fetchone()
                latest_block = w3.eth.block_number
                from_block = int(row["value"]) + 1 if row else max(0, latest_block - 5000)
                if from_block > latest_block:
                    cur.close(); conn.close()
                    self.stop_event.wait(15)
                    continue
                to_block = min(latest_block, from_block + 2000)
                logs = w3.eth.get_logs({"fromBlock": from_block, "toBlock": to_block,
                    "address": Web3.to_checksum_address(USDT_BEP20_CONTRACT), "topics": [TRANSFER_EVENT_TOPIC, None, to_topic]})
                processed = 0
                for log in logs:
                    try:
                        value = int.from_bytes(log["data"], "big") if isinstance(log["data"], (bytes, bytearray)) else int(log["data"], 16)
                        if value < min_amount_wei:
                            continue
                        sender = "0x" + log["topics"][1].hex()[-40:]
                        tx_hash = log["transactionHash"].hex()
                        cur.execute("SELECT id, is_premium FROM users WHERE LOWER(wallet_address) = LOWER(%s);", (sender,))
                        user = cur.fetchone()
                        if user and not user["is_premium"]:
                            try:
                                cur.execute("INSERT INTO premium_payments (user_id, tx_hash, amount) VALUES (%s, %s, %s);",
                                    (user["id"], tx_hash, value / (10 ** USDT_DECIMALS)))
                                cur.execute("UPDATE users SET is_premium = TRUE WHERE id = %s;", (user["id"],))
                                conn.commit()
                                processed += 1
                                logger.info(f"Premium activé: user {user['id']}")
                                process_premium_direct_bonus(user["referred_by"], user["id"]) if user.get("referred_by") else None
                            except psycopg2.errors.UniqueViolation:
                                conn.rollback()
                    except Exception as e:
                        logger.error(f"Erreur traitement log: {e}")
                        conn.rollback()
                cur.execute("""INSERT INTO blockchain_scan_state (key, value) VALUES ('last_block', %s)
                    ON CONFLICT (key) DO UPDATE SET value = %s;""", (str(to_block), str(to_block)))
                conn.commit()
                cur.close(); conn.close()
                if processed > 0:
                    logger.info(f"{processed} paiements premium traités")
                self.stop_event.wait(15)
            except Exception as e:
                logger.exception(f"Erreur scanner: {e}")
                self.stop_event.wait(30)

scanner = PremiumPaymentScanner()

# ========== ROUTES API ==========

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/register", methods=["POST"])
@limiter.limit("5 per minute")
def register():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    password = data.get("password")
    wallet_address = data.get("wallet_address")
    referral_code = data.get("referral_code", "").strip().upper()
    
    if not email or not password:
        return jsonify({"error": "Email et mot de passe requis"}), 400
    if len(password) < 8:
        return jsonify({"error": "Min 8 caractères"}), 400
    if wallet_address and not Web3.is_address(wallet_address):
        return jsonify({"error": "Wallet invalide"}), 400
    
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO users (email, password_hash, wallet_address) VALUES (%s, %s, %s) RETURNING id, email;",
            (email, password_hash, wallet_address))
        user = cur.fetchone()
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"error": "Email déjà utilisé"}), 409
    finally:
        cur.close(); conn.close()
    
    assign_referral_code(user["id"])
    
    # Direct referral
    direct_result = None
    if referral_code:
        conn_ref = get_connection()
        cur_ref = conn_ref.cursor()
        cur_ref.execute("SELECT id FROM users WHERE referral_code = %s;", (referral_code,))
        referrer = cur_ref.fetchone()
        cur_ref.close(); conn_ref.close()
        if referrer:
            direct_result = process_direct_referral(referrer["id"], user["id"])
    
    # MLM
    mlm_result = place_in_binary_tree(user["id"], referral_code) if MLM_ENABLED else None
    
    token = create_access_token(identity=str(user["id"]))
    return jsonify({"user": user, "access_token": token, "direct_referral": direct_result, "mlm": mlm_result}), 201

@app.route("/login", methods=["POST"])
@limiter.limit("10 per minute")
def login():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    password = data.get("password")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = %s;", (email,))
    user = cur.fetchone()
    cur.close(); conn.close()
    if not user:
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401
    try:
        password_valid = bcrypt.checkpw(password.encode(), user["password_hash"].encode())
    except ValueError:
        return jsonify({"error": "Problème technique"}), 500
    if not password_valid:
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401
    token = create_access_token(identity=str(user["id"]))
    return jsonify({"access_token": token, "balance": float(user["balance"])})

@app.route("/videos", methods=["GET"])
@cache_result(expire_seconds=60, key_prefix="videos")
def list_videos():
    is_premium = False
    try:
        verify_jwt_in_request(optional=True)
        user_id = get_jwt_identity()
        if user_id:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT is_premium FROM users WHERE id = %s;", (int(user_id),))
            row = cur.fetchone()
            cur.close(); conn.close()
            is_premium = bool(row["is_premium"]) if row else False
    except:
        pass
    conn = get_connection()
    cur = conn.cursor()
    if is_premium:
        cur.execute("SELECT * FROM videos WHERE active = TRUE ORDER BY created_at DESC;")
    else:
        cur.execute("SELECT * FROM videos WHERE active = TRUE AND premium_only = FALSE ORDER BY created_at DESC;")
    videos = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(videos)

@app.route("/videos", methods=["POST"])
@admin_required
@limiter.limit("20 per minute")
def add_video():
    data = request.get_json()
    investment = float(data.get("investment_amount", 0) or 0)
    maintenance = float(data.get("maintenance_fee", 0) or 0)
    reward = float(data.get("reward_amount", 0) or 0)
    remaining = investment - maintenance if investment > 0 else 0
    if remaining < 0:
        return jsonify({"error": "Frais > investissement"}), 400
    if reward <= 0:
        return jsonify({"error": "reward_amount > 0 requis"}), 400
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""INSERT INTO videos (youtube_id, title, sponsor_name, reward_amount, min_watch_seconds,
        investment_amount, maintenance_fee, remaining_budget, premium_only) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *;""",
        (data.get("youtube_id"), data.get("title"), data.get("sponsor_name"), reward,
        data.get("min_watch_seconds", 30), investment, maintenance, remaining, bool(data.get("premium_only", False))))
    video = cur.fetchone()
    conn.commit()
    cur.close(); conn.close()
    invalidate_cache("videos:*")
    logger.info(f"Vidéo ajoutée: {video['title']}")
    return jsonify(video), 201

@app.route("/watch", methods=["POST"])
@jwt_required()
@limiter.limit("30 per hour")
def watch_video():
    user_id = int(get_jwt_identity())
    data = request.get_json()
    video_id = data.get("video_id")
    watched_seconds = data.get("watched_seconds", 0)
    if not isinstance(watched_seconds, int) or watched_seconds < 0:
        return jsonify({"error": "Temps invalide"}), 400
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM videos WHERE id = %s AND active = TRUE;", (video_id,))
    video = cur.fetchone()
    if not video:
        cur.close(); conn.close()
        return jsonify({"error": "Vidéo introuvable"}), 404
    if watched_seconds < video["min_watch_seconds"]:
        cur.close(); conn.close()
        return jsonify({"error": f"Min {video['min_watch_seconds']}s requises"}), 400
    if float(video["remaining_budget"]) < float(video["reward_amount"]):
        cur.execute("UPDATE videos SET active = FALSE WHERE id = %s;", (video_id,))
        conn.commit()
        cur.close(); conn.close()
        return jsonify({"error": "Budget épuisé"}), 410
    try:
        cur.execute("""INSERT INTO watch_logs (user_id, video_id, watched_seconds, reward_given, ip_address, user_agent)
            VALUES (%s, %s, %s, %s, %s, %s);""",
            (user_id, video_id, watched_seconds, video["reward_amount"], request.remote_addr, request.user_agent.string[:200] if request.user_agent else None))
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        cur.close(); conn.close()
        return jsonify({"error": "Déjà récompensé"}), 409
    cur.execute("UPDATE videos SET remaining_budget = remaining_budget - %s WHERE id = %s RETURNING remaining_budget;",
        (video["reward_amount"], video_id))
    new_budget = cur.fetchone()["remaining_budget"]
    if float(new_budget) < float(video["reward_amount"]):
        cur.execute("UPDATE videos SET active = FALSE WHERE id = %s;", (video_id,))
    cur.execute("UPDATE users SET balance = balance + %s WHERE id = %s RETURNING balance;", (video["reward_amount"], user_id))
    new_balance = cur.fetchone()["balance"]
    conn.commit()
    payout_triggered = False
    if float(new_balance) >= WITHDRAWAL_THRESHOLD:
        with payout_locks_lock:
            if user_id not in payout_locks or not payout_locks[user_id].locked():
                lock = threading.Lock()
                payout_locks[user_id] = lock
        if lock.acquire(blocking=False):
            try:
                trigger_payout(cur, user_id, float(new_balance))
                conn.commit()
                payout_triggered = True
            finally:
                lock.release()
    cur.close(); conn.close()
    return jsonify({"reward_given": float(video["reward_amount"]), "new_balance": float(new_balance), "payout_triggered": payout_triggered})

@app.route("/balance", methods=["GET"])
@jwt_required()
def get_balance():
    user_id = int(get_jwt_identity())
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE id = %s;", (user_id,))
    user = cur.fetchone()
    cur.close(); conn.close()
    return jsonify({"balance": float(user["balance"]) if user else 0})

@app.route("/payouts", methods=["GET"])
@jwt_required()
def list_payouts():
    user_id = int(get_jwt_identity())
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM payouts WHERE user_id = %s ORDER BY created_at DESC;", (user_id,))
    payouts = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(payouts)

@app.route("/profile/wallet", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute")
def update_wallet():
    user_id = int(get_jwt_identity())
    data = request.get_json()
    wallet_address = data.get("wallet_address")
    if not wallet_address or not Web3.is_address(wallet_address):
        return jsonify({"error": "Adresse invalide"}), 400
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET wallet_address = %s WHERE id = %s;", (wallet_address, user_id))
    conn.commit()
    cur.close(); conn.close()
    return jsonify({"success": True})

@app.route("/profile/facebook", methods=["POST"])
@jwt_required()
@limiter.limit("10 per minute")
def update_facebook_link():
    user_id = int(get_jwt_identity())
    data = request.get_json()
    link = data.get("facebook_link", "").strip()
    if not link or ("facebook.com" not in link and "fb.com" not in link):
        return jsonify({"error": "Lien Facebook invalide"}), 400
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET facebook_link = %s WHERE id = %s;", (link, user_id))
    conn.commit()
    cur.close(); conn.close()
    return jsonify({"success": True})

@app.route("/reseau/list", methods=["GET"])
@cache_result(expire_seconds=120, key_prefix="reseau")
def reseau_list():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT facebook_link FROM users WHERE facebook_link IS NOT NULL ORDER BY created_at DESC;")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([r["facebook_link"] for r in rows])

@app.route("/premium/status", methods=["GET"])
@jwt_required()
def premium_status():
    user_id = int(get_jwt_identity())
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_premium FROM users WHERE id = %s;", (user_id,))
    user = cur.fetchone()
    cur.close(); conn.close()
    return jsonify({"is_premium": bool(user["is_premium"]) if user else False})

@app.route("/referral/code", methods=["GET"])
@jwt_required()
def get_referral_code():
    user_id = int(get_jwt_identity())
    code = assign_referral_code(user_id)
    if not code:
        return jsonify({"error": "Impossible de générer le code"}), 500
    return jsonify({"referral_code": code, "referral_link": f"https://cashmoney.app/register?ref={code}"})

@app.route("/referral/stats", methods=["GET"])
@jwt_required()
def get_referral_stats():
    user_id = int(get_jwt_identity())
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""SELECT total_referrals, total_earned, premium_referrals FROM referral_stats WHERE user_id = %s;""", (user_id,))
    stats = cur.fetchone()
    cur.execute("""SELECT u.email, u.created_at, r.reward_given, r.premium_bonus_given, r.status FROM referrals r
        JOIN users u ON r.referred_id = u.id WHERE r.referrer_id = %s ORDER BY r.created_at DESC;""", (user_id,))
    referrals = cur.fetchall()
    cur.close(); conn.close()
    return jsonify({
        "stats": {"total_referrals": stats["total_referrals"] if stats else 0, "total_earned": float(stats["total_earned"]) if stats else 0, "premium_referrals": stats["premium_referrals"] if stats else 0},
        "referrals": [{"email": r["email"], "joined_at": r["created_at"].isoformat() if r["created_at"] else None, "reward_given": float(r["reward_given"]), "premium_bonus": float(r["premium_bonus_given"] or 0), "status": r["status"]} for r in referrals]
    })

@app.route("/referral/leaderboard", methods=["GET"])
@cache_result(expire_seconds=300, key_prefix="leaderboard")
def referral_leaderboard():
    limit = min(int(request.args.get("limit", 20)), 100)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""SELECT u.email, rs.total_referrals, rs.total_earned, rs.premium_referrals FROM referral_stats rs
        JOIN users u ON rs.user_id = u.id ORDER BY rs.total_earned DESC LIMIT %s;""", (limit,))
    leaders = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([{"rank": i + 1, "email": l["email"], "total_referrals": l["total_referrals"], "total_earned": float(l["total_earned"]), "premium_referrals": l["premium_referrals"]} for i, l in enumerate(leaders)])

# MLM Routes
@app.route("/mlm/status", methods=["GET"])
@jwt_required()
def mlm_status():
    user_id = int(get_jwt_identity())
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM mlm_binary_tree WHERE user_id = %s;", (user_id,))
    tree_data = cur.fetchone()
    if not tree_data:
        cur.close(); conn.close()
        return jsonify({"active": False})
    cur.execute("SELECT level, COUNT(*) as count, SUM(amount) as total FROM mlm_earnings WHERE user_id = %s GROUP BY level ORDER BY level;", (user_id,))
    earnings_by_level = cur.fetchall()
    cur.execute("""SELECT u.email, t.position, t.created_at FROM mlm_binary_tree t
        JOIN users u ON t.user_id = u.id WHERE t.parent_id = %s;""", (user_id,))
    direct_referrals = cur.fetchall()
    cur.execute("SELECT * FROM mlm_upgrades WHERE user_id = %s ORDER BY created_at DESC;", (user_id,))
    upgrades = cur.fetchall()
    cur.close(); conn.close()
    current_level = tree_data["current_level"]
    level_info = MLM_LEVELS[current_level - 1] if current_level <= 10 else MLM_LEVELS[-1]
    return jsonify({
        "active": True, "current_level": {"number": current_level, "name": level_info["name"], "members_limit": level_info["members"], "gain_per_member": level_info["gain_per_member"], "upgrade_cost": level_info["upgrade_cost"]},
        "total_earned": float(tree_data["total_earned"]), "total_members": tree_data["total_members"],
        "position": tree_data["position"], "earnings_by_level": [{"level": e["level"], "count": e["count"], "total": float(e["total"])} for e in earnings_by_level],
        "direct_referrals": [{"email": r["email"], "position": r["position"], "joined_at": r["created_at"].isoformat() if r["created_at"] else None} for r in direct_referrals],
        "upgrades": [{"old_level": u["old_level"], "new_level": u["new_level"], "cost": float(u["cost"]), "date": u["created_at"].isoformat() if u["created_at"] else None} for u in upgrades],
        "next_level": MLM_LEVELS[current_level] if current_level < 10 else None
    })

@app.route("/mlm/tree", methods=["GET"])
@jwt_required()
def mlm_tree():
    user_id = int(get_jwt_identity())
    depth = min(int(request.args.get("depth", 3)), 5)
    conn = get_connection()
    cur = conn.cursor()
    def build_tree(uid, current_depth):
        if current_depth > depth:
            return None
        cur.execute("SELECT user_id, left_child_id, right_child_id, current_level, total_earned, total_members FROM mlm_binary_tree WHERE user_id = %s;", (uid,))
        node = cur.fetchone()
        if not node:
            return None
        cur.execute("SELECT email FROM users WHERE id = %s;", (uid,))
        user = cur.fetchone()
        return {"user_id": uid, "email": user["email"] if user else "Unknown", "level": node["current_level"], "total_earned": float(node["total_earned"]), "total_members": node["total_members"], "left": build_tree(node["left_child_id"], current_depth + 1) if node["left_child_id"] else None, "right": build_tree(node["right_child_id"], current_depth + 1) if node["right_child_id"] else None}
    tree = build_tree(user_id, 1)
    cur.close(); conn.close()
    return jsonify(tree or {"error": "Pas encore dans le système MLM"})

@app.route("/mlm/levels", methods=["GET"])
def mlm_levels_info():
    return jsonify([{"level": l["level"], "name": l["name"], "members_required": l["members"], "gain_per_member": l["gain_per_member"], "upgrade_cost": l["upgrade_cost"], "total_potential": l["members"] * l["gain_per_member"]} for l in MLM_LEVELS])

@app.route("/mlm/leaderboard", methods=["GET"])
def mlm_leaderboard():
    limit = min(int(request.args.get("limit", 20)), 100)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""SELECT u.email, t.current_level, t.total_earned, t.total_members FROM mlm_binary_tree t
        JOIN users u ON t.user_id = u.id ORDER BY t.total_earned DESC LIMIT %s;""", (limit,))
    leaders = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([{"rank": i + 1, "email": l["email"], "level": l["current_level"], "level_name": MLM_LEVELS[l["current_level"] - 1]["name"] if l["current_level"] <= 10 else "Icon", "total_earned": float(l["total_earned"]), "total_members": l["total_members"]} for i, l in enumerate(leaders)])

@app.route("/mlm/upgrade", methods=["POST"])
@jwt_required()
def mlm_upgrade():
    user_id = int(get_jwt_identity())
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT current_level FROM mlm_binary_tree WHERE user_id = %s;", (user_id,))
        user_mlm = cur.fetchone()
        if not user_mlm:
            return jsonify({"error": "Pas dans le MLM"}), 400
        current_level = user_mlm["current_level"]
        if current_level >= 10:
            return jsonify({"error": "Niveau max atteint"}), 400
        next_level_config = MLM_LEVELS[current_level]
        upgrade_cost = next_level_config["upgrade_cost"]
        cur.execute("SELECT balance FROM users WHERE id = %s;", (user_id,))
        balance = cur.fetchone()["balance"]
        if float(balance) < upgrade_cost:
            return jsonify({"error": f"Solde insuffisant. Coût: {upgrade_cost} USDT"}), 400
        cur.execute("UPDATE users SET balance = balance - %s WHERE id = %s;", (upgrade_cost, user_id))
        cur.execute("UPDATE mlm_binary_tree SET current_level = %s WHERE user_id = %s;", (current_level + 1, user_id))
        cur.execute("INSERT INTO mlm_upgrades (user_id, old_level, new_level, cost) VALUES (%s, %s, %s, %s);",
            (user_id, current_level, current_level + 1, upgrade_cost))
        conn.commit()
        return jsonify({"success": True, "message": f"Niveau {current_level + 1} ({next_level_config['name']})", "new_level": current_level + 1, "cost": upgrade_cost})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); conn.close()

# Direct Referral Routes
@app.route("/direct-referral/stats", methods=["GET"])
@jwt_required()
def direct_referral_stats():
    user_id = int(get_jwt_identity())
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM direct_referral_stats WHERE user_id = %s;", (user_id,))
    stats = cur.fetchone()
    cur.execute("""SELECT u.email, u.created_at, d.direct_bonus, d.premium_bonus, d.status FROM direct_referrals d
        JOIN users u ON d.referred_id = u.id WHERE d.referrer_id = %s ORDER BY d.created_at DESC;""", (user_id,))
    referrals = cur.fetchall()
    cur.execute("SELECT * FROM binary_bonuses WHERE user_id = %s ORDER BY calculated_at DESC LIMIT 5;", (user_id,))
    binary_bonuses = cur.fetchall()
    cur.close(); conn.close()
    return jsonify({
        "stats": {"total_direct_referrals": stats["total_direct_referrals"] if stats else 0, "total_direct_bonus": float(stats["total_direct_bonus"]) if stats else 0, "total_premium_bonus": float(stats["total_premium_bonus"]) if stats else 0, "total_matching_bonus": float(stats["total_matching_bonus"]) if stats else 0, "total_binary_bonus": float(stats["total_binary_bonus"]) if stats else 0, "total_earned": float(stats["total_direct_bonus"] + stats["total_premium_bonus"] + stats["total_matching_bonus"] + stats["total_binary_bonus"]) if stats else 0},
        "referrals": [{"email": r["email"], "joined_at": r["created_at"].isoformat() if r["created_at"] else None, "direct_bonus": float(r["direct_bonus"]), "premium_bonus": float(r["premium_bonus"] or 0), "status": r["status"]} for r in referrals],
        "recent_binary_bonuses": [{"left_volume": float(b["left_volume"]), "right_volume": float(b["right_volume"]), "bonus": float(b["bonus_amount"]), "date": b["calculated_at"].isoformat() if b["calculated_at"] else None} for b in binary_bonuses]
    })

@app.route("/direct-referral/leaderboard", methods=["GET"])
def direct_referral_leaderboard():
    limit = min(int(request.args.get("limit", 20)), 100)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""SELECT u.email, s.total_direct_referrals, s.total_direct_bonus, s.total_premium_bonus,
        s.total_matching_bonus, s.total_binary_bonus FROM direct_referral_stats s
        JOIN users u ON s.user_id = u.id ORDER BY s.total_direct_bonus DESC LIMIT %s;""", (limit,))
    leaders = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([{"rank": i + 1, "email": l["email"], "direct_referrals": l["total_direct_referrals"], "direct_bonus": float(l["total_direct_bonus"]), "premium_bonus": float(l["total_premium_bonus"]), "matching_bonus": float(l["total_matching_bonus"]), "binary_bonus": float(l["total_binary_bonus"]), "total": float(l["total_direct_bonus"] + l["total_premium_bonus"] + l["total_matching_bonus"] + l["total_binary_bonus"])} for i, l in enumerate(leaders)])

# Admin Routes
@app.route("/admin/login", methods=["POST"])
@limiter.limit("5 per minute")
def admin_login():
    if not ADMIN_PASSWORD:
        return jsonify({"error": "ADMIN_PASSWORD non configuré"}), 500
    data = request.get_json()
    if data.get("password") != ADMIN_PASSWORD:
        logger.warning("Tentative admin échouée")
        return jsonify({"error": "Mot de passe incorrect"}), 401
    token = create_access_token(identity="admin", additional_claims={"is_admin": True})
    return jsonify({"access_token": token})

@app.route("/admin/reset-password", methods=["POST"])
@admin_required
@limiter.limit("10 per minute")
def admin_reset_password():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    new_password = data.get("password")
    if not email or not new_password or len(new_password) < 8:
        return jsonify({"error": "Email et mot de passe (min 8) requis"}), 400
    new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET password_hash = %s WHERE email = %s RETURNING id;", (new_hash, email))
    updated = cur.fetchone()
    conn.commit()
    cur.close(); conn.close()
    if not updated:
        return jsonify({"error": "Aucun compte trouvé"}), 404
    logger.info(f"Password reset: {email}")
    return jsonify({"success": True})

@app.route("/admin/maintenance-total", methods=["GET"])
@admin_required
def maintenance_total():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(maintenance_fee), 0) AS total FROM videos;")
    result = cur.fetchone()
    cur.close(); conn.close()
    return jsonify({"total_maintenance_collected": float(result["total"])})

@app.route("/debug/config", methods=["GET"])
def debug_config():
    return jsonify({
        "PAYOUT_WALLET_CONFIGUREE": bool(PAYOUT_WALLET_PRIVATE_KEY),
        "PREMIUM_RECEIVE_ADDRESS": PREMIUM_RECEIVE_ADDRESS,
        "DATABASE_URL_CONFIGUREE": bool(DATABASE_URL),
        "ADMIN_PASSWORD_CONFIGUREE": bool(ADMIN_PASSWORD),
        "JWT_SECRET_KEY_CONFIGUREE": bool(JWT_SECRET_KEY and JWT_SECRET_KEY != "change-moi-absolument"),
        "BSC_CONNECTED": w3.is_connected() if w3 else False,
        "USDT_DECIMALS": USDT_DECIMALS,
        "MLM_ENABLED": MLM_ENABLED,
        "REDIS_AVAILABLE": REDIS_AVAILABLE,
        "SENTRY_CONFIGURED": bool(SENTRY_DSN)
    })

# ========== HTML ROUTES (render_template) ==========

@app.route("/register", methods=["GET"])
def register_page():
    return render_template("inscription.html")

@app.route("/login", methods=["GET"])
def login_page():
    return render_template("login.html")

@app.route("/dashboard")
def dashboard_page():
    return render_template("dashboard.html")

@app.route("/retrait")
def retrait_page():
    return render_template("retrait.html")

@app.route("/premium", methods=["GET"])
def premium_page():
    return render_template("depot.html", premium_price=PREMIUM_PRICE_USDT, premium_address=PREMIUM_RECEIVE_ADDRESS)

@app.route("/reseau", methods=["GET"])
def reseau_page():
    return render_template("text.html")

@app.route("/admin/login", methods=["GET"])
def admin_login_page():
    return render_template("admin_login.html")

@app.route("/admin/videos", methods=["GET"])
@admin_required
def admin_videos_page():
    return render_template("admin.html")

@app.route("/parrainage", methods=["GET"])
@jwt_required(optional=True)
def parrainage_page():
    return render_template("parrainage.html")

@app.route("/mlm", methods=["GET"])
@jwt_required(optional=True)
def mlm_page():
    return render_template("mlm.html")

@app.route("/parrainage-direct", methods=["GET"])
@jwt_required(optional=True)
def direct_referral_page():
    return render_template("parrainage_direct.html")

# ========== DÉMARRAGE ==========

init_db()
if PREMIUM_RECEIVE_ADDRESS:
    scanner.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
'''

with open('/mnt/agents/output/app.py', 'w', encoding='utf-8') as f:
    f.write(app_py)

print(f"✅ app.py créé avec render_template() — {len(app_py)} caractères")
