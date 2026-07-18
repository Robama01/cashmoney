
import os
print("Démarrage de l'application...")

port = int(os.environ.get("PORT", 10000))
print(f"Port : {port}")

from flask import Flask
app = Flask(__name__)

@app.route("/")
def index():
    return "OK"

if __name__ == "__main__":
    print("Lancement du serveur...")
    app.run(host="0.0.0.0", port=port, debug=False)
    print("Serveur arrêté.")
