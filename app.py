import os
import sqlite3
import hashlib
import secrets
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash
from payment_checker import start_payment_checker

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

DB_PATH = "cashmoney.db"
ADMIN_ID_WEB = os.environ.get("ADMIN_EMAIL", "admin@cashmoney.com")
WALLET_USDT = os.environ.get("WALLET_USDT", "0xE4901E78F8c92199bAfD93AD87C5a250C48199c2")
MEMBERSHIP_USDT = 0.35
GAINS_NIVEAU = [2000,1000,2000,1000,2000,1000,2000,1000,2000,1000,2000,1000]

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE,
        email TEXT UNIQUE,
        password_hash TEXT,
        nom TEXT,
        username TEXT,
        parrain_id INTEGER,
        actif INTEGER DEFAULT 0,
        gains_total REAL DEFAULT 0,
        wallet_sender TEXT,
        date_inscription TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS paiements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        montant REAL,
        statut TEXT DEFAULT 'en_attente',
        tx_hash TEXT UNIQUE,
        wallet_sender TEXT,
        date_confirmation TEXT,
        date TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS commissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        beneficiaire_id INTEGER,
        source_id INTEGER,
        niveau INTEGER,
        montant REAL,
        date TEXT)""")
    conn.commit()
    conn.close()
