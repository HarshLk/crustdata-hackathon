"""Guard and vault: canary name/phone blocked everywhere, pseudonymised, vault reads logged."""
from __future__ import annotations

import pytest

from pdr import guard, vault
from pdr.contracts import GuardViolation

CANARY_NAME = "Canary Ramprasad Yadav"
CANARY_PHONE = "+91 94440 12321"
CANARY_LIC = "MH14 20110012345"


@pytest.fixture(autouse=True)
def _data(tmp_path, monkeypatch):
    monkeypatch.setenv("PDR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PDR_VAULT_SECRET", raising=False)
    return tmp_path


def _plant():
    return vault.put_driver("ACME", CANARY_NAME, CANARY_PHONE, CANARY_LIC)


@pytest.mark.parametrize("dest", ["crustdata_search", "crustdata_fetch", "crustdata_identify", "llm", "k7", "web"])
@pytest.mark.parametrize("value", [CANARY_NAME, "9444012321", "094440-12321", "MH1420110012345"])
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
    txt = f"Driver {CANARY_NAME} (ph 94440-12321, DL MH-14-2011-0012345) reported bandh; other driver ph 9000012345; advice no 9876500000"
    out = guard.pseudonymise(txt, "ACME")
    assert "Ramprasad" not in out and "12321" not in out and "0012345" not in out and "9000012345" not in out
    assert ref in out and "advice no 9876500000" in out
    guard.check("llm", out)


def test_driver_ref_stable_and_vault_read_logged(_data):
    a = _plant()
    b = vault.put_driver("ACME", None, "9444012321")
    assert a == b and len(a) == 12
    row = guard.vault_read(a, "kam", "dispute letter annex")
    assert row["name"] == CANARY_NAME
    log = vault.access_log()
    assert log[-1]["who"] == "kam" and log[-1]["purpose"] == "dispute letter annex"
    assert (_data / "vault.db").exists()


def test_ten_digit_business_refs_pass_k7_and_llm():
    _plant()
    letter = ("We dispute deduction under payment advice no. 9876543210 line 1, LR 8123456789, "
              "e-way bill 391234567890, vehicle MH12AB1234, action ACME|PA1|1:6a3f9e2c1b7d.")
    for dest in ("k7", "llm"):
        guard.check(dest, letter)
        guard.check(dest, {"advice_no": "7012345678", "body": "Advice 7012345678 is disputed."})
        guard.check(dest, "Please quote 9988776655 in your reply.", allow_refs=["9988776655"])


@pytest.mark.parametrize("txt", ["Driver mob 9812345678", "contact: 98123 45678", "+91 9812345678", "call 09812345678",
                                 "whatsapp 9812345678 for pod"])
def test_phone_with_context_blocks(txt):
    for dest in ("k7", "llm"):
        with pytest.raises(GuardViolation):
            guard.check(dest, txt)


def test_canary_phone_blocks_even_as_a_ref():
    _plant()
    for dest in ("k7", "llm", "crustdata_search", "crustdata_identify", "crustdata_fetch", "web"):
        with pytest.raises(GuardViolation):
            guard.check(dest, {"advice_no": "9444012321", "body": "advice 9444012321"}, "ACME", allow_refs=["9444012321"])
