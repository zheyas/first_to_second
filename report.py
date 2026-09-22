"""Агрегация: конверсия первого абонемента во второй, по срезам."""
import re
import threading
import time
from collections import defaultdict
from datetime import date as date_cls

import bitrix

EMPTY_CITY = "Без города"
EMPTY_BRANCH = "Без филиала"
EMPTY_MANAGER = "Без менеджера"
EMPTY_TRAINER = "Без тренера"

CACHE_TTL = 600  # сек — сбор всей истории тяжелее обычного отчёта за период, кэш держим дольше
_cache: dict = {}
_lock = threading.Lock()

_ABONEMENT_RE = re.compile(r"абонемент\D*\d+", re.IGNORECASE)


def _is_abonement(product_name: str) -> bool:
    """Абонемент — товарная строка вида «Абонемент 12 тренировок СПБ».
    Пробное занятие и разовая тренировка абонементом не считаются (по ТЗ)."""
    return bool(_ABONEMENT_RE.search(str(product_name or "")))


def _clean_trainer(value) -> str:
    """«Елфимов Михаил +7(980)744-45-11» → «Елфимов Михаил» (в поле пишут телефон следом)."""
    text = re.split(r"[+\d]", str(value or ""), maxsplit=1)[0].strip(" ,;-")
    return text or EMPTY_TRAINER


def _norm(value, empty: str) -> str:
    text = str(value or "").strip()
    return text or empty


def _load_clients() -> list[dict]:
    """Список клиентов с их 1-й и (если есть) 2-й покупкой абонемента — за всю историю.
    Результат кэшируется на CACHE_TTL: полный сбор дороже отчёта за период.

    Лок держится на всё время сборки, а не только на чтение/запись кэша: иначе
    два параллельных запроса с холодным кэшем (например, ручная проверка поверх
    прогрева при старте) оба проходят проверку «кэша нет» и одновременно тянут
    всю историю сделок из Bitrix24 — удваивая нагрузку на и без того небольшой
    лимит запросов портала и всё только замедляя."""
    with _lock:
        hit = _cache.get("clients")
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        clients = _fetch_all_clients()
        _cache["clients"] = (time.time(), clients)
        return clients


def _fetch_all_clients() -> list[dict]:
    deals = bitrix.fetch_won_deals()
    deal_ids = [d["ID"] for d in deals]
    products = bitrix.product_rows(deal_ids)
    cities = bitrix.city_enum()
    manager_ids = {d.get("ASSIGNED_BY_ID") for d in deals if str(d.get("ASSIGNED_BY_ID") or "").isdigit()}
    managers = bitrix.user_names(manager_ids)

    # Одна покупка абонемента = одна сделка, у которой хотя бы одна товарная
    # строка — абонемент (на сделке изредка бывает по две строки-абонемента
    # сразу — это по-прежнему одна покупка, не две).
    events = []
    for d in deals:
        rows = products.get(int(d["ID"]), [])
        if not any(_is_abonement(r.get("PRODUCT_NAME")) for r in rows):
            continue
        cat = int(d.get("CATEGORY_ID") or 0)
        branch_field = bitrix.F_BRANCH_NEW if cat == bitrix.CATEGORY_NEW else bitrix.F_BRANCH_RECURRING
        contact_id = str(d.get("CONTACT_ID") or "").strip()
        company_id = str(d.get("COMPANY_ID") or "").strip()
        client_key = f"c{contact_id}" if contact_id and contact_id != "0" else (
            f"co{company_id}" if company_id and company_id != "0" else f"deal{d['ID']}"
        )
        events.append({
            "client_key": client_key,
            "deal_id": d["ID"],
            "title": d.get("TITLE") or "",
            "date": (d.get("CLOSEDATE") or d.get("DATE_CREATE") or "")[:10],
            "city": cities.get(str(d.get(bitrix.F_CITY)), "") or EMPTY_CITY,
            "branch": _norm(d.get(branch_field), EMPTY_BRANCH),
            "manager": managers.get(str(d.get("ASSIGNED_BY_ID") or ""), "") or EMPTY_MANAGER,
            "trainer": _clean_trainer(d.get(bitrix.F_TRAINER)),
        })

    by_client = defaultdict(list)
    for e in events:
        if e["date"]:
            by_client[e["client_key"]].append(e)

    clients = []
    for key, evs in by_client.items():
        evs.sort(key=lambda x: (x["date"], int(x["deal_id"])))
        first, second = evs[0], (evs[1] if len(evs) > 1 else None)
        days_to_convert = None
        if second:
            try:
                days_to_convert = (
                    date_cls.fromisoformat(second["date"]) - date_cls.fromisoformat(first["date"])
                ).days
            except ValueError:
                days_to_convert = None
        clients.append({
            "client_key": key,
            "title": first["title"],
            "first_date": first["date"],
            "first_deal_id": first["deal_id"],
            "city": first["city"],
            "branch": first["branch"],
            "manager": first["manager"],
            "trainer": first["trainer"],
            "converted": second is not None,
            "second_date": second["date"] if second else None,
            "second_deal_id": second["deal_id"] if second else None,
            "days_to_convert": days_to_convert,
        })

    return clients


def _slice(rows: list[dict], key: str) -> list[dict]:
    buckets = defaultdict(lambda: {"first": 0, "second": 0, "days": []})
    for r in rows:
        b = buckets[r[key]]
        b["first"] += 1
        if r["converted"]:
            b["second"] += 1
            if r["days_to_convert"] is not None:
                b["days"].append(r["days_to_convert"])
    out = []
    for name, b in buckets.items():
        out.append({
            "name": name,
            "first": b["first"],
            "second": b["second"],
            "conversion": round(b["second"] / b["first"] * 100, 1) if b["first"] else 0.0,
            "avg_days": round(sum(b["days"]) / len(b["days"]), 1) if b["days"] else None,
        })
    out.sort(key=lambda x: (-x["first"], x["name"]))
    return out


def _totals(rows: list[dict]) -> dict:
    first = len(rows)
    second = sum(1 for r in rows if r["converted"])
    days = [r["days_to_convert"] for r in rows if r["days_to_convert"] is not None]
    return {
        "name": "Итого",
        "first": first,
        "second": second,
        "conversion": round(second / first * 100, 1) if first else 0.0,
        "avg_days": round(sum(days) / len(days), 1) if days else None,
    }


def build(date_from: str, date_to: str) -> dict:
    all_clients = _load_clients()
    rows = [c for c in all_clients if date_from <= c["first_date"] <= date_to]

    daily = _slice(rows, "first_date")
    for r in daily:
        r["date"] = r["name"]

    return {
        "filters": {"date_from": date_from, "date_to": date_to},
        "totals": _totals(rows),
        "tables": {
            "cities": _slice(rows, "city"),
            "branches": _slice(rows, "branch"),
            "managers": _slice(rows, "manager"),
            "trainers": _slice(rows, "trainer"),
        },
        "daily": sorted(daily, key=lambda x: x["date"]),
        "clients": [
            {
                "title": c["title"],
                "first_date": c["first_date"],
                "second_date": c["second_date"],
                "city": c["city"],
                "branch": c["branch"],
                "manager": c["manager"],
                "trainer": c["trainer"],
                "converted": c["converted"],
                "first_deal_url": f"{bitrix.PORTAL_URL}/crm/deal/details/{c['first_deal_id']}/" if bitrix.PORTAL_URL else None,
                "second_deal_url": (f"{bitrix.PORTAL_URL}/crm/deal/details/{c['second_deal_id']}/"
                                     if bitrix.PORTAL_URL and c["second_deal_id"] else None),
            }
            for c in sorted(rows, key=lambda x: x["first_date"], reverse=True)
        ],
        "data_from": min((c["first_date"] for c in all_clients), default=None),
    }


def drop_cache() -> None:
    with _lock:
        _cache.clear()
