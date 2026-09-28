import os
import json
import uuid
import sqlite3
from datetime import datetime, timezone
from flask import Flask, request, Response, jsonify, render_template
from engine import run_pipeline, DOC_LABELS, SANCTIONED, GROQ_MODEL
from samples import SAMPLES

app = Flask(__name__)
DB_PATH = os.getenv("DB_PATH", "vendorgate.db")


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


with db() as con:
    con.execute("""CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY, created_at TEXT, legal_name TEXT, country TEXT,
        status TEXT, risk_score INTEGER, summary TEXT, submission TEXT, events TEXT)""")


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return {"ok": True, "groq": bool(os.getenv("GROQ_API_KEY")), "model": GROQ_MODEL}


@app.get("/api/config")
def config():
    return jsonify({"doc_labels": DOC_LABELS, "groq": bool(os.getenv("GROQ_API_KEY")), "model": GROQ_MODEL,
                    "samples": SAMPLES})


@app.post("/api/run")
def run():
    sub = request.get_json(force=True) or {}
    fast = request.args.get("fast") == "1"

    def gen():
        events = []
        for ev in run_pipeline(sub, fast):
            events.append(ev)
            yield json.dumps(ev) + "\n"
        dec = next(e for e in events if e["type"] == "decision")
        comms = next((e for e in events if e["type"] == "comms"), {})
        run_id = uuid.uuid4().hex[:8]
        with db() as con:
            con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)", (
                run_id, datetime.now(timezone.utc).isoformat(), sub.get("legal_name") or "(unnamed)",
                sub.get("country", ""), dec["status"], dec["risk_score"], comms.get("summary", ""),
                json.dumps(sub), json.dumps(events)))
        yield json.dumps({"type": "saved", "run_id": run_id}) + "\n"

    return Response(gen(), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs")
def runs():
    with db() as con:
        rows = con.execute("SELECT id,created_at,legal_name,country,status,risk_score,summary "
                           "FROM runs ORDER BY created_at DESC LIMIT 200").fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/runs/<rid>")
def run_detail(rid):
    with db() as con:
        r = con.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    if not r:
        return {"error": "not found"}, 404
    d = dict(r)
    d["submission"] = json.loads(d["submission"])
    d["events"] = json.loads(d["events"])
    return jsonify(d)


@app.delete("/api/runs")
def clear():
    with db() as con:
        con.execute("DELETE FROM runs")
    return {"ok": True}


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
