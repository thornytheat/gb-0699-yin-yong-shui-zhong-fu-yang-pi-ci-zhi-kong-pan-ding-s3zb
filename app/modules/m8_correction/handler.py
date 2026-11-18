from fastapi import APIRouter, Body

from app import store
from app.errors import BusinessError, object_payload
from app.modules.m8_correction import service

router = APIRouter()


def _run(ident):
    if not isinstance(ident, str) or not ident:
        raise BusinessError("RUN_ID", "运行编号须为文本")
    return store.get(ident)


@router.post("/api/8/correction")
def create_correction(payload: object = Body(...)):
    """为指定运行生成污染更正单；重复调用返回原单，不新增。"""
    payload = object_payload(payload)
    run = _run(payload.get("id"))
    order, created = service.create_correction(run, store)
    return {"created": created, "order": order, "receipt": order["original_receipt"]}


@router.post("/api/8/retry")
def retry_correction(payload: object = Body(...)):
    """从最后确认批次续跑；同一空白重试复用同一更正单。"""
    payload = object_payload(payload)
    run = _run(payload.get("id"))
    override = payload.get("override")
    fail_at = payload.get("fail_at_batch")
    if fail_at is not None and type(fail_at) is not int:
        raise BusinessError("FAIL_AT", "失败批次序号须为整数")
    order, progressed = service.retry_correction(
        run, store, override=override, fail_at_batch=fail_at,
    )
    return {
        "progressed": progressed,
        "state": order["state"],
        "resume_cursor": order["resume_cursor"],
        "order": order,
        "receipt": order["original_receipt"],
    }


@router.get("/api/8/order/{ident}")
def get_order(ident: str):
    order = store.get_correction_by_run(ident)
    if order is None:
        raise BusinessError("NO_ORDER", "该批次尚无污染更正单", 404)
    return {"order": order, "receipt": order["original_receipt"]}
