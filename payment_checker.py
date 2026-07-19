import os
import sqlite3
import requests
import time
import threading
from datetime import datetime

# Configuration
BSCSCAN_API_KEY = os.environ.get("BSCSCAN_API_KEY")
WALLET_USDT = os.environ.get("WALLET_USDT")
USDT_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"  # USDT BEP20
MEMBERSHIP_USDT = 0.35
USDT_DECIMALS = 18

DB_PATH = "cashmoney.db"
GAINS_NIVEAU = [2000, 1000, 2000, 1000, 2000, 1000, 2000, 1000, 2000, 1000, 2000, 1000]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_recent_usdt_transactions():
    if not BSCSCAN_API_KEY or not WALLET_USDT:
        print("[BscScan] BSCSCAN_API_KEY et WALLET_USDT doivent etre configures via l'environnement")
        return []
    url = "https://api.bscscan.com/api"
    params = {
        "module": "account",
        "action": "tokentx",
        "contractaddress": USDT_CONTRACT,
        "address": WALLET_USDT,
        "sort": "desc",
        "apikey": BSCSCAN_API_KEY,
        "offset": 50,
        "page": 1
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if data["status"] == "1":
            return data["result"]
        return []
    except Exception as e:
        print(f"[BscScan] Erreur: {e}")
        return []


def is_tx_already_processed(tx_hash):
    conn = get_db()
    row = conn.execute("SELECT id FROM paiements WHERE tx_hash=?", (tx_hash,)).fetchone()
    conn.close()
    return row is not None


def get_pending_payment_by_sender(sender_address):
    conn = get_db()
    row = conn.execute("""
        SELECT p.*, u.id as uid FROM paiements p
        JOIN users u ON u.id = p.user_id
        WHERE p.statut = 'en_attente'
        AND LOWER(p.wallet_sender) = LOWER(?)
    """, (sender_address,)).fetchone()
    conn.close()
    return row


def get_pending_payments_unmatched():
    conn = get_db()
    rows = conn.execute("""
        SELECT p.*, u.id as uid FROM paiements p
        JOIN users u ON u.id = p.user_id
        WHERE p.statut = 'en_attente'
        AND (p.wallet_sender IS NULL OR p.wallet_sender = '')
    """).fetchall()
    conn.close()
    return rows


def distribuer_commissions(nouveau_user_id):
    conn = get_db()
    current_id = nouveau_user_id
    chain = []
    for _ in range(12):
        row = conn.execute("SELECT parrain_id FROM users WHERE id=?", (current_id,)).fetchone()
        if not row or not row["parrain_id"]:
            break
        parrain = conn.execute("SELECT id, actif FROM users WHERE id=?", (row["parrain_id"],)).fetchone()
        if parrain and parrain["actif"] == 1:
            chain.append(parrain["id"])
        current_id = row["parrain_id"]

    for i, parrain_id in enumerate(chain):
        if i >= len(GAINS_NIVEAU):
            break
        montant = GAINS_NIVEAU[i]
        conn.execute("""
            INSERT INTO commissions (beneficiaire_id, source_id, niveau, montant, date)
            VALUES (?, ?, ?, ?, ?)
        """, (parrain_id, nouveau_user_id, i + 1, montant, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.execute("UPDATE users SET gains_total = gains_total + ? WHERE id=?", (montant, parrain_id))
    conn.commit()
    conn.close()


def activer_compte(user_id, tx_hash, montant_usdt):
    conn = get_db()
    conn.execute("UPDATE users SET actif=1 WHERE id=?", (user_id,))
    conn.execute("""
        UPDATE paiements SET statut='confirme', tx_hash=?, date_confirmation=?
        WHERE user_id=? AND statut='en_attente'
    """, (tx_hash, datetime.now().strftime("%Y-%m-%d %H:%M"), user_id))
    conn.commit()
    conn.close()
    distribuer_commissions(user_id)
    print(f"[OK] Compte {user_id} active | TX: {tx_hash} | {montant_usdt} USDT")


def check_payments():
    print(f"[Checker] Verification... {datetime.now().strftime('%H:%M:%S')}")
    txs = get_recent_usdt_transactions()

    for tx in txs:
        if tx["to"].lower() != WALLET_USDT.lower():
            continue

        tx_hash = tx["hash"]
        sender = tx["from"]
        montant_usdt = int(tx["value"]) / (10 ** USDT_DECIMALS)

        if montant_usdt < 0.34:
            continue

        if is_tx_already_processed(tx_hash):
            continue

        print(f"[Paiement] {montant_usdt} USDT de {sender}")

        pending = get_pending_payment_by_sender(sender)
        if pending:
            activer_compte(pending["user_id"], tx_hash, montant_usdt)
            continue

        unmatched = get_pending_payments_unmatched()
        if unmatched:
            oldest = unmatched[-1]
            activer_compte(oldest["user_id"], tx_hash, montant_usdt)
            conn = get_db()
            conn.execute("UPDATE users SET wallet_sender=? WHERE id=?", (sender, oldest["user_id"]))
            conn.commit()
            conn.close()
        else:
            conn = get_db()
            conn.execute("""
                INSERT OR IGNORE INTO paiements (user_id, montant, statut, tx_hash, date)
                VALUES (0, ?, 'non_associe', ?, ?)
            """, (montant_usdt, tx_hash, datetime.now().strftime("%Y-%m-%d %H:%M")))
            conn.commit()
            conn.close()
            print(f"[WARN] Transaction non associee: {tx_hash}")


def start_payment_checker(interval=60):
    def loop():
        while True:
            try:
                check_payments()
            except Exception as e:
                print(f"[Erreur checker] {e}")
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print(f"[Checker] Demarre (interval: {interval}s)")
