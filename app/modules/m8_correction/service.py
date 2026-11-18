from datetime import datetime, timezone
from decimal import Decimal

from app.errors import BusinessError
from app.modules.m8_correction.rules import shared_pollution

# 更正处置动作的有限集合（由系统按复核结论派生，不接受外部任意值）。
DISPOSITIONS = {"隔离关联批次", "复测后重判", "作废重抽"}
# override.value_mg 允许的浓度表达类型。
_VALUE_TYPES = (str, int, Decimal)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _snapshot_receipt(run):
    """冻结原回执与原复核意见，后续任何更正都不得覆盖该快照。"""
    result = run["result"]
    return {
        "run_id": run["id"],
        "digest": run["digest"],
        "rule_version": run.get("rule_version", result.get("rule_version")),
        "revision": run["revision"],
        "decision": run["decision"],
        "balance": result.get("balance"),
        "released": [dict(r) for r in result.get("released", [])],
    }


def _affected_items(groups):
    items = []
    for g in groups:
        for batch in g["batches"]:
            items.append({
                "batch": batch,
                "analyte": g["analyte"],
                "blank_id": g["blank_id"],
                "value_mg": g["value_mg"],
                "mdl": g["mdl"],
                "status": "PENDING",
                "disposition": "隔离关联批次",
                "attempts": 0,
            })
    # 批次+检测项去重并稳定排序，作为续跑的确定顺序。
    dedup = {(i["batch"], i["analyte"]): i for i in items}
    return [dedup[k] for k in sorted(dedup)]


def build_order(run, groups):
    return {
        "id": "CO-" + run["id"][:12],
        "run_id": run["id"],
        "created_at": _now(),
        "updated_at": _now(),
        "state": "PROCESSING",
        "resume_cursor": 0,
        "failure": None,
        "original_receipt": _snapshot_receipt(run),
        "pause_reason": "共享空白污染复检超标，关联批次放行暂停",
        "groups": [dict(g) for g in groups],
        "affected": _affected_items(groups),
    }


def _validate_override(override):
    if override is None:
        return None
    if not isinstance(override, dict):
        raise BusinessError("OVERRIDE", "空白结果须为对象")
    status = override.get("status")
    if status not in {"PASS", "BLANK_FAIL"}:
        raise BusinessError("OVERRIDE", "空白复核结论仅支持 PASS 或 BLANK_FAIL")
    value = override.get("value_mg")
    if value is not None and not isinstance(value, _VALUE_TYPES):
        raise BusinessError("OVERRIDE", "空白浓度格式无效")
    return {"status": status, "value_mg": value}


def _validate_fail_at(fail_at_batch, total):
    if fail_at_batch is None:
        return None
    if type(fail_at_batch) is not int or fail_at_batch < 1 or fail_at_batch > total:
        raise BusinessError(
            "FAIL_AT", f"失败批次序号须在 1..{total} 之间",
        )
    return fail_at_batch


def create_correction(run, store):
    """为运行创建更正单；同一运行重复请求复用原单，绝不新增。"""
    existing = store.get_correction_by_run(run["id"])
    if existing is not None:
        return existing, False
    groups = shared_pollution(run["result"].get("blank_checks", []))
    if not groups:
        raise BusinessError("NO_POLLUTION", "未发现跨批次共用的超标空白，无需更正", 422)
    order = build_order(run, groups)
    store.save_correction(order)
    return store.get_correction_by_run(run["id"]), True


def retry_correction(run, store, override=None, fail_at_batch=None):
    """从最后确认的批次续跑；同一空白的重试不新增更正单。"""
    order = store.get_correction_by_run(run["id"])
    if order is None:
        raise BusinessError("NO_ORDER", "该批次尚无污染更正单", 404)
    if order["state"] == "COMPLETED":
        return order, False
    override = _validate_override(override)
    affected = order["affected"]
    total = len(affected)
    fail_at = _validate_fail_at(fail_at_batch, total)
    start = order["resume_cursor"]

    order["updated_at"] = _now()
    order["failure"] = None
    progressed = False
    for idx in range(start, total):
        item = affected[idx]
        item["attempts"] += 1
        # 注入失败：仅在尚未确认的批次上触发，已确认批次不重跑。
        if fail_at == idx + 1:
            item["status"] = "FAILED"
            order["state"] = "FAILED"
            order["resume_cursor"] = idx
            order["failure"] = {
                "batch": item["batch"],
                "analyte": item["analyte"],
                "message": "更正执行失败，已保留进度，等待续跑",
                "at": _now(),
            }
            store.save_correction(order)
            return store.get_correction_by_run(run["id"]), progressed
        item["status"] = "CONFIRMED"
        if override is not None:
            item["override"] = override
            item["disposition"] = "复测后重判" if override["status"] == "PASS" else "作废重抽"
        order["resume_cursor"] = idx + 1
        progressed = True

    order["state"] = "COMPLETED"
    order["updated_at"] = _now()
    store.save_correction(order)
    return store.get_correction_by_run(run["id"]), progressed
