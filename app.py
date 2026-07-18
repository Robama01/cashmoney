import os
import traceback
from flask import Flask, render_template

app = Flask(__name__)

@app.route("/")
def index():
    try:
        return render_template("index.html")
    except Exception as e:
        return f"<h1 style='color:red;'>ERREUR DANS LE TEMPLATE</h1><pre>{traceback.format_exc()}</pre>"

@app.route("/hello")
def hello():
    return "Salut ! La route /hello est OK."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
