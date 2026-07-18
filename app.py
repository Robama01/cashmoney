import os
import sys
from flask import Flask, render_template

print("🔍 Démarrage du script...")

try:
    app = Flask(__name__)
    print("✅ Application Flask créée")

    @app.route("/")
    def index():
        try:
            return render_template("index.html")
        except Exception as e:
            return f"<h1 style='color:red;'>ERREUR DANS LE TEMPLATE</h1><pre>{str(e)}</pre>"

    @app.route("/hello")
    def hello():
        return "Salut ! La route /hello est OK."

    print("✅ Routes définies")

except Exception as e:
    print(f"❌ ERREUR LORS DE LA CRÉATION : {e}")
    sys.exit(1)

# Pas de if __name__ == "__main__" pour Render
