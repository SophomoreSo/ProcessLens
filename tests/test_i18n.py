"""Translations (en, ko, pl) and user preferences (language, animations)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.config import PROJECT_ROOT
from app.i18n import LANGUAGES, catalog, t
from app.schemas.rules import RULE_PARAM_MODELS
from tests.conftest import HAND_CHECK, HAND_MAPPING, HAND_OPTIONS, run_import

LOCALES = PROJECT_ROOT / "app" / "i18n" / "locales"
PLACEHOLDER = re.compile(r"\{(\w+)\}")
PAGES = ["/", "/guide", "/process", "/variants", "/cases", "/performance", "/conformance", "/alerts", "/discovery", "/compare",
         "/relationships", "/tasks", "/datasets", "/import", "/settings"]


def raw(lang: str) -> dict:
    data = json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def used_keys() -> set[str]:
    keys: set[str] = set()
    for p in (PROJECT_ROOT / "app" / "templates").glob("*.html"):
        keys |= set(re.findall(r"t\('([a-z0-9_.]+)'", p.read_text()))
    for p in (PROJECT_ROOT / "app" / "static" / "js").glob("*.js"):
        keys |= set(re.findall(r'\bt\("([a-z0-9_.]+)"', p.read_text()))
    for p in (PROJECT_ROOT / "app").rglob("*.py"):
        keys |= set(re.findall(r'"((?:finding|error|conf|rule\.desc|alert\.msg|note|job|import|preview|demo|status|algo'
                               r'|run|ref|ocel|task|guide|entity)\.[a-z0-9_.]+)"', p.read_text()))
    from app.mining import registry as mining_registry

    for spec in [*mining_registry.ALGORITHMS.values(), *mining_registry.OC_ALGORITHMS.values()]:
        keys |= {spec.name_key, *spec.preconditions, *spec.limitations}
        keys |= {p_.help_key for p_ in spec.params}
        if spec.unavailable_reason:
            keys.add(spec.unavailable_reason)
    keys |= {f"rule.type.{r}" for r in RULE_PARAM_MODELS} | {f"rule.help.{r}" for r in RULE_PARAM_MODELS}
    keys |= {f"conf.{s}.{k}" for s in ("start", "end") for k in ("ok", "bad", "tie_ok", "tie_bad", "tie_mixed")}
    keys |= {f"conf.dur.{a}{b}" for a in ("over", "within") for b in ("", "_open")}
    from app.services import ocel as ocel_service

    keys |= {f"ocel.finding.{c}" for c in re.findall(r'_finding\(findings, "\w+", "(\w+)"', Path(ocel_service.__file__).read_text())}
    keys |= {f"set.lang.{code}" for code in LANGUAGES}
    keys |= {f"set.motion.{m}{s}" for m in ("system", "on", "off") for s in ("", "_hint")}
    keys |= {f"completion.short.{m}" for m in ("none", "end_activity", "case_status")}
    return {k for k in keys if not k.endswith(".")}


def test_every_locale_has_the_same_keys_and_placeholders():
    reference = raw("en")
    for lang in ("ko", "pl"):
        other = raw(lang)
        assert set(other) == set(reference), (lang, sorted(set(reference) ^ set(other)))
        for key, text in reference.items():
            assert set(PLACEHOLDER.findall(other[key])) == set(PLACEHOLDER.findall(text)), (lang, key)
            assert other[key].strip(), (lang, key)


def test_all_used_keys_exist():
    missing = sorted(used_keys() - set(raw("en")))
    assert not missing, missing


def test_translation_rendering_and_fallbacks():
    assert t("ko", "status.completed") == "완료"
    assert t("pl", "status.completed") == "zakończony"
    assert t("pl", "unit.hours", n="2,5") == "2,5 godz."
    assert t("xx", "status.completed") == "completed"  # unknown language -> English
    assert t("en", "no.such.key") == "no.such.key"
    assert t("en", "error.not_found", what={"$key": "entity.case", "$params": {}}) == "Case not found."
    assert t("ko", "error.not_found", what={"$key": "entity.case", "$params": {}}) == "케이스을(를) 찾을 수 없습니다."
    assert t("en", "error.invalid_filter") == "Invalid filter: {detail}"  # missing param stays visible, no crash
    assert catalog("ko") is catalog("ko")  # cached


@pytest.mark.parametrize("lang", list(LANGUAGES))
def test_pages_render_without_raw_keys(client, hand_dataset, lang):
    assert client.put("/api/preferences", json={"language": lang}).status_code == 200
    keys = {k for k in used_keys() if k.count(".") >= 1}
    for path in PAGES:
        r = client.get(path)
        assert r.status_code == 200, path
        html = r.text
        assert f'<html lang="{lang}"' in html
        text = re.sub(r"<script[\s\S]*?</script>", "", html)
        leftovers = [k for k in keys if re.search(rf"[>\s\"]{re.escape(k)}[<\s\"]", text)]
        assert not leftovers, (lang, path, leftovers[:5])


def test_embedded_catalog_is_safe_json(client):
    client.put("/api/preferences", json={"language": "pl"})
    html = client.get("/").text
    block = re.search(r'<script type="application/json" id="i18n-catalog">(.*?)</script>', html, re.S).group(1)
    data = json.loads(block)
    assert data["nav.overview"] == "Przegląd"
    assert "</" not in block  # tojson escapes "<", so the payload cannot close the script element


def test_preferences_api(client):
    p = client.get("/api/preferences").json()
    assert (p["language"], p["motion"]) == ("en", "system")
    assert set(p["languages"]) == {"en", "ko", "pl"}
    r = client.put("/api/preferences", json={"language": "ko", "motion": "off"})
    assert r.json()["language"] == "ko" and r.json()["motion"] == "off"
    html = client.get("/").text
    assert 'lang="ko"' in html and 'data-motion="off"' in html and "개요" in html
    assert client.put("/api/preferences", json={"language": "de"}).status_code == 422
    assert client.put("/api/preferences", json={"motion": "fast"}).status_code == 422
    assert client.put("/api/preferences", json={"motion": "on"}).json()["language"] == "ko"  # partial update


def test_preferences_persist_across_restart(data_dir):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.security import csrf_token

    with TestClient(app) as c:
        c.headers["X-CSRF-Token"] = csrf_token()
        c.put("/api/preferences", json={"language": "pl", "motion": "on"})
    with TestClient(app) as c:
        assert c.get("/api/preferences").json() == {"language": "pl", "motion": "on",
                                                    "languages": {"en": "English", "ko": "한국어", "pl": "Polski"}}


def test_api_messages_follow_language(client, hand_dataset):
    ko = {"X-ProcessLens-Language": "ko"}
    base = f"/api/datasets/{hand_dataset}/conformance-rules"
    rule = client.post(base, json={"name": "B", "rule_type": "required_activity", "params": {"activity": "B"}},
                       headers=ko).json()
    assert rule["description"] == "'B'이(가) 발생해야 함"
    ev = client.get(f"{base}/{rule['id']}/evaluation", params={"result": "compliant"}, headers=ko).json()
    assert ev["cases"]["items"][0]["explanation"] in ("'B'이(가) 1회 발생했습니다.", "'B'이(가) 2회 발생했습니다.")
    assert ev["notes"][0].startswith("규칙 기반 적합성")
    pl = client.get(f"{base}/{rule['id']}/evaluation", params={"result": "not_evaluable"},
                    headers={"X-ProcessLens-Language": "pl"}).json()
    assert "nie wiadomo" in pl["cases"]["items"][0]["explanation"]
    err = client.get(f"/api/datasets/{hand_dataset}/cases/NOPE", headers=ko).json()["error"]
    assert err["code"] == "not_found" and err["message"] == "케이스을(를) 찾을 수 없습니다."
    graph = client.get(f"/api/datasets/{hand_dataset}/graph", headers={"X-ProcessLens-Language": "pl"}).json()
    assert graph["data"]["notes"][1].startswith("To nie jest model BPMN")
    # the saved preference is the default when no header is sent
    client.put("/api/preferences", json={"language": "pl"})
    err2 = client.get("/api/datasets/999/overview").json()["error"]
    assert err2["message"] == "Nie znaleziono: Zbiór danych."


def test_persisted_alerts_and_findings_render_in_any_language(client, hand_dataset):
    client.post(f"/api/datasets/{hand_dataset}/alert-rules",
                json={"name": "long", "alert_type": "duration_threshold",
                      "params": {"threshold_hours": 5, "metric": "observed_duration"}})
    client.post(f"/api/datasets/{hand_dataset}/alert-rules/evaluate")
    en = client.get("/api/alerts", params={"dataset_id": hand_dataset}).json()["items"][0]["message"]
    ko = client.get("/api/alerts", params={"dataset_id": hand_dataset},
                    headers={"X-ProcessLens-Language": "ko"}).json()["items"][0]["message"]
    assert en == "Observed duration 6.0 h exceeds 5.0 h."
    assert ko == "관측 소요 시간 6.0시간이 5.0시간을 초과합니다."
    bad = "case_id,activity,timestamp\n1,A,2024-01-01T00:00:00Z\n,B,2024-01-01T01:00:00Z\n"
    job = run_import(client, bad, {"case_id": "case_id", "activity": "activity", "timestamp": "timestamp"},
                     invalid_row_policy="quarantine")
    rep = client.get(f"/api/datasets/{job['dataset_id']}/validation-report",
                     headers={"X-ProcessLens-Language": "pl"}).json()
    assert any(f["message"] == "ID przypadku jest puste." for f in rep["findings"])


def test_rejected_import_error_is_localized(client):
    bad = "case_id,activity,timestamp\n,A,2024-01-01T00:00:00Z\n"
    from tests.conftest import upload
    up = upload(client, bad)
    r = client.post(f"/api/uploads/{up['upload_id']}/import",
                    json={"mapping": {"case_id": "case_id", "activity": "activity", "timestamp": "timestamp"}})
    from app.services import jobs
    jobs.wait_for(r.json()["id"])
    job = client.get(f"/api/jobs/{r.json()['id']}", headers={"X-ProcessLens-Language": "ko"}).json()
    assert job["status"] == "failed"
    assert job["error"].startswith("가져오기 거부")
    assert job["stage"] == "거부됨"


def test_demo_uses_current_language(client):
    client.put("/api/preferences", json={"language": "ko"})
    r = client.post("/api/demo/load")
    from app.services import jobs
    jobs.wait_for(r.json()["id"])
    ds = next(d for d in client.get("/api/datasets").json() if d["source_filename"] == "p2p_demo_events.csv")
    assert ds["name"] == "데모: 구매-지급(합성)"
    rules = client.get(f"/api/datasets/{ds['id']}/conformance-rules").json()
    assert rules[0]["name"] == "구매 주문 승인 필수"


def test_unknown_header_language_falls_back_to_preference(client, hand_dataset):
    r = client.get(f"/api/datasets/{hand_dataset}/cases/NOPE", headers={"X-ProcessLens-Language": "xx"})
    assert r.json()["error"]["message"] == "Case not found."


def test_motion_css_disables_everything_when_off():
    css = (PROJECT_ROOT / "app" / "static" / "css" / "app.css").read_text()
    off_rule = re.search(r':root\[data-motion-active="false"\] \*[^{]*\{([^}]*)\}', css).group(1)
    assert "animation: none !important" in off_rule and "transition: none !important" in off_rule
    js = (PROJECT_ROOT / "app" / "static" / "js" / "core.js").read_text()
    assert "prefers-reduced-motion: reduce" in js  # "system" follows the OS setting


def test_locale_files_are_valid_utf8_json():
    for path in LOCALES.glob("*.json"):
        json.loads(Path(path).read_text(encoding="utf-8"))
