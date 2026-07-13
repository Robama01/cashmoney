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
    @app.route("/dashboard")
@login_required
def dashboard():
    u=get_user_by_id(session["user_id"])
    conn=get_db()
    filleuls=conn.execute("SELECT COUNT(*) as n FROM users WHERE parrain_id=? AND actif=1",(u["id"],)).fetchone()["n"]
    commissions=conn.execute("SELECT * FROM commissions WHERE beneficiaire_id=? ORDER BY date DESC LIMIT 10",(u["id"],)).fetchall()
    pending=conn.execute("SELECT * FROM paiements WHERE user_id=? AND statut='en_attente'",(u["id"],)).fetchone()
    conn.close()
    lien=request.host_url+"inscription?ref="+str(u["id"])
    return render_template("dashboard.html",user=u,filleuls=filleuls,commissions=commissions,pending=pending,lien_parrainage=lien,wallet=WALLET_USDT,membership=MEMBERSHIP_USDT,gains_niveau=GAINS_NIVEAU)

@app.route("/payer",methods=["POST"])
@login_required
def payer():
    u=get_user_by_id(session["user_id"])
    if u["actif"]==1:
        flash("Compte déjà actif !","success")
        return redirect(url_for("dashboard"))
    ws=request.form.get("wallet_sender","").strip()
    conn=get_db()
    ex=conn.execute("SELECT id FROM paiements WHERE user_id=? AND statut='en_attente'",(u["id"],)).fetchone()
    if not ex:
        conn.execute("INSERT INTO paiements (user_id,montant,wallet_sender,date) VALUES (?,?,?,?)",(u["id"],MEMBERSHIP_USDT,ws or None,datetime.now().strftime("%Y-%m-%d %H:%M")))
        if ws: conn.execute("UPDATE users SET wallet_sender=? WHERE id=?",(ws,u["id"]))
        conn.commit()
    conn.close()
    flash("Paiement enregistré ! Vérification automatique en cours.","success")
    return redirect(url_for("dashboard"))

@app.route("/admin")
def admin_login_page():
    return render_template("admin_login.html")

@app.route("/admin/login",methods=["POST"])
def admin_login():
    email=request.form.get("email")
    password=request.form.get("password")
    if email==ADMIN_ID_WEB and password==os.environ.get("ADMIN_PASSWORD","cashmoney2024"):
        session["admin"]=True
        return redirect(url_for("admin_dashboard"))
    flash("Identifiants incorrects.","error")
    return redirect(url_for("admin_login_page"))

@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"): return redirect(url_for("admin_login_page"))
    conn=get_db()
    total=conn.execute("SELECT COUNT(*) as n FROM users").fetchone()["n"]
    actifs=conn.execute("SELECT COUNT(*) as n FROM users WHERE actif=1").fetchone()["n"]
    pending=conn.execute("SELECT p.*,u.nom,u.email FROM paiements p JOIN users u ON u.id=p.user_id WHERE p.statut='en_attente'").fetchall()
    gains=conn.execute("SELECT SUM(gains_total) as s FROM users").fetchone()["s"] or 0
    conn.close()
    return render_template("admin.html",total=total,actifs=actifs,pending_payments=pending,total_gains=int(gains))

@app.route("/admin/confirmer/<int:pid>")
def admin_confirmer(pid):
    if not session.get("admin"): return redirect(url_for("admin_login_page"))
    conn=get_db()
    p=conn.execute("SELECT * FROM paiements WHERE id=?",(pid,)).fetchone()
    if p:
        conn.execute("UPDATE paiements SET statut='confirme' WHERE id=?",(pid,))
        conn.execute("UPDATE users SET actif=1 WHERE id=?",(p["user_id"],))
        conn.commit()
        distribuer_commissions(p["user_id"])
    conn.close()
    flash("Confirmé !","success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/rejeter/<int:pid>")
def admin_rejeter(pid):
    if not session.get("admin"): return redirect(url_for("admin_login_page"))
    conn=get_db()
    conn.execute("UPDATE paiements SET statut='rejete' WHERE id=?",(pid,))
    conn.commit()
    conn.close()
    flash("Rejeté.","info")
    return redirect(url_for("admin_dashboard"))

init_db()
start_payment_checker(interval=60)

if __name__=="__main__":
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port,debug=False)
