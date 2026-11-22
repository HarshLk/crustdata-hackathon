"""Guard and vault: canary name/phone blocked everywhere, pseudonymised, vault reads logged."""
from __future__ import annotations

import pytest

from pdr import guard, vault
from pdr.contracts import GuardViolation

CANARY_NAME = "Canary Ramprasad Yadav"
CANARY_PHONE = "+91 98765 43210"
CANARY_LIC = "MH14 20110012345"


@pytest.fixture(autouse=True)
def _data(tmp_path, monkeypatch):
    monkeypatch.setenv("PDR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PDR_VAULT_SECRET", raising=False)
    return tmp_path


def _plant():
    return vault.put_driver("ACME", CANARY_NAME, CANARY_PHONE, CANARY_LIC)


@pytest.mark.parametrize("dest", ["crustdata_search", "crustdata_fetch", "crustdata_identify", "llm", "k7", "web"])
@pytest.mark.parametrize("value", [CANARY_NAME, "9876543210", "098765-43210", "MH1420110012345"])
def test_canary_blocked_for_every_destination(dest, value):
    _plant()
    with pytest.raises(GuardViolation):
        guard.check(dest, {"query": f"https://x.in/{value}"} if "fetch" in dest or dest == "web" else value, "ACME")


def test_canary_blocked_inside_nested_payload():
    _plant()
    with pytest.raises(GuardViolation) as e:
        guard.check("llm", {"messages": [{"content": f"driver canary ramprasad   yadav said hi"}]})
    assert "ramprasad" not in str(e.value).lower()


def test_unvaulted_phone_and_licence_patterns_blocked_for_llm_and_k7():
    for dest in ("llm", "k7"):
        with pytest.raises(GuardViolation):
            guard.check(dest, "call 9123456780 now")
        with pytest.raises(GuardViolation):
            guard.check(dest, "DL no KA0520190001234")


def test_allowed_payloads_pass():
    _plant()
    guard.check("crustdata_search", {"query": "Nashik bandh", "start": "2026-10-01"})
    guard.check("crustdata_search", "chakka jam Maharashtra 2026")
    guard.check("crustdata_identify", ["Acme Foods Private Limited"])
    guard.check("crustdata_fetch", ["https://www.thehindu.com/news/bandh-nashik"])
    guard.check("llm", "Clause 15: penalty @ 0.5% per week of delayed quantity value, Rs 12,000.")
    guard.check("k7", "We dispute deduction PA/2026/0412 line 3 of Rs 12,000 for LR-88121.")


@pytest.mark.parametrize("q", ["Nashik bandh 12,000", "LR-88121 delay", "penalty Acme Nashik",
                               "one two three four five six seven eight nine ten eleven", "bandh 1234567"])
def test_search_rules(q):
    with pytest.raises(GuardViolation):
        guard.check("crustdata_search", q)


@pytest.mark.parametrize("n", ["Acme Foods deduction Rs 12000", "Acme invoice INV-2231", "acme@x.com"])
def test_identify_rules(n):
    with pytest.raises(GuardViolation):
        guard.check("crustdata_identify", n)


def test_pseudonymise_replaces_canary():
    ref = _plant()
    txt = f"Driver {CANARY_NAME} (ph 98765-43210, DL MH-14-2011-0012345) reported bandh; other 9000012345"
    out = guard.pseudonymise(txt, "ACME")
    assert "Ramprasad" not in out and "43210" not in out and "0012345" not in out and "9000012345" not in out
    assert ref in out
    guard.check("llm", out)


def test_driver_ref_stable_and_vault_read_logged(_data):
    a = _plant()
    b = vault.put_driver("ACME", None, "9876543210")
    assert a == b and len(a) == 12
    row = guard.vault_read(a, "kam", "dispute letter annex")
    assert row["name"] == CANARY_NAME
    log = vault.access_log()
    assert log[-1]["who"] == "kam" and log[-1]["purpose"] == "dispute letter annex"
    assert (_data / "vault.db").exists()
