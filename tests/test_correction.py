import pytest

from app.pipeline import run
from app import store
from app.errors import BusinessError
from app.exporter import evidence_csv
from app.modules.m8_correction.rules import shared_pollution
from app.modules.m8_correction import service

HEADER = "id,batch,analyte,kind,pair,value,spike,unit,version\n"


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    # 每个测试使用独立 SQLite，避免相同输入复用更正单而互相污染。
    # store.py 以 from app.config import DATA_DIR 按值绑定，须直接替换其引用。
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "data")
    return store


def _shared_text():
    # 同一空白 BLK9 被 B01、B02 两个检测批共用，且浓度 0.002 高于 MDL 0.001。
    return HEADER + (
        "BLK9,B01,Pb,blank,,0.002,0,mg/L,1\n"
        "BLK9,B02,Pb,blank,,0.002,0,mg/L,1\n"
    )


def _single_text():
    # 仅单批次超标，不构成共享污染。
    return HEADER + "BLK9,B01,Pb,blank,,0.002,0,mg/L,1\n"


@pytest.fixture
def shared_run(isolated_store):
    text = _shared_text()
    return isolated_store.save(text, run(text))


# ---------- 规则层 ----------

def test_shared_pollution_groups_batches():
    result = run(_shared_text())
    groups = shared_pollution(result["blank_checks"])
    assert len(groups) == 1
    g = groups[0]
    assert g["blank_id"] == "BLK9" and g["analyte"] == "Pb"
    assert g["batches"] == ["B01", "B02"] and g["batch_count"] == 2


def test_single_batch_pollution_not_shared():
    result = run(_single_text())
    assert shared_pollution(result["blank_checks"]) == []


def test_pipeline_has_eight_reports():
    result = run(_shared_text())
    assert len(result["reports"]) == 8
    assert result["reports"][7]["metrics"]["共享污染空白"] == 1


# ---------- 服务层：建单与原回执保留 ----------

def test_create_order_snapshots_original_receipt(isolated_store, shared_run):
    order, created = service.create_correction(shared_run, store)
    assert created is True
    receipt = order["original_receipt"]
    assert receipt["run_id"] == shared_run["id"]
    assert receipt["decision"] == shared_run["decision"]
    assert receipt["revision"] == shared_run["revision"]
    assert {r["batch"] for r in receipt["released"]} == {"B01", "B02"}
    assert order["state"] == "PROCESSING"
    assert len(order["affected"]) == 2
    assert {i["batch"] for i in order["affected"]} == {"B01", "B02"}
    assert all(i["disposition"] == "隔离关联批次" for i in order["affected"])


def test_create_order_idempotent_no_duplicate(isolated_store, shared_run):
    first, created1 = service.create_correction(shared_run, store)
    second, created2 = service.create_correction(shared_run, store)
    assert created1 is True and created2 is False
    assert first["id"] == second["id"]
    assert store.list_corrections() and len(store.list_corrections()) >= 1


def test_create_order_rejects_non_shared(isolated_store):
    text = _single_text()
    lone = store.save(text, run(text))
    with pytest.raises(BusinessError, match="无需更正"):
        service.create_correction(lone, store)


def test_original_receipt_never_overwritten(isolated_store, shared_run):
    order, _ = service.create_correction(shared_run, store)
    before = order["original_receipt"]
    service.retry_correction(shared_run, store)
    again = store.get_correction_by_run(shared_run["id"])
    assert again["original_receipt"] == before
    assert again["state"] == "COMPLETED"


# ---------- 续跑、失败与重试不新增 ----------

def test_retry_completes_all_batches(isolated_store, shared_run):
    service.create_correction(shared_run, store)
    order, progressed = service.retry_correction(shared_run, store)
    assert progressed is True
    assert order["state"] == "COMPLETED"
    assert order["resume_cursor"] == len(order["affected"])
    assert all(i["status"] == "CONFIRMED" for i in order["affected"])


def test_failure_resumes_from_last_confirmed(isolated_store, shared_run):
    service.create_correction(shared_run, store)
    order, _ = service.retry_correction(shared_run, store, fail_at_batch=2)
    assert order["state"] == "FAILED"
    assert order["resume_cursor"] == 1
    assert order["failure"]["batch"] == "B02"
    statuses = {i["batch"]: i["status"] for i in order["affected"]}
    assert statuses == {"B01": "CONFIRMED", "B02": "FAILED"}

    # 续跑：已确认批次不重跑（B01 attempts 维持 1），从最后确认批次之后继续，
    # 失败的 B02 在新一次续跑中重试，故累计两次尝试。
    resumed, progressed = service.retry_correction(shared_run, store)
    assert progressed is True
    assert resumed["state"] == "COMPLETED"
    by_batch = {i["batch"]: i for i in resumed["affected"]}
    assert by_batch["B01"]["attempts"] == 1
    assert by_batch["B02"]["attempts"] == 2
    assert by_batch["B02"]["status"] == "CONFIRMED"


def test_retry_same_blank_keeps_single_order(isolated_store, shared_run):
    first, _ = service.create_correction(shared_run, store)
    service.retry_correction(shared_run, store, fail_at_batch=1)
    service.retry_correction(shared_run, store)
    again = store.get_correction_by_run(shared_run["id"])
    assert again["id"] == first["id"]
    assert again["state"] == "COMPLETED"


def test_retry_completed_is_noop(isolated_store, shared_run):
    service.create_correction(shared_run, store)
    service.retry_correction(shared_run, store)
    order, progressed = service.retry_correction(shared_run, store)
    assert progressed is False and order["state"] == "COMPLETED"


def test_fail_at_bounds(isolated_store, shared_run):
    service.create_correction(shared_run, store)
    with pytest.raises(BusinessError):
        service.retry_correction(shared_run, store, fail_at_batch=99)


def test_override_validation(isolated_store, shared_run):
    service.create_correction(shared_run, store)
    with pytest.raises(BusinessError):
        service.retry_correction(shared_run, store, override={"status": "WEIRD"})


def test_retry_with_pass_override_records_disposition(isolated_store, shared_run):
    service.create_correction(shared_run, store)
    order, _ = service.retry_correction(
        shared_run, store, override={"status": "PASS", "value_mg": "0.0005"},
    )
    assert order["state"] == "COMPLETED"
    assert all(i["disposition"] == "复测后重判" for i in order["affected"])
    assert all(i["override"]["status"] == "PASS" for i in order["affected"])


# ---------- 放行暂停与恢复 ----------

def test_review_paused_while_correction_open(isolated_store, shared_run):
    service.create_correction(shared_run, store)
    with pytest.raises(BusinessError, match="放行暂停"):
        store.review(shared_run["id"], shared_run["revision"], "通过")
    # 退回同样被暂停，暂停期间不得产生新的复核修订。
    with pytest.raises(BusinessError, match="放行暂停"):
        store.review(shared_run["id"], shared_run["revision"], "退回")
    assert store.get(shared_run["id"])["revision"] == shared_run["revision"]


def test_review_blocked_after_default_isolation_completed(isolated_store, shared_run):
    service.create_correction(shared_run, store)
    service.retry_correction(shared_run, store)  # 默认“隔离关联批次”，未复测 PASS
    updated = store.get(shared_run["id"])
    with pytest.raises(BusinessError, match="阻断项"):
        store.review(updated["id"], updated["revision"], "通过")


def test_review_resumes_after_pass_rejudge_completed(isolated_store, shared_run):
    service.create_correction(shared_run, store)
    service.retry_correction(shared_run, store, override={"status": "PASS", "value_mg": "0.0005"})
    updated = store.get(shared_run["id"])
    reviewed = store.review(updated["id"], updated["revision"], "通过")
    assert reviewed["decision"] == "通过"
    # 原回执快照中的阻断结论仍被保留，未被新复核覆盖。
    order = store.get_correction_by_run(shared_run["id"])
    assert {r["status"] for r in order["original_receipt"]["released"]} == {"BLANK_FAIL"}


# ---------- 证据导出 ----------

def test_evidence_contains_receipt_batches_and_result(isolated_store, shared_run):
    service.create_correction(shared_run, store)
    service.retry_correction(shared_run, store, fail_at_batch=2)
    text = evidence_csv(store.get(shared_run["id"]))
    assert "original_receipt" in text
    assert "correction_order" in text
    assert "correction_batch" in text
    assert "B01" in text and "B02" in text
    assert shared_run["digest"] in text
