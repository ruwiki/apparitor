from apparitor import audit, match
from apparitor.models import Decision
from tests.conftest import info


def test_candidates_order_and_dedup():
    assert match.candidates("Pessimist", "Pessimist", "pessimist2006") == ["Pessimist", "pessimist2006"]
    assert match.candidates(None, None, "x") == ["x"]


def test_pick_prefers_edits():
    infos = {
        "Pessimist": info(name="Pessimist", editcount=0),
        "pessimist2006": info(name="Pessimist2006", editcount=364170),
    }
    assert match.pick(["Pessimist", "pessimist2006"], infos).name == "Pessimist2006"
    assert match.pick(["nobody"], {"nobody": None}) is None


def test_audit_report_sections():
    same = Decision(ok=True, why="ок", add=[], remove=[])
    diff = Decision(ok=True, why="ок", add=["ПИ+"], remove=["ПИ"])
    rows = [
        audit.Row(who="Carn", mine="Арбитр", info=info(name="Carn"), linked="Carn", decision=same),
        audit.Row(who="Swarrel", mine="ПИ", info=info(name="Swarrel", labels=["ПИ"]), decision=diff),
        audit.Row(who="Lotta", mine="—", info=None),
        audit.Row(who="Gone", mine="✔", info=None, linked="Old Name"),  # связка есть, учётки нет
    ]
    head, body = audit.build("Clerks", rows)
    assert "участников 4; связка подтверждена (OAuth/правка) 2, совпадение только по нику 1, не сопоставлено 1" in head
    assert "по подтверждённым 1, по нику 1" in head
    assert "Swarrel ↔ Swarrel (ПИ): роли бота сейчас ПИ; бот выдал бы ПИ+, снял бы ПИ" in body
    assert "Lotta; роли бота: —" in body
    assert "Gone ↔ Old Name: связка подтверждена, но такой учётки в рувики нет" in body
    assert body.index("Gone ↔") < body.index("== Расхождения по нику"), "пропавшая связка — в первом разделе"
