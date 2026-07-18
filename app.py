import os
from flask import Flask

app = Flask(__name__)

@app.route("/")
def index():
    return "Bonjour ! Le serveur fonctionne."

@app.route("/hello")
def hello():
    return "Salut ! La route /hello est OK."

# Pas de if __name__ == "__main__" pour Gunicorn
# Gunicorn utilisera directement 'app'
