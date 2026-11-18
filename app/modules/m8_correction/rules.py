from app.report import report

# 污染更正仅针对“同一空白样被多个检测批共用”的情形：
# 以 (空白编号, 检测项) 归并超标空白，命中两个及以上不同检测批才构成共享污染。
SHARED_MIN_BATCHES = 2


def shared_pollution(blank_checks):
    """把超标空白按 (blank_id, analyte) 归并，返回被多个检测批共用的污染组。"""
    groups = {}
    for row in blank_checks:
        if row.get("status") != "BLANK_FAIL":
            continue
        key = (row["id"], row["analyte"])
        groups.setdefault(key, []).append(row)
    out = []
    for (blank_id, analyte), rows in sorted(groups.items()):
        batches = sorted({r["batch"] for r in rows})
        if len(batches) < SHARED_MIN_BATCHES:
            continue
        sample = rows[0]
        out.append({
            "blank_id": blank_id,
            "analyte": analyte,
            "mdl": sample["mdl"],
            "value_mg": sample["value_mg"],
            "batches": batches,
            "batch_count": len(batches),
            "lines": sorted({r["line"] for r in rows}),
            "reason": "BLANK_FAIL",
        })
    return out


def apply(ctx):
    out = shared_pollution(ctx.get("blank_checks", []))
    ctx["pollution_groups"] = out
    return report(
        out,
        共享污染空白=len(out),
        关联检测批=sum(g["batch_count"] for g in out),
    )
