import os
import sqlite3
from datetime import datetime, date
from functools import wraps
from urllib.parse import urlencode

from flask import Flask, g, redirect, render_template, request, session, url_for, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash

APP_NAME = "Encontre Distribuidoras"
DB_PATH = os.path.join(os.path.dirname(__file__), "app.db")

ADMIN_EMAIL = "admin@gmail.com"
ADMIN_PASSWORD = "admin"  # só para teste

app = Flask(__name__)
app.secret_key = "dev-secret-key-change-later"


# -----------------------
# DB
# -----------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)

    db.execute("""
    CREATE TABLE IF NOT EXISTS admins (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      email TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """)

    db.execute("""
    CREATE TABLE IF NOT EXISTS clients (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      email TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """)

    db.execute("""
    CREATE TABLE IF NOT EXISTS companies (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      client_id INTEGER,               -- dono (cliente). pode ser NULL quando criado pelo admin para teste
      name TEXT NOT NULL,
      products TEXT NOT NULL,
      contact_name TEXT,
      phone TEXT,
      whatsapp TEXT,
      email TEXT,
      address TEXT,
      cep TEXT,
      city TEXT,
      state TEXT,
      notes TEXT,

      plan_status TEXT NOT NULL DEFAULT 'inactive',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,

      FOREIGN KEY(client_id) REFERENCES clients(id)
    )
    """)

    db.execute("""
    CREATE TABLE IF NOT EXISTS analytics (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      company_id INTEGER NOT NULL,
      event_type TEXT NOT NULL,       -- 'view' | 'whatsapp_click'
      created_at TEXT NOT NULL,
      ip TEXT,
      user_agent TEXT,
      FOREIGN KEY(company_id) REFERENCES companies(id)
    )
    """)

    db.commit()

    # Seed admin
    row = db.execute("SELECT id FROM admins WHERE email=?", (ADMIN_EMAIL,)).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO admins (email, password_hash, created_at) VALUES (?, ?, ?)",
            (ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD), datetime.utcnow().isoformat())
        )
        db.commit()

    db.close()


# -----------------------
# Helpers
# -----------------------
def normalize_phone(s: str) -> str:
    if not s:
        return ""
    return "".join(ch for ch in s if ch.isdigit())


def track(company_id: int, event_type: str):
    db = get_db()
    db.execute(
        "INSERT INTO analytics (company_id, event_type, created_at, ip, user_agent) VALUES (?, ?, ?, ?, ?)",
        (
            company_id,
            event_type,
            datetime.utcnow().isoformat(),
            request.headers.get("X-Forwarded-For", request.remote_addr),
            (request.headers.get("User-Agent", "") or "")[:255],
        ),
    )
    db.commit()


def read_company_form():
    name = (request.form.get("name") or "").strip()
    products = (request.form.get("products") or "").strip()
    if not name:
        flash("Nome da empresa é obrigatório.", "error")
        raise ValueError("name required")
    if not products:
        flash("Produtos são obrigatórios.", "error")
        raise ValueError("products required")

    return {
        "name": name,
        "products": products,
        "contact_name": (request.form.get("contact_name") or "").strip(),
        "phone": normalize_phone((request.form.get("phone") or "").strip()),
        "whatsapp": normalize_phone((request.form.get("whatsapp") or "").strip()),
        "email": (request.form.get("email") or "").strip(),
        "address": (request.form.get("address") or "").strip(),
        "cep": (request.form.get("cep") or "").strip(),
        "city": (request.form.get("city") or "").strip(),
        "state": (request.form.get("state") or "").strip().upper(),
        "notes": (request.form.get("notes") or "").strip(),
    }


# -----------------------
# Auth: Admin
# -----------------------
def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("admin_login", next=request.full_path))
        return fn(*args, **kwargs)
    return wrapper

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        db = get_db()
        admin = db.execute("SELECT * FROM admins WHERE email=?", (email,)).fetchone()
        if admin and check_password_hash(admin["password_hash"], password):
            session.clear()
            session["admin_id"] = admin["id"]
            session["admin_email"] = admin["email"]
            next_url = request.args.get("next")
            return redirect(next_url or url_for("admin_dashboard"))
           
        flash("Login inválido.", "error")
        return redirect(url_for("admin_login"))
    return render_template("login_admin.html", app_name=APP_NAME, admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASSWORD)


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# -----------------------
# Auth: Client (Distribuidora)
# -----------------------
def client_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("client_id"):
            return redirect(url_for("client_login", next=request.full_path))
        return fn(*args, **kwargs)
    return wrapper
@app.route("/cliente/plano", methods=["GET", "POST"])
@client_required
def client_plan():
    db = get_db()
    company = db.execute("SELECT * FROM companies WHERE client_id=?", (session["client_id"],)).fetchone()

    if not company:
        flash("Cadastre sua empresa antes de selecionar um plano.", "error")
        return redirect(url_for("client_company_edit"))

    if request.method == "POST":
        chosen = request.form.get("plan") or "basic"
        # por enquanto só guardamos em notes (ou você cria coluna 'plan_name')
        new_notes = (company["notes"] or "")
        new_notes = f"[PLANO ESCOLHIDO: {chosen}] " + new_notes

        db.execute("UPDATE companies SET notes=?, updated_at=? WHERE id=?",
                   (new_notes, datetime.utcnow().isoformat(), company["id"]))
        db.commit()

        flash("Plano selecionado! Agora finalize a assinatura para ficar visível.", "ok")
        return redirect(url_for("client_dashboard"))

    return render_template("client_plan.html", app_name=APP_NAME, company=company)


@app.route("/cliente/cadastrar", methods=["GET", "POST"])
def client_register():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not email or not password or len(password) < 4:
            flash("Informe email e uma senha (mín. 4 caracteres).", "error")
            return redirect(url_for("client_register"))

        db = get_db()
        try:
            db.execute(
                "INSERT INTO clients (email, password_hash, created_at) VALUES (?, ?, ?)",
                (email, generate_password_hash(password), datetime.utcnow().isoformat())
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("Esse email já está cadastrado.", "error")
            return redirect(url_for("client_register"))

        flash("Conta criada. Faça login.", "ok")
        return redirect(url_for("client_login"))

    return render_template("client_register.html", app_name=APP_NAME)


@app.route("/cliente/login", methods=["GET", "POST"])
def client_login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        db = get_db()
        c = db.execute("SELECT * FROM clients WHERE email=?", (email,)).fetchone()
        if c and check_password_hash(c["password_hash"], password):
            session.clear()
            session["client_id"] = c["id"]
            session["client_email"] = c["email"]
            next_url = request.args.get("next")
            return redirect(next_url or url_for("client_dashboard"))
        flash("Login inválido.", "error")
        return redirect(url_for("client_login"))

    return render_template("client_login.html", app_name=APP_NAME)


@app.route("/cliente/logout")
def client_logout():
    session.clear()
    return redirect(url_for("public_index"))


# -----------------------
# Public (GET + POST para não dar Method Not Allowed)
# -----------------------
@app.route("/", methods=["GET", "POST"])
def public_index():
    if request.method == "POST":
        q = (request.form.get("q") or "").strip()
        city = (request.form.get("city") or "").strip()
        state = (request.form.get("state") or "").strip().upper()
        qs = urlencode({"q": q, "city": city, "state": state})
        return redirect(url_for("public_index") + ("?" + qs if qs else ""))

    q = (request.args.get("q") or "").strip().lower()
    city = (request.args.get("city") or "").strip().lower()
    state = (request.args.get("state") or "").strip().upper()

    db = get_db()
    sql = "SELECT * FROM companies WHERE plan_status='active'"
    params = []

    if q:
        sql += " AND (lower(name) LIKE ? OR lower(products) LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if city:
        sql += " AND lower(city) LIKE ?"
        params.append(f"%{city}%")
    if state:
        sql += " AND state = ?"
        params.append(state)

    sql += " ORDER BY id DESC LIMIT 200"
    rows = db.execute(sql, params).fetchall()
    return render_template("public_index.html", app_name=APP_NAME, rows=rows, q=q, city=city, state=state)


@app.route("/empresa/<int:company_id>")
def empresa(company_id: int):
    return public_company(company_id)


@app.route("/empresa/<int:company_id>")
def public_company(company_id: int):
    db = get_db()
    c = db.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    if not c:
        abort(404)

    # ✅ Se não estiver ativa, não mostra para o público
    if c["plan_status"] != "active":
        abort(404)

    track(company_id, "view")
    return render_template("public_company.html", app_name=APP_NAME, c=c)


@app.route("/go/whatsapp/<int:company_id>")
def go_whatsapp(company_id: int):
    db = get_db()
    c = db.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    if not c:
        abort(404)

    if c["plan_status"] != "active":
        abort(404)

    wpp = normalize_phone(c["whatsapp"])
    if not wpp:
        flash("Essa empresa não tem WhatsApp cadastrado.", "error")
        return redirect(url_for("public_index"))

    track(company_id, "whatsapp_click")
    return redirect(f"https://wa.me/55{wpp}")



# -----------------------
# Client: painel + editar empresa (a própria)
# -----------------------
@app.route("/cliente")
@client_required
def client_dashboard():
    db = get_db()
    company = db.execute("SELECT * FROM companies WHERE client_id=?", (session["client_id"],)).fetchone()
    return render_template("client_dashboard.html", app_name=APP_NAME, company=company)


@app.route("/cliente/empresa", methods=["GET", "POST"])
@client_required
def client_company_edit():
    db = get_db()
    company = db.execute("SELECT * FROM companies WHERE client_id=?", (session["client_id"],)).fetchone()

    if request.method == "POST":
        data = read_company_form()
        now = datetime.utcnow().isoformat()

        if company is None:
            db.execute("""
              INSERT INTO companies
              (client_id, name, products, contact_name, phone, whatsapp, email, address, cep, city, state, notes,
               plan_status, created_at, updated_at)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'inactive', ?, ?)
            """, (
                session["client_id"], data["name"], data["products"], data["contact_name"], data["phone"], data["whatsapp"],
                data["email"], data["address"], data["cep"], data["city"], data["state"], data["notes"],
                now, now
            ))
        else:
            db.execute("""
              UPDATE companies SET
                name=?, products=?, contact_name=?, phone=?, whatsapp=?, email=?,
                address=?, cep=?, city=?, state=?, notes=?, updated_at=?
              WHERE id=? AND client_id=?
            """, (
                data["name"], data["products"], data["contact_name"], data["phone"], data["whatsapp"], data["email"],
                data["address"], data["cep"], data["city"], data["state"], data["notes"], now,
                company["id"], session["client_id"]
            ))

        db.commit()
        flash("Dados salvos.", "ok")
        return redirect(url_for("client_dashboard"))

    return render_template("client_company_form.html", app_name=APP_NAME, company=company)


# -----------------------
# Admin: listar + criar/editar (para testes) + ativar/desativar
# -----------------------
@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    rows = db.execute("""
      SELECT c.id, c.name, c.city, c.state, c.plan_status, c.updated_at,
             cl.email as client_email
      FROM companies c
      LEFT JOIN clients cl ON cl.id = c.client_id
      ORDER BY c.id DESC
      LIMIT 500
    """).fetchall()
    return render_template("admin_dashboard.html", app_name=APP_NAME, rows=rows)



@app.route("/admin/empresas/nova", methods=["GET", "POST"])
@admin_required
def admin_company_new():
    if request.method == "POST":
        data = read_company_form()
        now = datetime.utcnow().isoformat()
        db = get_db()
        db.execute("""
          INSERT INTO companies
          (client_id, name, products, contact_name, phone, whatsapp, email, address, cep, city, state, notes,
           plan_status, created_at, updated_at)
          VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'inactive', ?, ?)
        """, (
            data["name"], data["products"], data["contact_name"], data["phone"], data["whatsapp"], data["email"],
            data["address"], data["cep"], data["city"], data["state"], data["notes"],
            now, now
        ))
        db.commit()
        flash("Empresa criada (teste). Agora você pode ativar o plano.", "ok")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_company_form.html", app_name=APP_NAME, mode="new", c=None)


@app.route("/admin/empresas/<int:company_id>/editar", methods=["GET", "POST"])
@admin_required
def admin_company_edit(company_id: int):
    db = get_db()
    c = db.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    if not c:
        abort(404)

    if request.method == "POST":
        data = read_company_form()
        now = datetime.utcnow().isoformat()
        db.execute("""
          UPDATE companies SET
            name=?, products=?, contact_name=?, phone=?, whatsapp=?, email=?,
            address=?, cep=?, city=?, state=?, notes=?, updated_at=?
          WHERE id=?
        """, (
            data["name"], data["products"], data["contact_name"], data["phone"], data["whatsapp"], data["email"],
            data["address"], data["cep"], data["city"], data["state"], data["notes"], now,
            company_id
        ))
        db.commit()
        flash("Empresa atualizada.", "ok")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_company_form.html", app_name=APP_NAME, mode="edit", c=c)


@app.route("/admin/empresas/<int:company_id>/toggle-plano", methods=["POST"])
@admin_required
def admin_toggle_plan(company_id: int):
    db = get_db()
    c = db.execute("SELECT id, plan_status FROM companies WHERE id=?", (company_id,)).fetchone()
    if not c:
        abort(404)

    new_status = "active" if c["plan_status"] != "active" else "inactive"
    db.execute("UPDATE companies SET plan_status=?, updated_at=? WHERE id=?",
               (new_status, datetime.utcnow().isoformat(), company_id))
    db.commit()

    flash(f"Plano alterado para: {new_status}", "ok")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/relatorio/hoje")
@admin_required
def admin_report_today():
    today = date.today().isoformat()
    db = get_db()
    rows = db.execute("""
      SELECT
        c.id,
        c.name,
        c.city,
        c.state,
        SUM(CASE WHEN a.event_type='view' THEN 1 ELSE 0 END) AS views,
        SUM(CASE WHEN a.event_type='whatsapp_click' THEN 1 ELSE 0 END) AS whatsapp_clicks
      FROM companies c
      LEFT JOIN analytics a ON a.company_id=c.id AND substr(a.created_at,1,10)=?
      GROUP BY c.id
      ORDER BY whatsapp_clicks DESC, views DESC
    """, (today,)).fetchall()

    return render_template("admin_report_today.html", app_name=APP_NAME, today=today, rows=rows)


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
