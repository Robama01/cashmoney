
import os
from flask import Flask, render_template

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/hello")
def hello():
    return "Salut ! La route /hello est OK."

# Pour Render, on n'a pas besoin de if __name__ == "__main__"
