"""Tests unitaires pour app.py (API Flask cashmoney).

app.py se connecte à PostgreSQL (``psycopg2``) au moment de l'import via
``init_db()`` et parle à la blockchain (``web3``). Les tests neutralisent ces
dépendances : ``psycopg2.connect`` est simulé avant l'import du module, puis
chaque endpoint reçoit une fausse connexion/curseur dont on contrôle les
résultats. Aucune vraie base ni aucun appel réseau n'est effectué.
"""

import os
from unittest.mock import MagicMock, patch

import bcrypt
import psycopg2
import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

# init_db() s'exécute à l'import : on simule la connexion au préalable.
_connect_patch = patch("psycopg2.connect", return_value=MagicMock())
_connect_patch.start()
import app as app_module  # noqa: E402

from flask_jwt_extended import create_access_token  # noqa: E402


def make_conn(fetchone=None, fetchall=None, execute_side_effect=None):
    """Construit une fausse connexion psycopg2 avec curseur contrôlé."""
    cur = MagicMock()
    if fetchone is not None:
        cur.fetchone.side_effect = list(fetchone)
    if fetchall is not None:
        cur.fetchall.side_effect = list(fetchall)
    if execute_side_effect is not None:
        cur.execute.side_effect = execute_side_effect
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def auth_header():
    with app_module.app.app_context():
        token = create_access_token(identity="1")
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# send_usdt_bep20
# --------------------------------------------------------------------------- #

def test_send_usdt_bep20_missing_private_key(monkeypatch):
    monkeypatch.setattr(app_module, "PAYOUT_WALLET_PRIVATE_KEY", None)
    ok, msg = app_module.send_usdt_bep20("0xabc", 10)
    assert ok is False
    assert "PAYOUT_WALLET_PRIVATE_KEY" in msg


def test_send_usdt_bep20_handles_exception(monkeypatch):
    monkeypatch.setattr(app_module, "PAYOUT_WALLET_PRIVATE_KEY", "0xdeadbeef")
    # w3.eth.account.from_key lève -> l'erreur est capturée et renvoyée.
    fake_w3 = MagicMock()
    fake_w3.eth.account.from_key.side_effect = Exception("clé invalide")
    monkeypatch.setattr(app_module, "w3", fake_w3)
    ok, msg = app_module.send_usdt_bep20("0xabc", 10)
    assert ok is False
    assert "clé invalide" in msg


# --------------------------------------------------------------------------- #
# index / test_page
# --------------------------------------------------------------------------- #

def test_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_test_page(client):
    resp = client.get("/test.html")
    assert resp.status_code == 200
    assert "Test API cashmoney" in resp.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# /register
# --------------------------------------------------------------------------- #

def test_register_success(client):
    conn, _ = make_conn(fetchone=[{"id": 1, "email": "a@b.com"}])
    with patch.object(app_module, "get_connection", return_value=conn):
        resp = client.post("/register", json={"email": "a@b.com", "password": "pw"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["user"]["email"] == "a@b.com"
    assert "access_token" in body


def test_register_missing_fields(client):
    resp = client.post("/register", json={"email": "a@b.com"})
    assert resp.status_code == 400


def test_register_invalid_wallet(client):
    resp = client.post(
        "/register",
        json={"email": "a@b.com", "password": "pw", "wallet_address": "not-an-address"},
    )
    assert resp.status_code == 400


def test_register_duplicate_email(client):
    conn, _ = make_conn(execute_side_effect=psycopg2.errors.UniqueViolation())
    with patch.object(app_module, "get_connection", return_value=conn):
        resp = client.post("/register", json={"email": "a@b.com", "password": "pw"})
    assert resp.status_code == 409


# --------------------------------------------------------------------------- #
# /login
# --------------------------------------------------------------------------- #

def test_login_success(client):
    pw_hash = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode()
    user = {"id": 1, "password_hash": pw_hash, "balance": 42}
    conn, _ = make_conn(fetchone=[user])
    with patch.object(app_module, "get_connection", return_value=conn):
        resp = client.post("/login", json={"email": "a@b.com", "password": "secret"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert "access_token" in body
    assert body["balance"] == 42.0


def test_login_wrong_password(client):
    pw_hash = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode()
    user = {"id": 1, "password_hash": pw_hash, "balance": 0}
    conn, _ = make_conn(fetchone=[user])
    with patch.object(app_module, "get_connection", return_value=conn):
        resp = client.post("/login", json={"email": "a@b.com", "password": "wrong"})
    assert resp.status_code == 401


def test_login_unknown_user(client):
    conn, _ = make_conn(fetchone=[None])
    with patch.object(app_module, "get_connection", return_value=conn):
        resp = client.post("/login", json={"email": "x@y.com", "password": "pw"})
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# /videos
# --------------------------------------------------------------------------- #

def test_list_videos(client):
    videos = [{"id": 1, "title": "v1"}, {"id": 2, "title": "v2"}]
    conn, _ = make_conn(fetchall=[videos])
    with patch.object(app_module, "get_connection", return_value=conn):
        resp = client.get("/videos")
    assert resp.status_code == 200
    assert resp.get_json() == videos


def test_add_video(client):
    video = {"id": 1, "title": "v1", "reward_amount": 5}
    conn, _ = make_conn(fetchone=[video])
    with patch.object(app_module, "get_connection", return_value=conn):
        resp = client.post("/videos", json={"youtube_id": "abc", "title": "v1",
                                            "reward_amount": 5})
    assert resp.status_code == 201
    assert resp.get_json() == video


# --------------------------------------------------------------------------- #
# /watch
# --------------------------------------------------------------------------- #

def test_watch_requires_auth(client):
    resp = client.post("/watch", json={"video_id": 1, "watched_seconds": 60})
    assert resp.status_code == 401


def test_watch_video_not_found(client, auth_header):
    conn, _ = make_conn(fetchone=[None])
    with patch.object(app_module, "get_connection", return_value=conn):
        resp = client.post("/watch", json={"video_id": 99, "watched_seconds": 60},
                           headers=auth_header)
    assert resp.status_code == 404


def test_watch_not_enough_seconds(client, auth_header):
    video = {"id": 1, "min_watch_seconds": 30, "reward_amount": 5}
    conn, _ = make_conn(fetchone=[video])
    with patch.object(app_module, "get_connection", return_value=conn):
        resp = client.post("/watch", json={"video_id": 1, "watched_seconds": 5},
                           headers=auth_header)
    assert resp.status_code == 400


def test_watch_success_no_payout(client, auth_header):
    video = {"id": 1, "min_watch_seconds": 30, "reward_amount": 5}
    conn, _ = make_conn(fetchone=[video, {"balance": 50}])
    with patch.object(app_module, "get_connection", return_value=conn):
        resp = client.post("/watch", json={"video_id": 1, "watched_seconds": 60},
                           headers=auth_header)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["reward_given"] == 5.0
    assert body["new_balance"] == 50.0
    assert body["payout_triggered"] is False


def test_watch_triggers_payout_over_threshold(client, auth_header, monkeypatch):
    monkeypatch.setattr(app_module, "WITHDRAWAL_THRESHOLD", 100)
    video = {"id": 1, "min_watch_seconds": 30, "reward_amount": 5}
    # fetchone: video, nouveau solde (>= seuil), puis wallet (dans trigger_payout)
    conn, _ = make_conn(fetchone=[video, {"balance": 150},
                                  {"wallet_address": "0xWALLET"}])
    with patch.object(app_module, "get_connection", return_value=conn), \
         patch.object(app_module, "send_usdt_bep20", return_value=(True, "0xtx")) as send:
        resp = client.post("/watch", json={"video_id": 1, "watched_seconds": 60},
                           headers=auth_header)
    assert resp.status_code == 200
    assert resp.get_json()["payout_triggered"] is True
    send.assert_called_once()


def test_watch_already_rewarded(client, auth_header):
    video = {"id": 1, "min_watch_seconds": 30, "reward_amount": 5}
    conn, _ = make_conn(
        fetchone=[video],
        execute_side_effect=[None, psycopg2.errors.UniqueViolation()],
    )
    with patch.object(app_module, "get_connection", return_value=conn):
        resp = client.post("/watch", json={"video_id": 1, "watched_seconds": 60},
                           headers=auth_header)
    assert resp.status_code == 409


# --------------------------------------------------------------------------- #
# trigger_payout
# --------------------------------------------------------------------------- #

def test_trigger_payout_no_wallet():
    cur = MagicMock()
    cur.fetchone.return_value = {"wallet_address": None}
    app_module.trigger_payout(cur, user_id=1, amount=100)
    # Un payout "failed" est enregistré, aucun envoi tenté.
    inserted = " ".join(str(c) for c in cur.execute.call_args_list)
    assert "failed" in inserted


def test_trigger_payout_success():
    cur = MagicMock()
    cur.fetchone.return_value = {"wallet_address": "0xWALLET"}
    with patch.object(app_module, "send_usdt_bep20", return_value=(True, "0xtx")):
        app_module.trigger_payout(cur, user_id=1, amount=100)
    calls = " ".join(str(c) for c in cur.execute.call_args_list)
    assert "'sent'" in calls
    assert "balance = 0" in calls


def test_trigger_payout_failure():
    cur = MagicMock()
    cur.fetchone.return_value = {"wallet_address": "0xWALLET"}
    with patch.object(app_module, "send_usdt_bep20", return_value=(False, "boom")):
        app_module.trigger_payout(cur, user_id=1, amount=100)
    calls = " ".join(str(c) for c in cur.execute.call_args_list)
    assert "failed" in calls


# --------------------------------------------------------------------------- #
# /balance et /payouts
# --------------------------------------------------------------------------- #

def test_get_balance(client, auth_header):
    conn, _ = make_conn(fetchone=[{"balance": 77}])
    with patch.object(app_module, "get_connection", return_value=conn):
        resp = client.get("/balance", headers=auth_header)
    assert resp.status_code == 200
    assert resp.get_json()["balance"] == 77.0


def test_get_balance_requires_auth(client):
    assert client.get("/balance").status_code == 401


def test_list_payouts(client, auth_header):
    payouts = [{"id": 1, "amount": 100, "status": "sent"}]
    conn, _ = make_conn(fetchall=[payouts])
    with patch.object(app_module, "get_connection", return_value=conn):
        resp = client.get("/payouts", headers=auth_header)
    assert resp.status_code == 200
    assert resp.get_json() == payouts
