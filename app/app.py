
import os
import time
from pathlib import Path

from flask import Flask, render_template, request, jsonify
import psycopg
import redis
from psycopg.rows import dict_row

app = Flask(__name__)

# ==========================================
# KONFIGURASI DATABASE
# ==========================================

DB_HOST = os.getenv("DB_HOST", "db")
DB_NAME = os.getenv("DB_NAME", "labvisit")
DB_USER = os.getenv("DB_USER", "labuser")
DB_PORT = int(os.getenv("DB_PORT", "5432"))

# D26 SECRET HANDLING
# Password dibaca dari Docker Secret.
DB_PASSWORD_FILE = Path("/run/secrets/db_password")

if DB_PASSWORD_FILE.is_file():
    DB_PASSWORD = DB_PASSWORD_FILE.read_text(
        encoding="utf-8"
    ).strip()
else:
    # Fallback untuk pengembangan lokal.
    DB_PASSWORD = os.getenv(
        "DB_PASSWORD",
        "LabVisit_Strong_2026"
    )

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

cache = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True
)


# ==========================================
# KONEKSI POSTGRESQL
# ==========================================

def get_db():
    return psycopg.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT,
        row_factory=dict_row,
        connect_timeout=5
    )


# ==========================================
# INISIALISASI DATABASE
# ==========================================

def init_db():
    for attempt in range(30):
        try:
            with get_db() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS visitors (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(120) NOT NULL,
                        institution VARCHAR(150) NOT NULL,
                        purpose VARCHAR(255) NOT NULL,
                        visit_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

            print(
                "Database LABVISIT berhasil disiapkan.",
                flush=True
            )
            return

        except psycopg.OperationalError:
            print(
                f"Menunggu database... percobaan {attempt + 1}/30",
                flush=True
            )
            time.sleep(2)

    raise RuntimeError("Database tidak dapat dihubungi.")


# ==========================================
# HALAMAN UTAMA
# ==========================================

@app.route("/")
def index():
    return render_template("index.html")


# ==========================================
# HEALTH CHECK
# ==========================================

@app.route("/health")
def health():
    try:
        with get_db() as conn:
            conn.execute("SELECT 1")

        cache.ping()

        return jsonify({
            "status": "healthy",
            "application": "LABVISIT",
            "database": "connected",
            "redis": "connected",
            "secret_handling": DB_PASSWORD_FILE.is_file()
        }), 200

    except Exception:
        return jsonify({
            "status": "unhealthy",
            "application": "LABVISIT",
            "database": "disconnected or unavailable",
            "redis": "disconnected or unavailable"
        }), 503


# ==========================================
# MENGAMBIL DATA PENGUNJUNG
# ==========================================

@app.route("/api/visitors", methods=["GET"])
def get_visitors():
    keyword = request.args.get("search", "").strip()

    with get_db() as conn:
        visitors = conn.execute("""
            SELECT
                id,
                name,
                institution,
                purpose,
                visit_date
            FROM visitors
            WHERE name ILIKE %s
               OR institution ILIKE %s
               OR purpose ILIKE %s
            ORDER BY visit_date DESC
        """, (
            f"%{keyword}%",
            f"%{keyword}%",
            f"%{keyword}%"
        )).fetchall()

    return jsonify(visitors)


# ==========================================
# MENAMBAHKAN PENGUNJUNG
# ==========================================

@app.route("/api/visitors", methods=["POST"])
def add_visitor():
    data = request.get_json(silent=True) or {}

    name = data.get("name", "")
    institution = data.get("institution", "")
    purpose = data.get("purpose", "")

    if not all(
        isinstance(value, str)
        for value in (name, institution, purpose)
    ):
        return jsonify({
            "error": "Format data tidak valid."
        }), 400

    name = name.strip()
    institution = institution.strip()
    purpose = purpose.strip()

    if not name or not institution or not purpose:
        return jsonify({
            "error": "Nama, institusi, dan tujuan wajib diisi."
        }), 400

    if (
        len(name) > 120
        or len(institution) > 150
        or len(purpose) > 255
    ):
        return jsonify({
            "error": "Panjang data melebihi batas yang diizinkan."
        }), 400

    with get_db() as conn:
        visitor = conn.execute("""
            INSERT INTO visitors (
                name,
                institution,
                purpose
            )
            VALUES (%s, %s, %s)
            RETURNING
                id,
                name,
                institution,
                purpose,
                visit_date
        """, (
            name,
            institution,
            purpose
        )).fetchone()

    cache.delete("total_visitors", "today_visitors")

    return jsonify({
        "message": "Data pengunjung berhasil disimpan.",
        "visitor": visitor
    }), 201


# ==========================================
# MENGHAPUS DATA PENGUNJUNG
# ==========================================

@app.route("/api/visitors/<int:visitor_id>", methods=["DELETE"])
def delete_visitor(visitor_id):
    with get_db() as conn:
        result = conn.execute("""
            DELETE FROM visitors
            WHERE id = %s
            RETURNING id
        """, (visitor_id,)).fetchone()

    if not result:
        return jsonify({
            "error": "Data pengunjung tidak ditemukan."
        }), 404

    cache.delete("total_visitors", "today_visitors")

    return jsonify({
        "message": "Data pengunjung berhasil dihapus."
    }), 200


# ==========================================
# STATISTIK PENGUNJUNG
# ==========================================

@app.route("/api/stats", methods=["GET"])
def stats():
    with get_db() as conn:
        total = conn.execute("""
            SELECT COUNT(*) AS total
            FROM visitors
        """).fetchone()["total"]

        today = conn.execute("""
            SELECT COUNT(*) AS total
            FROM visitors
            WHERE visit_date::date = CURRENT_DATE
        """).fetchone()["total"]

    cache.set("total_visitors", total)
    cache.set("today_visitors", today)

    return jsonify({
        "total_visitors": int(total),
        "today_visitors": int(today)
    })


# ==========================================
# INISIALISASI DATABASE
# ==========================================

init_db()


# ==========================================
# MODE DEVELOPMENT
# ==========================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
