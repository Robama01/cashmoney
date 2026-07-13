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

def hash_password(p):
    return hashlib.sha256(p.encode()).hexdigest()

def get_user_by_email(email):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    return u

def get_user_by_id(uid):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return u

def distribuer_commissions(uid):
    conn = get_db()
    chain = []
    current = uid
    for _ in range(12):
        r = conn.execute("SELECT parrain_id FROM users WHERE id=?", (current,)).fetchone()
        if not r or not r["parrain_id"]: break
        p = conn.execute("SELECT id,actif FROM users WHERE id=?", (r["parrain_id"],)).fetchone()
        if p and p["actif"]==1: chain.append(p["id"])
        current = r["parrain_id"]
    for i,pid in enumerate(chain):
        if i>=len(GAINS_NIVEAU): break
        m = GAINS_NIVEAU[i]
        conn.execute("INSERT INTO commissions (beneficiaire_id,source_id,niveau,montant,date) VALUES (?,?,?,?,?)",(pid,uid,i+1,m,datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.execute("UPDATE users SET gains_total=gains_total+? WHERE id=?",(m,pid))
    conn.commit()
    conn.close()

def login_required(f):
    @wraps(f)
    def decorated(*args,**kwargs):
        if "user_id" not in session: return redirect(url_for("login"))
        return f(*args,**kwargs)
    return decorated

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/inscription",methods=["GET","POST"])
def inscription():
    ref = request.args.get("ref")
    if request.method=="POST":
        nom=request.form.get("nom","").strip()
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        parrain_code=request.form.get("parrain_code","").strip()
        if not nom or not email or not password:
            flash("Tous les champs sont obligatoires.","error")
            return render_template("inscription.html",ref=ref)
        if len(password)<6:
            flash("Mot de passe trop court.","error")
            return render_template("inscription.html",ref=ref)
        if get_user_by_email(email):
            flash("Email déjà utilisé.","error")
            return render_template("inscription.html",ref=ref)
        parrain_id=None
        if parrain_code:
            conn=get_db()
            p=conn.execute("SELECT id FROM users WHERE id=?",(parrain_code,)).fetchone()
            conn.close()
            if p: parrain_id=p["id"]
        conn=get_db()
        conn.execute("INSERT INTO users (nom,email,password_hash,parrain_id,date_inscription) VALUES (?,?,?,?,?)",(nom,email,hash_password(password),parrain_id,datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        conn.close()
        flash("Compte créé ! Connectez-vous.","success")
        return redirect(url_for("login"))
    return render_template("inscription.html",ref=ref)

@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        u=get_user_by_email(email)
        if u and u["password_hash"]==hash_password(password):
            session["user_id"]=u["id"]
            return redirect(url_for("dashboard"))
        flash("Email ou mot de passe incorrect.","error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))
