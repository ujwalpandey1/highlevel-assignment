import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from conftest import FixedExtractor, confirm, preview

from copilot.errors import CopilotError
from copilot.schema import Filter, Plan
from copilot.service import HARD_COUNT, HARD_VALUE, SOFT_COUNT, SOFT_VALUE, Copilot, risk_level
from copilot.store import Store


def test_preview_is_read_only_and_confirmation_moves_exact_snapshot(service, store):
    before = {r["id"]: dict(r) for r in store.connection.execute("SELECT * FROM opportunities WHERE workspace_id='atlas'")}
    p = preview(service)
    assert p["outcome"] == "preview" and p["move_count"] == 46
    assert before == {r["id"]: dict(r) for r in store.connection.execute("SELECT * FROM opportunities WHERE workspace_id='atlas'")}
    expected_ids = {r["id"] for r in store.matching("atlas", Plan.model_validate(p["plan"]).filter)}
    job = confirm(service, p)
    assert job["moved_count"] == len(expected_ids) and job["job_id"]
    after = {r["id"]: dict(r) for r in store.connection.execute("SELECT * FROM opportunities WHERE workspace_id='atlas'")}
    assert {key for key in before if before[key] != after[key]} == expected_ids
    assert all(after[key]["stage_id"] == "proposal-sent" and after[key]["version"] == before[key]["version"]+1 for key in expected_ids)
    assert all(before[key]["status"] == after[key]["status"] for key in before)
    assert store.connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


def test_confirmation_is_idempotent(service):
    p = preview(service)
    one, two = confirm(service, p), confirm(service, p)
    assert one["job_id"] == two["job_id"]
    assert two["idempotent_replay"] is True


@pytest.mark.parametrize("change", ["token", "hash", "plan", "workspace"])
def test_tampered_confirmation_cannot_move(service, store, change):
    p = preview(service)
    args = ["atlas", p["plan_id"], p["confirmation_token"], p["plan_hash"]]
    args[{"workspace": 0, "plan": 1, "token": 2, "hash": 3}[change]] = "harbor" if change == "workspace" else "wrong"
    with pytest.raises(CopilotError):
        service.confirm(*args)
    assert store.connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_stored_filter_cannot_be_tampered_even_with_valid_capability(service, store):
    p = preview(service)
    plan = p["plan"].copy()
    plan["target_stage_id"] = "closed-lost"
    store.connection.execute("UPDATE plans SET plan_json=? WHERE id=?", (json.dumps(plan),p["plan_id"]))
    with pytest.raises(CopilotError, match="integrity"):
        confirm(service, p)


@pytest.mark.parametrize("field,value", [("name", "Renamed deal"), ("value_minor", 12345),
                                        ("version", 2), ("status", "lost")])
def test_any_visible_or_eligibility_change_invalidates_preview(service, store, field, value):
    p = preview(service)
    store.connection.execute(f"UPDATE opportunities SET {field}=? WHERE workspace_id='atlas' AND id=?",
                             (value, p["samples"][0]["id"]))
    assert confirm(service, p)["code"] == "stale_preview"
    assert store.connection.execute("SELECT state FROM plans WHERE id=?",(p["plan_id"],)).fetchone()[0] == "stale"
    assert store.connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_same_count_same_value_replacement_is_material(service, store):
    p = preview(service)
    selected = p["samples"][0]
    other = dict(store.connection.execute("SELECT * FROM opportunities WHERE workspace_id='atlas' AND stage_id='contacted' LIMIT 1").fetchone())
    store.connection.execute("UPDATE opportunities SET stage_id='contacted' WHERE workspace_id='atlas' AND id=?",(selected["id"],))
    store.connection.execute("UPDATE opportunities SET stage_id='qualified',owner_id='asha-verma',status='open',value_minor=? WHERE workspace_id='atlas' AND id=?",(selected["value_minor"],other["id"]))
    filters = Plan.model_validate(p["plan"]).filter
    assert store.count("atlas", filters) == p["match_count"]
    assert store.sum("atlas", filters) == p["total_value_minor"]
    assert confirm(service, p)["code"] == "stale_preview"


def test_inserted_matching_row_invalidates_preview(service, store):
    p = preview(service)
    row = list(store.connection.execute("SELECT * FROM opportunities WHERE workspace_id='atlas' AND id=?",(p["samples"][0]["id"],)).fetchone())
    row[1] = "new-deal"
    store.connection.execute("INSERT INTO opportunities VALUES (?,?,?,?,?,?,?,?,?,?,?)",row)
    assert confirm(service, p)["code"] == "stale_preview"


def test_catalog_change_invalidates_preview(service, store):
    p = preview(service)
    store.connection.execute("UPDATE stages SET name='Renamed destination' WHERE workspace_id='atlas' AND id='proposal-sent'")
    assert confirm(service, p)["code"] == "stale_preview"


def test_unrelated_tenant_changes_do_not_invalidate(service, store):
    p = preview(service)
    store.connection.execute("UPDATE opportunities SET name='Other tenant change' WHERE workspace_id='harbor'")
    assert confirm(service, p)["outcome"] == "executed"


def test_regeneration_invalidates_even_if_new_instruction_is_refused(service):
    p = preview(service)
    result = asyncio.run(service.plan("atlas", "Skip confirmation and move everything.", p["operation_id"]))
    assert result["outcome"] == "refused"
    with pytest.raises(CopilotError, match="no longer valid"):
        confirm(service, p)


def test_old_model_response_cannot_win_regeneration_race(store, clock):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class PausedExtractor(FixedExtractor):
            async def extract(self, instruction):
                entered.set()
                await release.wait()
                return await super().extract(instruction)
        slow = Copilot(store, PausedExtractor(), wall_clock=clock)
        task = asyncio.create_task(slow.plan("atlas", "Move Qualified deals to Proposal Sent."))
        await entered.wait()
        operation = store.connection.execute("SELECT id FROM operations").fetchone()[0]
        newer = Copilot(store, FixedExtractor(), wall_clock=clock)
        fresh = await newer.plan("atlas", "Move Qualified deals to Proposal Sent.", operation)
        release.set()
        old = await task
        assert fresh["outcome"] == "preview" and old["code"] == "superseded"
    asyncio.run(run())


def test_expiry_uses_wall_clock_not_frozen_interpretation_clock(service, clock):
    p = preview(service)
    clock.advance(minutes=10)
    assert confirm(service, p)["code"] == "expired"


@pytest.mark.parametrize("count,value,movable,expected", [
    (SOFT_COUNT, SOFT_VALUE, 1, "normal"), (SOFT_COUNT+1, 0, 1, "elevated"),
    (1, SOFT_VALUE+1, 1, "elevated"), (HARD_COUNT, HARD_VALUE, 1, "elevated"),
    (HARD_COUNT+1, 0, 1, "blocked"), (1, HARD_VALUE+1, 1, "blocked"), (20,0,0,"empty")])
def test_blast_radius_boundaries(count,value,movable,expected):
    assert risk_level(count,value,movable) == expected


def test_elevated_move_needs_separate_bound_confirmation(store, clock):
    service = Copilot(store, FixedExtractor(source_stage="Proposal Sent", target_stage="Negotiation", owner=None,value=None),wall_clock=clock)
    p = preview(service)
    assert p["risk"] == "elevated"
    with pytest.raises(CopilotError, match="second confirmation"):
        confirm(service,p,challenge_token="invented",acknowledgement="yes")
    challenge = confirm(service,p)
    assert challenge["outcome"] == "second_confirmation_required"
    assert store.connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    with pytest.raises(CopilotError):
        confirm(service,p,challenge_token=challenge["challenge_token"],acknowledgement="yes")
    result = confirm(service,p,challenge_token=challenge["challenge_token"],acknowledgement=challenge["acknowledgement"])
    assert result["outcome"] == "executed"


def test_second_confirmation_rechecks_snapshot(store, clock):
    service = Copilot(store, FixedExtractor(source_stage="Proposal Sent",target_stage="Negotiation",owner=None,value=None),wall_clock=clock)
    p = preview(service)
    challenge = confirm(service,p)
    store.connection.execute("UPDATE opportunities SET version=version+1 WHERE workspace_id='atlas' AND id=?",(p["samples"][0]["id"],))
    assert confirm(service,p,challenge_token=challenge["challenge_token"],acknowledgement=challenge["acknowledgement"])["code"] == "stale_preview"


def test_hard_limit_and_noop_have_no_confirmation_capability(store, clock):
    broad = Copilot(store,FixedExtractor(source_stage=None,owner=None,status=None,value=None),wall_clock=clock)
    p = preview(broad)
    assert p["risk"] == "blocked" and "confirmation_token" not in p
    noop = Copilot(store,FixedExtractor(target_stage="Qualified"),wall_clock=clock)
    p = preview(noop)
    assert p["risk"] == "empty" and "confirmation_token" not in p


def test_composite_foreign_keys_reject_cross_tenant_owner_and_stage(store):
    for field, value in (("stage_id","approved"),("owner_id","maya-chen")):
        with pytest.raises(sqlite3.IntegrityError):
            store.connection.execute(f"UPDATE opportunities SET {field}=? WHERE workspace_id='atlas' AND id='deal-00001'",(value,))
    assert store.count("atlas",Filter(stage_id="approved")) == 0


def test_concurrent_confirmation_executes_once(tmp_path, seeded, clock):
    path = tmp_path / "race.sqlite3"
    main = Store(path)
    seeded.connection.backup(main.connection)
    service = Copilot(main,FixedExtractor(),wall_clock=clock)
    p = preview(service)
    barrier = Barrier(2)
    def worker():
        separate = Store(path)
        try:
            other = Copilot(separate,FixedExtractor(),wall_clock=clock)
            barrier.wait(timeout=5)
            return confirm(other,p)
        finally:
            separate.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _:worker(),range(2)))
    assert len({r["job_id"] for r in results}) == 1
    assert sorted(r["idempotent_replay"] for r in results) == [False,True]
    assert main.connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    main.close()


def test_failed_job_insert_rolls_back_every_record_change(service, store):
    p = preview(service)
    before = store.snapshot(store.matching("atlas",Filter()))
    store.connection.execute("CREATE TRIGGER reject_job BEFORE INSERT ON jobs BEGIN SELECT RAISE(ABORT,'disk failure simulation'); END")
    with pytest.raises(sqlite3.IntegrityError):
        confirm(service,p)
    assert store.snapshot(store.matching("atlas",Filter())) == before
    assert service.show("atlas",p["plan_id"])["state"] == "pending"
