"""Сборка отчёта /audit из уже собранных строк. Чистое: без Discord и сети."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Decision, UserInfo


@dataclass
class Row:
    who: str  # отображаемое имя (+ логин)
    mine: str  # роли бота у участника сейчас
    info: UserInfo | None  # None — учётка не найдена
    linked: str | None = None  # имя из подтверждённой связки (OAuth/правка); None — сопоставление по нику
    decision: Decision | None = None  # есть всегда, когда есть info


def line(r: Row) -> str:
    d, info = r.decision, r.info
    if not info or not d:
        return f"{r.who}; роли бота: {r.mine}"
    why = f"; отказ: {d.why}" if not d.ok else ""
    return (
        f"{r.who} ↔ {info.name} ({info.labels_text}{why}): "
        f"роли бота сейчас {r.mine}; бот выдал бы {', '.join(d.add) or '—'}, снял бы {', '.join(d.remove) or '—'}"
    )


def build(guild_name: str, rows: list[Row]) -> tuple[str, str]:
    """(заголовок для канала, полный текст файла). Три раздела: расхождения по подтверждённым связкам,
    расхождения по нику (не подтверждено), не сопоставленные. Подтверждённая связка, у которой учётки
    больше нет (переименование, удаление) — в первый раздел: это расхождение, а не «не сопоставлен»."""
    unmatched = [line(r) for r in rows if not r.info and not r.linked]
    verified = [
        line(r)
        if r.info
        else f"{r.who} ↔ {r.linked}: связка подтверждена, но такой учётки в рувики нет (переименована?)"
        for r in rows
        if r.linked and (not r.info or (r.decision and r.decision.changes))
    ]
    by_nick = [line(r) for r in rows if r.info and not r.linked and r.decision and r.decision.changes]
    n_linked = sum(1 for r in rows if r.linked)
    n_nick = sum(1 for r in rows if r.info and not r.linked)
    head = (
        f"Аудит сервера {guild_name}: участников {len(rows)}; связка подтверждена (OAuth/правка) {n_linked}, "
        f"совпадение только по нику {n_nick}, не сопоставлено {len(unmatched)}. "
        f"Расхождений: по подтверждённым {len(verified)}, по нику {len(by_nick)}. Роли не менялись."
    )
    body = "\n".join(
        [
            head,
            "",
            f"== Расхождения по подтверждённым связкам ({len(verified)}) — человек тот, "
            "ошибка возможна только в карте ролей ==",
            *(verified or ["нет"]),
            "",
            f"== Расхождения по нику ({len(by_nick)}) — НЕ подтверждено: ник мог совпасть с чужим участником рувики ==",
            *(by_nick or ["нет"]),
            "",
            f"== Не сопоставлены ({len(unmatched)}) — ник не совпал ни с одним участником рувики ==",
            *(unmatched or ["нет"]),
        ]
    )
    return head, body
