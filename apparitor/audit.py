"""Сборка отчёта /audit из уже собранных строк. Чистое: без Discord и сети."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Decision, UserInfo


@dataclass
class Row:
    who: str  # отображаемое имя (+ логин)
    mine: str  # роли бота у участника сейчас
    info: UserInfo | None  # None — не сопоставлен
    linked: bool  # связка подтверждена (OAuth/правка), иначе — по нику
    decision: Decision | None = None


def line(r: Row) -> str:
    d, info = r.decision, r.info
    why = f"; отказ: {d.why}" if not d.ok else ""
    return (
        f"{r.who} ↔ {info.name} ({', '.join(info.labels) or 'без флагов'}{why}): "
        f"роли бота сейчас {r.mine}; бот выдал бы {', '.join(d.add) or '—'}, снял бы {', '.join(d.remove) or '—'}"
    )


def build(guild_name: str, rows: list[Row]) -> tuple[str, str]:
    """(заголовок для канала, полный текст файла). Три раздела: расхождения по подтверждённым связкам,
    расхождения по нику (не подтверждено), не сопоставленные."""
    unmatched = [f"{r.who}; роли бота: {r.mine}" for r in rows if not r.info]
    verified = [line(r) for r in rows if r.info and r.linked and r.decision.changes]
    by_nick = [line(r) for r in rows if r.info and not r.linked and r.decision.changes]
    n_linked = sum(1 for r in rows if r.info and r.linked)
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
