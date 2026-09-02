import os
import sqlite3
import secrets
import json
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, abort, jsonify, flash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
DB = os.path.join(os.path.dirname(__file__), "database.db")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

ACTIVITY_TYPES = {
    "matching": "🔗 Сәйкестендіру",
    "quiz": "🧠 Тест / Викторина",
    "fill": "✍️ Бос орынды толтыр",
    "truefalse": "⚡ Дұрыс / Бұрыс",
    "categorize": "🗂️ Санаттарға бөлу",
    "ordering": "🔢 Ретін тап",
    "flashcards": "🃏 Флеш-карталар",
    "wordpuzzle": "🔤 Сөзжұмбақ",
    "imagequiz": "🖼️ Сурет бойынша",
    "audioquiz": "🎧 Аудио сұрақ",
}


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        slug TEXT UNIQUE NOT NULL,
        difficulty TEXT NOT NULL DEFAULT 'Қалыпты',
        timer_seconds INTEGER NOT NULL DEFAULT 0,
        shuffle INTEGER NOT NULL DEFAULT 1,
        activity_type TEXT NOT NULL DEFAULT 'matching',
        data_json TEXT NOT NULL DEFAULT '[]',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pairs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        left_text TEXT NOT NULL,
        right_text TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        student_name TEXT NOT NULL DEFAULT 'Аноним',
        score INTEGER NOT NULL,
        total INTEGER NOT NULL,
        time_seconds INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
    )""")
    migrations = [
        "ALTER TABLE tasks ADD COLUMN difficulty TEXT NOT NULL DEFAULT 'Қалыпты'",
        "ALTER TABLE tasks ADD COLUMN timer_seconds INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE tasks ADD COLUMN shuffle INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE tasks ADD COLUMN activity_type TEXT NOT NULL DEFAULT 'matching'",
        "ALTER TABLE tasks ADD COLUMN data_json TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE results ADD COLUMN student_name TEXT NOT NULL DEFAULT 'Аноним'",
        "ALTER TABLE results ADD COLUMN time_seconds INTEGER NOT NULL DEFAULT 0",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def logged_in():
    return session.get("admin") is True


def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not logged_in():
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapped


@app.route("/")
def home():
    if logged_in():
        return redirect(url_for("admin"))
    return render_template("home.html", activity_types=ACTIVITY_TYPES)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD):
            session["admin"] = True
            return redirect(request.args.get("next") or url_for("admin"))
        flash("Құпиясөз қате.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/admin")
@login_required
def admin():
    conn = db()
    tasks = conn.execute("""SELECT t.*, COUNT(p.id) AS pair_count,
        (SELECT COUNT(*) FROM results r WHERE r.task_id=t.id) AS result_count
        FROM tasks t LEFT JOIN pairs p ON p.task_id=t.id
        GROUP BY t.id ORDER BY t.id DESC""").fetchall()
    conn.close()
    return render_template("admin.html", tasks=tasks, activity_types=ACTIVITY_TYPES)


def clean_settings(form):
    difficulty = form.get("difficulty", "Қалыпты")
    if difficulty not in {"Жеңіл", "Қалыпты", "Қиын", "Хардкор"}:
        difficulty = "Қалыпты"
    try:
        timer = max(0, min(1800, int(form.get("timer_seconds", "0"))))
    except (ValueError, TypeError):
        timer = 0
    if difficulty == "Хардкор" and timer == 0:
        timer = 45
    return difficulty, timer, 1 if form.get("shuffle") == "on" else 0


def activity_type(form):
    value = form.get("activity_type", "matching")
    return value if value in ACTIVITY_TYPES else "matching"


def collect_items(form, kind):
    if kind in {"quiz", "imagequiz", "audioquiz"}:
        questions = form.getlist("question[]")
        correct = form.getlist("correct[]")
        option1 = form.getlist("option1[]")
        option2 = form.getlist("option2[]")
        option3 = form.getlist("option3[]")
        option4 = form.getlist("option4[]")
        media = form.getlist("media[]")
        items = []
        for i, (q, c, a, b, d, e) in enumerate(zip(questions, correct, option1, option2, option3, option4)):
            opts = [x.strip() for x in (a, b, d, e) if x.strip()]
            q, c = q.strip(), c.strip()
            m = media[i].strip() if i < len(media) else ""
            if q and c and c in opts and (kind == "quiz" or m):
                item = {"question": q, "correct": c, "options": opts}
                if kind in {"imagequiz", "audioquiz"}: item["media"] = m
                items.append(item)
        return items
    if kind == "truefalse":
        qs = form.getlist("question[]")
        ans = form.getlist("answer[]")
        return [{"question": q.strip(), "answer": a} for q, a in zip(qs, ans) if q.strip() and a in {"true", "false"}]
    if kind in {"fill", "wordpuzzle"}:
        qs = form.getlist("question[]")
        ans = form.getlist("answer[]")
        return [{"question": q.strip(), "answer": a.strip()} for q, a in zip(qs, ans) if q.strip() and a.strip()]
    if kind == "categorize":
        qs=form.getlist("question[]"); correct=form.getlist("correct[]"); cats=form.getlist("categories[]")
        return [{"question":q.strip(),"correct":c.strip(),"categories":[x.strip() for x in cats[i].split("|") if x.strip()]} for i,(q,c) in enumerate(zip(qs,correct)) if q.strip() and c.strip() and i < len(cats)]
    if kind == "ordering":
        qs=form.getlist("question[]"); orders=form.getlist("order[]")
        return [{"question":q.strip(),"order":[x.strip() for x in orders[i].split("|") if x.strip()]} for i,q in enumerate(qs) if q.strip() and i < len(orders) and orders[i].strip()]
    if kind == "flashcards":
        fronts=form.getlist("front[]"); backs=form.getlist("back[]")
        return [{"front":a.strip(),"back":b.strip()} for a,b in zip(fronts,backs) if a.strip() and b.strip()]
    lefts = form.getlist("left[]")
    rights = form.getlist("right[]")
    return [{"left": a.strip(), "right": b.strip()} for a, b in zip(lefts, rights) if a.strip() and b.strip()]


def save_task(conn, title, slug, difficulty, timer, shuffle, kind, items):
    cur = conn.execute("INSERT INTO tasks(title,slug,difficulty,timer_seconds,shuffle,activity_type,data_json) VALUES(?,?,?,?,?,?,?)",
                       (title, slug, difficulty, timer, shuffle, kind, json.dumps(items, ensure_ascii=False)))
    task_id = cur.lastrowid
    if kind == "matching":
        conn.executemany("INSERT INTO pairs(task_id,left_text,right_text) VALUES(?,?,?)",
                         [(task_id, x["left"], x["right"]) for x in items])
    return task_id


def editor_context(form=None, task=None, items=None):
    return render_template("editor.html", task=task, items=items or [], form=form, activity_types=ACTIVITY_TYPES)


@app.route("/admin/new", methods=["GET", "POST"])
@login_required
def new_task():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        kind = activity_type(request.form)
        items = collect_items(request.form, kind)
        difficulty, timer, shuffle = clean_settings(request.form)
        if not title or not items:
            flash("Атауы мен кемінде бір дұрыс толтырылған тапсырма енгізіңіз.", "error")
            return editor_context(request.form, None, items)
        slug = secrets.token_urlsafe(7)
        conn = db()
        while conn.execute("SELECT 1 FROM tasks WHERE slug=?", (slug,)).fetchone():
            slug = secrets.token_urlsafe(7)
        save_task(conn, title, slug, difficulty, timer, shuffle, kind, items)
        conn.commit(); conn.close()
        flash("Тапсырма сақталды. Оқушыға сілтемені жіберуге болады.", "success")
        return redirect(url_for("admin"))
    return editor_context(None, None, [{"left": "", "right": ""}])


@app.route("/admin/edit/<int:task_id>", methods=["GET", "POST"])
@login_required
def edit_task(task_id):
    conn = db()
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        conn.close(); abort(404)
    kind = task["activity_type"] if "activity_type" in task.keys() else "matching"
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        kind = activity_type(request.form)
        items = collect_items(request.form, kind)
        difficulty, timer, shuffle = clean_settings(request.form)
        if not title or not items:
            conn.close()
            flash("Атауы мен кемінде бір дұрыс толтырылған тапсырма енгізіңіз.", "error")
            return editor_context(request.form, task, items)
        conn.execute("UPDATE tasks SET title=?,difficulty=?,timer_seconds=?,shuffle=?,activity_type=?,data_json=? WHERE id=?",
                     (title, difficulty, timer, shuffle, kind, json.dumps(items, ensure_ascii=False), task_id))
        conn.execute("DELETE FROM pairs WHERE task_id=?", (task_id,))
        if kind == "matching":
            conn.executemany("INSERT INTO pairs(task_id,left_text,right_text) VALUES(?,?,?)",
                             [(task_id, x["left"], x["right"]) for x in items])
        conn.commit(); conn.close()
        flash("Өзгерістер сақталды.", "success")
        return redirect(url_for("admin"))
    try:
        items = json.loads(task["data_json"] or "[]")
    except Exception:
        items = []
    if not items and kind == "matching":
        items = [dict(x) for x in conn.execute("SELECT left_text as left,right_text as right FROM pairs WHERE task_id=? ORDER BY id", (task_id,)).fetchall()]
    conn.close()
    return editor_context(None, task, items)


@app.post("/admin/delete/<int:task_id>")
@login_required
def delete_task(task_id):
    conn = db()
    conn.execute("DELETE FROM results WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM pairs WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit(); conn.close()
    flash("Тапсырма өшірілді.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/results/<int:task_id>")
@login_required
def results(task_id):
    conn = db()
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    rows = conn.execute("SELECT * FROM results WHERE task_id=? ORDER BY score DESC, time_seconds ASC, id DESC", (task_id,)).fetchall()
    conn.close()
    if not task: abort(404)
    return render_template("results.html", task=task, results=rows)


@app.route("/task/<slug>")
def student(slug):
    conn = db()
    task = conn.execute("SELECT * FROM tasks WHERE slug=?", (slug,)).fetchone()
    if not task:
        conn.close(); abort(404)
    try:
        items = json.loads(task["data_json"] or "[]")
    except Exception:
        items = []
    if task["activity_type"] == "matching" and not items:
        items = [dict(x) for x in conn.execute("SELECT id,left_text as left,right_text as right FROM pairs WHERE task_id=? ORDER BY id", (task["id"],)).fetchall()]
    conn.close()
    return render_template("student.html", task=task, items=items, activity_types=ACTIVITY_TYPES)


@app.post("/task/<slug>/submit")
def submit(slug):
    data = request.get_json(force=True) or {}
    student_name = (data.get("student_name") or "Аноним").strip()[:80] or "Аноним"
    try:
        elapsed = max(0, min(86400, int(data.get("time_seconds", 0))))
    except (TypeError, ValueError):
        elapsed = 0
    conn = db()
    task = conn.execute("SELECT * FROM tasks WHERE slug=?", (slug,)).fetchone()
    if not task:
        conn.close(); return jsonify({"error": "Тапсырма табылмады"}), 404
    try:
        items = json.loads(task["data_json"] or "[]")
    except Exception:
        items = []
    if task["activity_type"] == "matching" and not items:
        rows = conn.execute("SELECT id,left_text,right_text FROM pairs WHERE task_id=?", (task["id"],)).fetchall()
        items = [{"id": str(x["id"]), "left": x["left_text"], "right": x["right_text"]} for x in rows]
    answers = data.get("answers", {}) or {}
    score = 0
    kind = task["activity_type"]
    for i, item in enumerate(items):
        key = str(i)
        if kind == "matching":
            if str(answers.get(key, "")) == str(item.get("right", "")): score += 1
        elif kind in {"quiz", "imagequiz", "audioquiz", "categorize"}:
            if str(answers.get(key, "")).strip() == str(item.get("correct", "")).strip(): score += 1
        elif kind == "ordering":
            got = answers.get(key, [])
            if isinstance(got, list) and got == item.get("order", []): score += 1
        elif kind == "flashcards":
            if str(answers.get(key, "")) == "known": score += 1
        else:
            if str(answers.get(key, "")).strip().casefold() == str(item.get("answer", "")).strip().casefold(): score += 1
    conn.execute("INSERT INTO results(task_id,student_name,score,total,time_seconds) VALUES(?,?,?,?,?)",
                 (task["id"], student_name, score, len(items), elapsed))
    conn.commit(); conn.close()
    return jsonify({"score": score, "total": len(items), "student_name": student_name})


@app.context_processor
def inject():
    return {"logged_in": logged_in(), "activity_types": ACTIVITY_TYPES}


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
