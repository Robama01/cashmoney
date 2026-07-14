import os,hashlib,secrets
from datetime import datetime
from functools import wraps
from flask import Flask,render_template,request,redirect,url_for,session,flash
import psycopg2
from psycopg2.extras import RealDictCursor
from payment_checker import start_payment_checker
app=Flask(__name__)
app.secret_key=os.environ.get("SECRET_KEY",secrets.token_hex(32))
DATABASE_URL=os.environ.get("DATABASE_URL")
ADMIN_EMAIL=os.environ.get("ADMIN_EMAIL","admin@cashmoney.com")
WALLET_USDT=os.environ.get("WALLET_USDT","0xE4901E78F8c92199bAfD93AD87C5a250C48199c2")
MEMBERSHIP_USDT=0.35
GAINS=[2000,1000,2000,1000,2000,1000,2000,1000,2000,1000,2000,1000]
def get_db():
 conn=psycopg2.connect(DATABASE_URL,cursor_factory=RealDictCursor)
 return conn
def init_db():
 conn=get_db()
 c=conn.cursor()
 c.execute("""CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY,email TEXT UNIQUE,password_hash TEXT,nom TEXT,parrain_id INTEGER,actif INTEGER DEFAULT 0,gains_total REAL DEFAULT 0,wallet_sender TEXT,date_inscription TEXT)""")
 c.execute("""CREATE TABLE IF NOT EXISTS paiements (id SERIAL PRIMARY KEY,user_id INTEGER,montant REAL,statut TEXT DEFAULT 'en_attente',tx_hash TEXT UNIQUE,wallet_sender TEXT,date_confirmation TEXT,date TEXT)""")
 c.execute("""CREATE TABLE IF NOT EXISTS commissions (id SERIAL PRIMARY KEY,beneficiaire_id INTEGER,source_id INTEGER,niveau INTEGER,montant REAL,date TEXT)""")
 conn.commit()
 conn.close()
def hp(p):
 return hashlib.sha256(p.encode()).hexdigest()
def gue(email):
 conn=get_db()
 c=conn.cursor()
 c.execute("SELECT * FROM users WHERE email=%s",(email,))
 u=c.fetchone()
 conn.close()
 return u
def gui(uid):
 conn=get_db()
 c=conn.cursor()
 c.execute("SELECT * FROM users WHERE id=%s",(uid,))
 u=c.fetchone()
 conn.close()
 return u
def dist(uid):
 conn=get_db()
 c=conn.cursor()
 chain=[]
 cur=uid
 for _ in range(12):
  c.execute("SELECT parrain_id FROM users WHERE id=%s",(cur,))
  r=c.fetchone()
  if not r or not r["parrain_id"]:break
  c.execute("SELECT id,actif FROM users WHERE id=%s",(r["parrain_id"],))
  p=c.fetchone()
  if p and p["actif"]==1:chain.append(p["id"])
  cur=r["parrain_id"]
 for i,pid in enumerate(chain):
  if i>=len(GAINS):break
  m=GAINS[i]
  c.execute("INSERT INTO commissions (beneficiaire_id,source_id,niveau,montant,date) VALUES (%s,%s,%s,%s,%s)",(pid,uid,i+1,m,datetime.now().strftime("%Y-%m-%d %H:%M")))
  c.execute("UPDATE users SET gains_total=gains_total+%s WHERE id=%s",(m,pid))
 conn.commit()
 conn.close()
def lr(f):
 @wraps(f)
 def d(*a,**k):
  if "user_id" not in session:return redirect(url_for("login"))
  return f(*a,**k)
 return d
@app.route("/")
def index():
 return render_template("index.html")
@app.route("/inscription",methods=["GET","POST"])
def inscription():
 ref=request.args.get("ref")
 if request.method=="POST":
  nom=request.form.get("nom","").strip()
  email=request.form.get("email","").strip().lower()
  pw=request.form.get("password","")
  pc=request.form.get("parrain_code","").strip()
  if not nom or not email or not pw:
   flash("Tous les champs sont obligatoires.","error")
   return render_template("inscription.html",ref=ref)
  if len(pw)<6:
   flash("Mot de passe trop court.","error")
   return render_template("inscription.html",ref=ref)
  if gue(email):
   flash("Email déjà utilisé.","error")
   return render_template("inscription.html",ref=ref)
  pid=None
  if pc:
   conn=get_db()
   cur=conn.cursor()
   cur.execute("SELECT id FROM users WHERE id=%s",(pc,))
   p=cur.fetchone()
   conn.close()
   if p:pid=p["id"]
  conn=get_db()
  cur=conn.cursor()
  cur.execute("INSERT INTO users (nom,email,password_hash,parrain_id,date_inscription) VALUES (%s,%s,%s,%s,%s)",(nom,email,hp(pw),pid,datetime.now().strftime("%Y-%m-%d %H:%M")))
  conn.commit()
  conn.close()
  flash("Compte créé ! Connectez-vous.","success")
  return redirect(url_for("login"))
 return render_template("inscription.html",ref=ref)
@app.route("/login",methods=["GET","POST"])
def login():
 if request.method=="POST":
  email=request.form.get("email","").strip().lower()
  pw=request.form.get("password","")
  u=gue(email)
  if u and u["password_hash"]==hp(pw):
   session["user_id"]=u["id"]
   return redirect(url_for("dashboard"))
  flash("Email ou mot de passe incorrect.","error")
 return render_template("login.html")
@app.route("/logout")
def logout():
 session.clear()
 return redirect(url_for("index"))
@app.route("/dashboard")
@lr
def dashboard():
 u=gui(session["user_id"])
 conn=get_db()
 c=conn.cursor()
 c.execute("SELECT COUNT(*) as n FROM users WHERE parrain_id=%s AND actif=1",(u["id"],))
 f=c.fetchone()["n"]
 c.execute("SELECT * FROM commissions WHERE beneficiaire_id=%s ORDER BY date DESC LIMIT 10",(u["id"],))
 cm=c.fetchall()
 c.execute("SELECT * FROM paiements WHERE user_id=%s AND statut='en_attente'",(u["id"],))
 p=c.fetchone()
 conn.close()
 lien=request.host_url+"inscription?ref="+str(u["id"])
 return render_template("dashboard.html",user=u,filleuls=f,commissions=cm,pending=p,lien_parrainage=lien,wallet=WALLET_USDT,membership=MEMBERSHIP_USDT,gains_niveau=GAINS)
@app.route("/payer",methods=["POST"])
@lr
def payer():
 u=gui(session["user_id"])
 if u["actif"]==1:
  flash("Compte déjà actif !","success")
  return redirect(url_for("dashboard"))
 ws=request.form.get("wallet_sender","").strip()
 conn=get_db()
 c=conn.cursor()
 c.execute("SELECT id FROM paiements WHERE user_id=%s AND statut='en_attente'",(u["id"],))
 ex=c.fetchone()
 if not ex:
  c.execute("INSERT INTO paiements (user_id,montant,wallet_sender,date) VALUES (%s,%s,%s,%s)",(u["id"],MEMBERSHIP_USDT,ws or None,datetime.now().strftime("%Y-%m-%d %H:%M")))
  if ws:c.execute("UPDATE users SET wallet_sender=%s WHERE id=%s",(ws,u["id"]))
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
 pw=request.form.get("password")
 if email==ADMIN_EMAIL and pw==os.environ.get("ADMIN_PASSWORD","cashmoney2024"):
  session["admin"]=True
  return redirect(url_for("admin_dashboard"))
 flash("Identifiants incorrects.","error")
 return redirect(url_for("admin_login_page"))
@app.route("/admin/dashboard")
def admin_dashboard():
 if not session.get("admin"):return redirect(url_for("admin_login_page"))
 conn=get_db()
 c=conn.cursor()
 c.execute("SELECT COUNT(*) as n FROM users")
 total=c.fetchone()["n"]
 c.execute("SELECT COUNT(*) as n FROM users WHERE actif=1")
 actifs=c.fetchone()["n"]
 c.execute("SELECT p.*,u.nom,u.email FROM paiements p JOIN users u ON u.id=p.user_id WHERE p.statut='en_attente'")
 pp=c.fetchall()
 c.execute("SELECT SUM(gains_total) as s FROM users")
 gains=c.fetchone()["s"] or 0
 conn.close()
 return render_template("admin.html",total=total,actifs=actifs,pending_payments=pp,total_gains=int(gains))
@app.route("/admin/confirmer/<int:pid>")
def admin_confirmer(pid):
 if not session.get("admin"):return redirect(url_for("admin_login_page"))
 conn=get_db()
 c=conn.cursor()
 c.execute("SELECT * FROM paiements WHERE id=%s",(pid,))
 p=c.fetchone()
 if p:
  c.execute("UPDATE paiements SET statut='confirme' WHERE id=%s",(pid,))
  c.execute("UPDATE users SET actif=1 WHERE id=%s",(p["user_id"],))
  conn.commit()
  dist(p["user_id"])
 conn.close()
 flash("Confirmé !","success")
 return redirect(url_for("admin_dashboard"))
@app.route("/admin/rejeter/<int:pid>")
def admin_rejeter(pid):
 if not session.get("admin"):return redirect(url_for("admin_login_page"))
 conn=get_db()
 c=conn.cursor()
 c.execute("UPDATE paiements SET statut='rejete' WHERE id=%s",(pid,))
 conn.commit()
 conn.close()
 flash("Rejeté.","info")
 return redirect(url_for("admin_dashboard"))
 @app.route("/retrait",methods=["GET","POST"])
@lr
def retrait():
 u=gui(session["user_id"])
 if u["actif"]==0:
  flash("Activez votre compte d'abord.","error")
  return redirect(url_for("dashboard"))
 conn=get_db()
 c=conn.cursor()
 if request.method=="POST":
  montant=int(request.form.get("montant",0))
  wallet=request.form.get("wallet","").strip()
  if montant<1000:
   flash("Montant minimum 1000 FCFA.","error")
  elif montant>u["gains_total"]:
   flash("Solde insuffisant.","error")
  elif not wallet:
   flash("Adresse wallet obligatoire.","error")
  else:
   c.execute("INSERT INTO retraits (user_id,montant,wallet,statut,date) VALUES (%s,%s,%s,'en_attente',%s)",(u["id"],montant,wallet,datetime.now().strftime("%Y-%m-%d %H:%M")))
   c.execute("UPDATE users SET gains_total=gains_total-%s WHERE id=%s",(montant,u["id"]))
   conn.commit()
   flash("Demande de retrait soumise ! L'admin va traiter votre demande.","success")
   return redirect(url_for("retrait"))
 c.execute("SELECT * FROM retraits WHERE user_id=%s ORDER BY date DESC",(u["id"],))
 retraits=c.fetchall()
 conn.close()
 return render_template("retrait.html",user=u,retraits=retraits)
init_db(c.execute("CREATE TABLE IF NOT EXISTS retraits (id SERIAL PRIMARY KEY,user_id INTEGER,montant REAL,wallet TEXT,statut TEXT DEFAULT 'en_attente',date TEXT)")
start_payment_checker(interval=60)
if __name__=="__main__":
 port=int(os.environ.get("PORT",5000))
 app.run(host="0.0.0.0",port=port,debug=False)

def check_and_pay_balance(user_id, seuil=5.00):
    """
    Vérifie si le solde de l'utilisateur atteint le seuil.
    Si oui, envoie automatiquement le montant total sur son wallet.
    Retourne True si paiement envoyé, False sinon.
    """
    user = User.query.get(user_id)  # Adaptez à votre modèle
    if not user:
        logger.error(f"❌ Utilisateur ID {user_id} introuvable.")
        return False
    
    if user.gains < seuil:
        logger.info(f"ℹ️ {user.email} | Solde: {user.gains}$ | Seuil: {seuil}$ - En attente.")
        return False
    
    if not user.usdt_wallet or user.usdt_wallet == "":
        logger.warning(f"⚠️ {user.email} a {user.gains}$ mais PAS de wallet USDT.")
        return False
    
    amount_to_send = round(user.gains, 2)
    logger.info(f"🚀 Paiement pour {user.email} | Montant: {amount_to_send}$")
    
    tx_hash = send_usdt_auto(user.usdt_wallet, amount_to_send)
    
    if tx_hash:
        user.gains = 0.00
        # Optionnel : log dans une table PaymentHistory
        # history = PaymentHistory(user_id=user.id, amount=amount_to_send, tx_hash=tx_hash)
        # db.session.add(history)
        db.session.commit()
        logger.info(f"✅ {amount_to_send}$ envoyé à {user.email}. Solde remis à 0.")
        return True
    else:
        logger.error(f"❌ Échec du paiement pour {user.email}. Solde conservé.")
        return False
