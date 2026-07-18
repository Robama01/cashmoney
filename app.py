import os
from flask import Flask

app = Flask(__name__)

@app.route("/")
def index():
    return "Bonjour ! Le serveur fonctionne."

@app.route("/hello")
def hello():
    return "Salut ! La route /hello est OK."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
