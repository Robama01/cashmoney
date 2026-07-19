"""Utilitaires partagés pour l'accès à la base de données PostgreSQL."""

import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


@contextmanager
def db_cursor(commit=False):
    """Ouvre une connexion et un curseur, puis les ferme automatiquement.

    Si ``commit`` est vrai, la transaction est validée quand le bloc se termine
    sans erreur. En cas d'exception (ou si ``commit`` est faux et rien n'est
    validé), la fermeture de la connexion annule la transaction en cours.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        yield cur
        if commit:
            conn.commit()
    finally:
        cur.close()
        conn.close()
