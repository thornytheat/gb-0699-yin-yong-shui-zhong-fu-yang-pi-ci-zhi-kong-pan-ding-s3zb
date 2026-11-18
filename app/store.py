import sqlite3, json, hashlib
from datetime import datetime, timezone
from app.config import DATA_DIR, RULE_VERSION
from app.errors import BusinessError

def connect():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DATA_DIR/"runs.sqlite", timeout=15)
    db.row_factory=sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, decision TEXT NOT NULL DEFAULT '待复核', created TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS correction_orders (run_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated TEXT NOT NULL)")
    return db

def save(text, result):
    digest=hashlib.sha256((RULE_VERSION+"\n"+text).encode()).hexdigest()
    ident=digest[:16]
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,digest,result,created) VALUES(?,?,?,?)",(ident,digest,json.dumps(dict(result,rule_version=RULE_VERSION),ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
    return get(ident)

def get(ident):
    with connect() as db:
        row=db.execute("SELECT * FROM runs WHERE id=?",(ident,)).fetchone()
    if row is None: raise BusinessError("NOT_FOUND","批次不存在",404)
    item=dict(row);item["result"]=json.loads(item["result"]);item["rule_version"]=item["result"].get("rule_version","2026.11.18.1")
    return item

def history():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT id,revision,decision,created FROM runs ORDER BY created DESC LIMIT 40")]

def _release_blocked(current, order):
    """原阻断结论保留在 run.result 中不被覆盖；更正完成后，仅对复测
    PASS 重判的关联（批次,检测项）解除空白污染阻断，其余阻断仍生效。"""
    if not current["result"].get("blocked"):
        return False
    if order is None or order["state"] != "COMPLETED":
        return True
    resolved = {
        (i["batch"], i["analyte"])
        for i in order["affected"]
        if i.get("override", {}).get("status") == "PASS"
    }
    for r in current["result"].get("released", []):
        if r["status"] != "PASS" and (r["batch"], r["analyte"]) not in resolved:
            return True
    return False


def review(ident,revision,decision):
    if not isinstance(decision,str) or decision not in {"通过","退回"}: raise BusinessError("DECISION","复核决定无效")
    current=get(ident)
    order=get_correction_by_run(ident)
    if order is not None and order["state"]!="COMPLETED":
        raise BusinessError("PAUSED","污染更正未完成，关联批次放行暂停",409)
    if decision=="通过" and _release_blocked(current,order):
        raise BusinessError("BLOCKED","存在阻断项，请先修正数据",409)
    with connect() as db:
        changed=db.execute("UPDATE runs SET revision=revision+1,decision=? WHERE id=? AND revision=?",(decision,ident,revision)).rowcount
        if not changed: raise BusinessError("STALE","版本已更新，请刷新",409)
    return get(ident)

def save_correction(order):
    payload=json.dumps(order,ensure_ascii=False)
    with connect() as db:
        db.execute(
            "INSERT INTO correction_orders(run_id,payload,updated) VALUES(?,?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload,updated=excluded.updated",
            (order["run_id"],payload,order["updated_at"]),
        )
    return order

def get_correction_by_run(ident):
    with connect() as db:
        row=db.execute("SELECT payload FROM correction_orders WHERE run_id=?",(ident,)).fetchone()
    return json.loads(row["payload"]) if row else None

def list_corrections():
    with connect() as db:
        rows=db.execute("SELECT run_id,payload,updated FROM correction_orders ORDER BY updated DESC LIMIT 40").fetchall()
    return [json.loads(r["payload"]) for r in rows]
