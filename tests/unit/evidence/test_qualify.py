"""A8: candidates, verbatim quotes, place/region/highway rules, dates, status, contradiction."""
from __future__ import annotations

from datetime import datetime

import pytest

from pdr.contracts import EvidenceRequest
from pdr.evidence.candidates import affected_place, candidate_sentences, dates_in, propose
from pdr.evidence.pagecheck import check_page, snapshot
from pdr.evidence.qualify import assess
from pdr.llm import Claim, PassageProposal

from .conftest import IST, make_item, raw_item

KINI_W = (datetime(2026, 11, 4, 8, tzinfo=IST), datetime(2026, 11, 5, 20, tzinfo=IST))
PUNE_KOLHAPUR = dict(route_places=["Pune", "Satara", "Karad", "Kini", "Kolhapur"], route_states=["Maharashtra"])
PUNE_MUMBAI = dict(route_places=["Pune", "Lonavala", "Khopoli", "Panvel", "Navi Mumbai", "Mumbai"],
                   route_states=["Maharashtra"])
ODISHA_W = (datetime(2026, 10, 8, 6, tzinfo=IST), datetime(2026, 10, 8, 20, tzinfo=IST))
CUTTACK_BBSR = dict(route_places=["Cuttack", "Bhubaneswar"], route_states=["Odisha"])


def _page(item, settings, store):
    p = check_page(item, settings)
    assert not p.blocked, p.reason
    snapshot(p, store)
    return p


def _run(page, req, settings, proposals=None):
    props = proposals if proposals is not None else propose(
        page.text, page.title, page.published_at, list(req.route_places), list(req.route_states),
        list(req.event_types), settings)
    return assess(props, page, req, settings)


@pytest.fixture
def kini(settings, ctx):
    return _page(raw_item("fetch_domains_1.json", "outlookindia.com"), settings, ctx.store)


def test_kini_supports_pune_kolhapur(kini, settings):
    req = EvidenceRequest(window=KINI_W, event_types=["blockade"], **PUNE_KOLHAPUR)
    a = _run(kini, req, settings)
    assert len(a.supports) == 1
    ev = a.supports[0]
    assert ev.stance == "supports" and ev.place_named in ("Satara", "Kolhapur")
    assert "Nagthane in Satara district" in ev.passage and ev.passage in kini.text
    assert ev.published_at == kini.published_at and ev.content_hash == kini.content_hash
    assert ev.link.startswith(kini.url + "#:~:text=The%20blockade%20has%20disrupted")
    assert a.interval[0] <= KINI_W[1] and a.interval[1] >= KINI_W[0]


def test_kini_rejected_for_pune_mumbai(kini, settings):
    """The 'Pune-Bengaluru highway' never matches a Pune->Mumbai trip (D-10, case 3)."""
    req = EvidenceRequest(window=KINI_W, event_types=["blockade"], **PUNE_MUMBAI)
    a = _run(kini, req, settings)
    assert a.supports == [] and a.reasons == ["place_not_on_route"]


def test_highway_name_and_direction_are_not_places():
    s = "Traffic on the Pune-Bengaluru highway was blocked as vehicles travelling from Pune towards Kolhapur were stranded."
    assert not affected_place(s, "Pune", ["Pune", "Kolhapur"])
    assert not affected_place(s, "Kolhapur", ["Pune", "Kolhapur"])
    assert affected_place("Trucks were stranded near Kini toll plaza in Kolhapur district.", "Kolhapur")
    assert affected_place("Roads were blocked in Bhubaneswar, Cuttack and Puri.", "Cuttack")


def test_kini_out_of_window(kini, settings):
    req = EvidenceRequest(window=(datetime(2026, 10, 27, 8, tzinfo=IST), datetime(2026, 10, 29, 20, tzinfo=IST)),
                          event_types=["blockade"], **PUNE_KOLHAPUR)
    assert _run(kini, req, settings).reasons == ["out_of_window"]


def test_event_type_not_covered(kini, settings):
    req = EvidenceRequest(window=KINI_W, event_types=["flood"], **PUNE_KOLHAPUR)
    assert _run(kini, req, settings).supports == []


def test_shrirampur_maharashtra_rejected_for_srirampur_west_bengal(settings, ctx):
    item = make_item("https://news.example/shrirampur", "Farmers block roads in Ahilyanagar",
                     ["Srirampur, Oct 20: Hundreds of trucks were stranded at Srirampur in Ahilyanagar district of "
                      "Maharashtra on Tuesday as farmers staged a blockade over onion prices.",
                      "Traffic on the state highway remained blocked for six hours."],
                     "2026-10-20T18:00:00+05:30")
    page = _page(item, settings, ctx.store)
    req = EvidenceRequest(route_places=["Guwahati", "Srirampur", "Siliguri"], route_states=["Assam", "West Bengal"],
                          window=(datetime(2026, 10, 19, tzinfo=IST), datetime(2026, 10, 21, tzinfo=IST)),
                          event_types=["blockade"])
    a = _run(page, req, settings)
    assert a.supports == [] and a.reasons == ["region_not_named"]


def test_announcement_only_rejected(settings, ctx):
    page = _page(raw_item("fetch_domains_0.json", "odishatv.in"), settings, ctx.store)
    req = EvidenceRequest(window=ODISHA_W, event_types=["bandh"], **CUTTACK_BBSR)
    a = _run(page, req, settings)
    assert a.supports == [] and a.reasons == ["announced"]


def test_called_off_rejected(settings, ctx):
    item = make_item("https://news.example/off", "Odisha bandh called off",
                     ["Bhubaneswar, Oct 7: The transporters' bandh was called off late on Wednesday after talks.",
                      "Trucks were stranded in Cuttack for some hours before the bandh was withdrawn."],
                     "2026-10-08T07:00:00+05:30")
    page = _page(item, settings, ctx.store)
    req = EvidenceRequest(window=ODISHA_W, event_types=["bandh"], **CUTTACK_BBSR)
    a = _run(page, req, settings)
    assert a.supports == [] and a.reasons == ["called_off"]


def test_odisha_bandh_supports_cuttack_bhubaneswar(settings, ctx):
    page = _page(raw_item("fetch_domains_0.json", "theprint.in"), settings, ctx.store)
    req = EvidenceRequest(window=ODISHA_W, event_types=["bandh"], **CUTTACK_BBSR)
    a = _run(page, req, settings)
    assert a.supports and a.supports[0].place_named in ("Cuttack", "Bhubaneswar")
    assert a.supports[0].event_from.isoformat() == "2026-10-08"


def test_statewide_report_needs_a_statewide_stop_quote(settings, ctx):
    page = _page(raw_item("fetch_domains_1.json", "assamtribune.com"), settings, ctx.store)
    req = EvidenceRequest(window=ODISHA_W, event_types=["bandh"], route_places=["Jharsuguda"], route_states=["Odisha"])
    a = _run(page, req, settings)
    assert a.supports and a.supports[0].place_named == "Odisha"
    assert "across the state" in a.supports[0].passage


def test_unaffected_sentence_gives_contradiction(settings, ctx):
    item = make_item("https://news.example/police", "Bandh: police say highways open",
                     ["Bhubaneswar, Oct 8: Police said traffic on the national highway between Bhubaneswar and "
                      "Cuttack remained unaffected during the bandh on Thursday.",
                      "Commissioner said vehicles plied normally in Cuttack."],
                     "2026-10-08T15:00:00+05:30")
    page = _page(item, settings, ctx.store)
    req = EvidenceRequest(window=ODISHA_W, event_types=["bandh"], **CUTTACK_BBSR)
    a = _run(page, req, settings)
    assert a.supports == []
    assert a.contradicts and all(c.stance == "contradicts" for c in a.contradicts)
    assert any("remained unaffected" in c.passage and c.place_named in ("Cuttack", "Bhubaneswar") for c in a.contradicts)
    assert any("plied normally in Cuttack" in c.passage for c in a.contradicts)


def test_model_proposal_with_non_verbatim_quote_discarded(kini, settings):
    req = EvidenceRequest(window=KINI_W, event_types=["blockade"], **PUNE_KOLHAPUR)
    good = propose(kini.text, kini.title, kini.published_at, list(req.route_places), list(req.route_states),
                   list(req.event_types), settings)[0]
    bad = good.model_copy(update={"places_affected": [Claim(value="Kini", quote="Trucks were stranded at Kini toll plaza in Kolhapur district.")]})
    a = _run(kini, req, settings, proposals=[bad])
    assert a.supports == [] and a.reasons == ["quote_not_verbatim"]


def test_model_cannot_claim_a_highway_name_as_place(kini, settings):
    req = EvidenceRequest(window=KINI_W, event_types=["blockade"], **PUNE_MUMBAI)
    q = next(s for s in kini.text.split("\n") if s.startswith("The Pune-Bengaluru National Highway (NH-48) remained"))
    p = PassageProposal(event_type="blockade", status="happened", places_affected=[Claim(value="Pune", quote=q)],
                        event_from="2026-11-03", event_to="2026-11-05", effect_quote=q)
    assert _run(kini, req, settings, proposals=[p]).supports == []


def test_candidates_need_place_and_event_word(kini):
    c = candidate_sentences(kini.text, ["Satara"], [], ["blockade"])
    assert c and all("Satara" in s and "blockade" in s.lower() for s in c)
    assert candidate_sentences(kini.text, ["Lonavala"], [], ["blockade"]) == []


def test_dates_resolved_against_publication():
    from datetime import date
    pub = date(2026, 11, 6)  # a Friday
    assert set(dates_in("remained blocked for a third consecutive day on Thursday", pub)) == {date(2026, 11, 3), date(2026, 11, 5)}
    assert dates_in("Bhubaneswar, Oct 8 (PTI) roads blocked", date(2026, 10, 8)) == [date(2026, 10, 8)]
    assert dates_in("on 8 October 2026 trucks stopped", date(2026, 10, 9)) == [date(2026, 10, 8)]


def test_detention_wording_is_an_effect_on_goods_movement():
    from pdr.evidence.candidates import has_effect
    assert has_effect("The vehicles have reportedly been held around Srirampur and Bakshirhat since September 17.")
    assert has_effect("Over 300 trucks were detained at the check post.")
    assert not has_effect("The meeting was held at Srirampur.")


def test_since_a_date_runs_to_publication_and_including_list_is_locative():
    from datetime import date
    assert set(dates_in("The vehicles have been held around Srirampur since September 17.", date(2026, 10, 23))) == \
        {date(2026, 9, 17), date(2026, 10, 23)}
    assert affected_place("Protesters blocked roads at several places, including Bhubaneswar, Cuttack and Puri.", "Cuttack")
