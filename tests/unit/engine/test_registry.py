"""Registry and fail-closed barrier."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pdr.codes import CheckStatus, Reason, Stage
from pdr.contracts import CheckContext
from pdr.engine import registry
from pdr.engine.registry import check, checks_for, run_check, settings_for_stage
from pdr.models import CheckResult, Deduction


def _ctx():
    d = Deduction(deduction_id="D1", dispute_key="X|1|1", event_fp="f", client_id="X", client_name_raw="X",
                  deduction_date=date(2026, 11, 1), amount=Decimal("100"), reason_text="late",
                  reason_code="late_delivery")
    return CheckContext(deduction=d, trip=None, client=None, agreement=None, terms=[], prior={}, ctx=None)


def test_register_and_list():
    @check(stage=Stage.terms_checked, needs_terms=["cap"], settings=["zz_test_section"], check_id="t_reg_ok")
    def ok(c):
        return CheckResult(check_id="t_reg_ok", stage=Stage.terms_checked, status=CheckStatus.NO_GROUND)

    ids = [s.check_id for s in checks_for("terms_checked")]
    assert "t_reg_ok" in ids
    assert "zz_test_section" in settings_for_stage(Stage.terms_checked)
    assert run_check(ok, _ctx())[0].status == CheckStatus.NO_GROUND
    registry._REGISTRY.pop("t_reg_ok")


def test_barrier_converts_exception():
    @check(stage=Stage.records_checked, check_id="t_reg_boom")
    def boom(c):
        raise ZeroDivisionError("x")

    out = run_check(boom, _ctx())
    registry._REGISTRY.pop("t_reg_boom")
    assert len(out) == 1
    r = out[0]
    assert r.status == CheckStatus.INCOMPLETE and r.reason == Reason.INTERNAL_ERROR
    assert r.missing_item == "check t_reg_boom failed: ZeroDivisionError"


def test_barrier_rejects_bad_return():
    @check(stage=Stage.records_checked, check_id="t_reg_bad")
    def bad(c):
        return "GROUND"

    out = run_check(bad, _ctx())
    registry._REGISTRY.pop("t_reg_bad")
    assert out[0].reason == Reason.INTERNAL_ERROR


def test_list_return_and_none():
    @check(stage=Stage.records_checked, check_id="t_reg_list")
    def many(c):
        return [CheckResult(check_id="a", stage=Stage.records_checked, status=CheckStatus.NOT_APPLICABLE)] * 2

    assert len(run_check(many, _ctx())) == 2
    registry._REGISTRY.pop("t_reg_list")
