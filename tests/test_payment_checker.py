"""Tests unitaires pour payment_checker.py.

Le module utilise une base SQLite (``DB_PATH``) et l'API BscScan (``requests``).
Les tests isolent complètement ces dépendances : la base est remplacée par un
fichier SQLite temporaire et les appels réseau sont simulés (``unittest.mock``).
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

import payment_checker as pc


SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parrain_id INTEGER,
    actif INTEGER DEFAULT 0,
    gains_total REAL DEFAULT 0,
    wallet_sender TEXT
);
CREATE TABLE paiements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    montant REAL,
    statut TEXT,
    tx_hash TEXT,
    wallet_sender TEXT,
    date TEXT,
    date_confirmation TEXT
);
CREATE TABLE commissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    beneficiaire_id INTEGER,
    source_id INTEGER,
    niveau INTEGER,
    montant REAL,
    date TEXT
);
"""


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Crée une base SQLite temporaire pointée par payment_checker.DB_PATH."""
    path = tmp_path / "cashmoney_test.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setattr(pc, "DB_PATH", str(path))
    return str(path)


def add_user(db_path, user_id, parrain_id=None, actif=0, wallet_sender=None):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO users (id, parrain_id, actif, gains_total, wallet_sender) "
        "VALUES (?, ?, ?, 0, ?)",
        (user_id, parrain_id, actif, wallet_sender),
    )
    conn.commit()
    conn.close()


def add_paiement(db_path, user_id, statut="en_attente", tx_hash=None,
                 wallet_sender=None, montant=0.35):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO paiements (user_id, montant, statut, tx_hash, wallet_sender, date) "
        "VALUES (?, ?, ?, ?, ?, '2024-01-01 00:00')",
        (user_id, montant, statut, tx_hash, wallet_sender),
    )
    conn.commit()
    conn.close()


def fetch_user(db_path, user_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return row


# --------------------------------------------------------------------------- #
# get_recent_usdt_transactions
# --------------------------------------------------------------------------- #

def test_get_recent_usdt_transactions_success():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"status": "1", "result": [{"hash": "0xabc"}]}
    with patch.object(pc.requests, "get", return_value=fake_resp) as mock_get:
        result = pc.get_recent_usdt_transactions()
    assert result == [{"hash": "0xabc"}]
    mock_get.assert_called_once()
    # Les bons paramètres BscScan sont transmis.
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["contractaddress"] == pc.USDT_CONTRACT
    assert kwargs["params"]["address"] == pc.WALLET_USDT


def test_get_recent_usdt_transactions_api_error_status():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"status": "0", "result": "NOTOK"}
    with patch.object(pc.requests, "get", return_value=fake_resp):
        assert pc.get_recent_usdt_transactions() == []


def test_get_recent_usdt_transactions_network_exception():
    with patch.object(pc.requests, "get", side_effect=Exception("timeout")):
        assert pc.get_recent_usdt_transactions() == []


# --------------------------------------------------------------------------- #
# is_tx_already_processed
# --------------------------------------------------------------------------- #

def test_is_tx_already_processed(db_path):
    assert pc.is_tx_already_processed("0xdead") is False
    add_paiement(db_path, user_id=1, tx_hash="0xdead")
    assert pc.is_tx_already_processed("0xdead") is True


# --------------------------------------------------------------------------- #
# get_pending_payment_by_sender
# --------------------------------------------------------------------------- #

def test_get_pending_payment_by_sender_case_insensitive(db_path):
    add_user(db_path, 1)
    add_paiement(db_path, user_id=1, statut="en_attente", wallet_sender="0xAbC123")
    row = pc.get_pending_payment_by_sender("0xabc123")
    assert row is not None
    assert row["user_id"] == 1


def test_get_pending_payment_by_sender_none_when_no_match(db_path):
    add_user(db_path, 1)
    add_paiement(db_path, user_id=1, statut="confirme", wallet_sender="0xAbC123")
    assert pc.get_pending_payment_by_sender("0xabc123") is None


# --------------------------------------------------------------------------- #
# get_pending_payments_unmatched
# --------------------------------------------------------------------------- #

def test_get_pending_payments_unmatched(db_path):
    add_user(db_path, 1)
    add_user(db_path, 2)
    add_user(db_path, 3)
    add_paiement(db_path, user_id=1, wallet_sender=None)      # non associé
    add_paiement(db_path, user_id=2, wallet_sender="")        # non associé
    add_paiement(db_path, user_id=3, wallet_sender="0xabc")   # associé -> exclu
    rows = pc.get_pending_payments_unmatched()
    ids = {r["user_id"] for r in rows}
    assert ids == {1, 2}


# --------------------------------------------------------------------------- #
# distribuer_commissions
# --------------------------------------------------------------------------- #

def test_distribuer_commissions_pays_active_uplines(db_path):
    # Chaîne : 3 -> 2 -> 1 (1 est le parrain de 2, 2 le parrain de 3).
    add_user(db_path, 1, parrain_id=None, actif=1)
    add_user(db_path, 2, parrain_id=1, actif=1)
    add_user(db_path, 3, parrain_id=2, actif=1)

    pc.distribuer_commissions(3)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    commissions = conn.execute(
        "SELECT * FROM commissions ORDER BY niveau"
    ).fetchall()
    conn.close()

    # Deux parrains en amont : niveau 1 -> user 2, niveau 2 -> user 1.
    assert [c["beneficiaire_id"] for c in commissions] == [2, 1]
    assert commissions[0]["montant"] == pc.GAINS_NIVEAU[0]
    assert commissions[1]["montant"] == pc.GAINS_NIVEAU[1]
    assert fetch_user(db_path, 2)["gains_total"] == pc.GAINS_NIVEAU[0]
    assert fetch_user(db_path, 1)["gains_total"] == pc.GAINS_NIVEAU[1]


def test_distribuer_commissions_skips_inactive_upline(db_path):
    # Le parrain direct (2) est inactif -> pas de commission, mais la chaîne
    # continue via parrain_id jusqu'au parrain actif (1).
    add_user(db_path, 1, parrain_id=None, actif=1)
    add_user(db_path, 2, parrain_id=1, actif=0)
    add_user(db_path, 3, parrain_id=2, actif=1)

    pc.distribuer_commissions(3)

    beneficiaires = [c["beneficiaire_id"] for c in _all_commissions(db_path)]
    assert beneficiaires == [1]
    assert fetch_user(db_path, 2)["gains_total"] == 0


def test_distribuer_commissions_no_parrain(db_path):
    add_user(db_path, 1, parrain_id=None, actif=1)
    pc.distribuer_commissions(1)
    assert _all_commissions(db_path) == []


def _all_commissions(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM commissions ORDER BY niveau").fetchall()
    conn.close()
    return rows


# --------------------------------------------------------------------------- #
# activer_compte
# --------------------------------------------------------------------------- #

def test_activer_compte(db_path):
    add_user(db_path, 1, parrain_id=None, actif=0)
    add_paiement(db_path, user_id=1, statut="en_attente")

    pc.activer_compte(1, "0xhash", 0.35)

    assert fetch_user(db_path, 1)["actif"] == 1
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    paie = conn.execute("SELECT * FROM paiements WHERE user_id=1").fetchone()
    conn.close()
    assert paie["statut"] == "confirme"
    assert paie["tx_hash"] == "0xhash"


# --------------------------------------------------------------------------- #
# check_payments
# --------------------------------------------------------------------------- #

def _tx(to, value, tx_hash, sender):
    return {"to": to, "from": sender, "hash": tx_hash,
            "value": str(int(value * (10 ** pc.USDT_DECIMALS)))}


def test_check_payments_skips_wrong_recipient(db_path):
    tx = _tx(to="0xOTHER", value=1.0, tx_hash="0x1", sender="0xsender")
    with patch.object(pc, "get_recent_usdt_transactions", return_value=[tx]), \
         patch.object(pc, "activer_compte") as mock_activate:
        pc.check_payments()
    mock_activate.assert_not_called()


def test_check_payments_skips_small_amount(db_path):
    tx = _tx(to=pc.WALLET_USDT, value=0.10, tx_hash="0x2", sender="0xsender")
    with patch.object(pc, "get_recent_usdt_transactions", return_value=[tx]), \
         patch.object(pc, "activer_compte") as mock_activate:
        pc.check_payments()
    mock_activate.assert_not_called()


def test_check_payments_skips_already_processed(db_path):
    add_paiement(db_path, user_id=1, tx_hash="0x3")
    tx = _tx(to=pc.WALLET_USDT, value=0.35, tx_hash="0x3", sender="0xsender")
    with patch.object(pc, "get_recent_usdt_transactions", return_value=[tx]), \
         patch.object(pc, "activer_compte") as mock_activate:
        pc.check_payments()
    mock_activate.assert_not_called()


def test_check_payments_matches_pending_by_sender(db_path):
    add_user(db_path, 1)
    add_paiement(db_path, user_id=1, statut="en_attente", wallet_sender="0xSENDER")
    tx = _tx(to=pc.WALLET_USDT, value=0.35, tx_hash="0x4", sender="0xsender")
    with patch.object(pc, "get_recent_usdt_transactions", return_value=[tx]), \
         patch.object(pc, "activer_compte") as mock_activate:
        pc.check_payments()
    mock_activate.assert_called_once()
    assert mock_activate.call_args[0][0] == 1


def test_check_payments_assigns_oldest_unmatched(db_path):
    add_user(db_path, 5)
    add_user(db_path, 6)
    add_paiement(db_path, user_id=5, statut="en_attente", wallet_sender=None)
    add_paiement(db_path, user_id=6, statut="en_attente", wallet_sender=None)
    tx = _tx(to=pc.WALLET_USDT, value=0.35, tx_hash="0x5", sender="0xnew")
    with patch.object(pc, "get_recent_usdt_transactions", return_value=[tx]), \
         patch.object(pc, "activer_compte") as mock_activate:
        pc.check_payments()
    mock_activate.assert_called_once()
    activated_user = mock_activate.call_args[0][0]
    # wallet_sender de l'utilisateur activé est renseigné avec l'expéditeur.
    assert fetch_user(db_path, activated_user)["wallet_sender"] == "0xnew"


def test_check_payments_records_unassociated(db_path):
    # Aucun paiement en attente -> la transaction est enregistrée "non_associe".
    tx = _tx(to=pc.WALLET_USDT, value=0.50, tx_hash="0x6", sender="0xnobody")
    with patch.object(pc, "get_recent_usdt_transactions", return_value=[tx]):
        pc.check_payments()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM paiements WHERE tx_hash='0x6'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["statut"] == "non_associe"


# --------------------------------------------------------------------------- #
# start_payment_checker
# --------------------------------------------------------------------------- #

def test_start_payment_checker_starts_daemon_thread():
    with patch.object(pc.threading, "Thread") as mock_thread:
        instance = mock_thread.return_value
        pc.start_payment_checker(interval=5)
    mock_thread.assert_called_once()
    assert mock_thread.call_args.kwargs["daemon"] is True
    instance.start.assert_called_once()
