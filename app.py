
import os
from flask import Flask

app = Flask(__name__)

@app.route("/")
def index():
    return "Bonjour ! Le serveur fonctionne."

@app.route("/hello")
def hello():
    return "Salut ! La route /hello est OK."

# ⚠️ CE BLOC EST ESSENTIEL POUR python app.py
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
