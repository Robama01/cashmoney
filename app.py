import os
import sys
from flask import Flask

print("🔍 Début du script app.py")  # Premier message de log

app = Flask(__name__)

print("✅ Application Flask créée")

@app.route("/")
def index():
    print("🟢 Requête sur /")  # Pour voir si la route est appelée
    return "Bonjour ! Le serveur fonctionne."

@app.route("/hello")
def hello():
    return "Salut ! La route /hello est OK."

if __name__ == "__main__":
    print("🚀 Lancement du serveur")
    try:
        port = int(os.environ.get("PORT", 5000))
        print(f"📡 Port utilisé : {port}")
        app.run(host="0.0.0.0", port=port, debug=False)
    except Exception as e:
        print(f"❌ ERREUR : {e}")
        sys.exit(1)
