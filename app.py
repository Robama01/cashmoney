import os
from flask import Flask, render_template, request, redirect, url_for, session, flash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "clef-secrete-temporaire")

# ==============================================
# CONFIGURATION
# ==============================================
MEMBERSHIP_USDT = 2.10
WALLET_USDT = "0xE4901E78F8c92199bAfD93AD87C5a250C48199c2"
REWARD_PER_VIDEO = 0.50
DAILY_VIDEO_LIMIT = 10

# ==============================================
# ROUTES (sans base de données)
# ==============================================

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/inscription", methods=["GET", "POST"])
def inscription():
    if request.method == "POST":
        # Simulation d'inscription (sans base de données)
        flash("⚠️ Inscription simulée (base de données non configurée)", "warning")
        return redirect(url_for("login"))
    ref = request.args.get("ref")
    return render_template("inscription.html", ref=ref)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        # Simulation de connexion
        session["user_id"] = 1  # ID fictif
        flash("✅ Connexion simulée (base de données non configurée)", "success")
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html", user="Test", balance=0.00, actif=0)

@app.route("/depot")
def depot():
    return render_template("depot.html", wallet=WALLET_USDT, montant=MEMBERSHIP_USDT, user_id=1, ref="test123")

@app.route("/watch-ads")
def watch_ads():
    return render_template("watch_ads.html", remaining=DAILY_VIDEO_LIMIT, reward=REWARD_PER_VIDEO)

@app.route("/hello")
def hello():
    return "<h1 style='color:green;'>✅ Code fonctionnel !</h1><p>Prix = 2.10 USDT | Vidéo = 0.50 USDT</p>"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
