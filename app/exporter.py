import csv,io,json
from app import store

def evidence_csv(run):
    out=io.StringIO();writer=csv.writer(out)
    writer.writerow(["run_id","sha256","rule_version","kind","line","reason","evidence"])
    writer.writerow([run["id"],run["digest"],run["rule_version"],"manifest","","",json.dumps({"balance":run["result"]["balance"],"decision":run["decision"],"revision":run["revision"]},ensure_ascii=False)])
    order=store.get_correction_by_run(run["id"])
    if order:
        receipt=order["original_receipt"]
        writer.writerow([run["id"],run["digest"],run["rule_version"],"original_receipt","","",json.dumps({"revision":receipt["revision"],"decision":receipt["decision"],"released":receipt["released"],"balance":receipt["balance"]},ensure_ascii=False)])
        writer.writerow([run["id"],run["digest"],run["rule_version"],"correction_order",order["id"],"",json.dumps({"state":order["state"],"pause_reason":order["pause_reason"],"resume_cursor":order["resume_cursor"],"failure":order["failure"]},ensure_ascii=False)])
        for item in order["affected"]:
            writer.writerow([run["id"],run["digest"],run["rule_version"],"correction_batch",item.get("line",""),item["status"],json.dumps(item,ensure_ascii=False)])
    for row in run["result"].get("final",[]):
        writer.writerow([run["id"],run["digest"],run["rule_version"],"result",row.get("line",""),row.get("status",""),json.dumps(row,ensure_ascii=False)])
    for row in run["result"].get("quarantine",[]):
        writer.writerow([run["id"],run["digest"],run["rule_version"],"quarantine",row.get("line",""),row["reason"],json.dumps(row,ensure_ascii=False)])
    return "\ufeff"+out.getvalue()
