import os
from flask import Flask

app = Flask(__name__)

@app.route("/")
def index():
    return "<h1 style='color:green;'>✅ Site fonctionnel !</h1><p>Votre site est en ligne.</p>"

@app.route("/hello")
def hello():
    return "<h1 style='color:blue;'>✅ Route /hello OK !</h1><p>Prix = 2.10 USDT</p>"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
