"""Клиент Bitrix24 REST для отчёта по конверсии первого абонемента во второй."""
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv

load_dotenv()

WEBHOOK = (os.getenv("BITRIX_WEBHOOK_URL") or "").rstrip("/") + "/"
PORTAL_URL = re.sub(r"^(https?://[^/]+).*$", r"\1", WEBHOOK) if WEBHOOK.startswith("http") else ""

# Воронки: «Новые клиенты» (первая покупка) и «Постоянные клиенты» (повторные).
# Абонементы продаются только в этих двух воронках (проверено по товарным
# строкам сделок других воронок — там их не бывает).
CATEGORY_NEW = int(os.getenv("NEW_CLIENTS_CATEGORY", "6"))
CATEGORY_RECURRING = int(os.getenv("RECURRING_CLIENTS_CATEGORY", "18"))
CATEGORIES = (CATEGORY_NEW, CATEGORY_RECURRING)
STAGE_WON = {CATEGORY_NEW: f"C{CATEGORY_NEW}:WON", CATEGORY_RECURRING: f"C{CATEGORY_RECURRING}:WON"}

# Пользовательские поля сделки
F_CITY = os.getenv("UF_CITY_FIELD", "UF_CRM_1788273435017")            # Город (отчёт): СПБ / МСК
F_BRANCH_NEW = os.getenv("UF_BRANCH_FIELD", "UF_CRM_66F3C807CCD05")    # Филиал ученика (воронка «Новые клиенты»)
F_BRANCH_RECURRING = os.getenv("UF_PAYMENT_BRANCH_FIELD", "UF_CRM_1786967007592")  # Филиал платежа (воронка «Постоянные клиенты»)
F_TRAINER = os.getenv("UF_TRAINER_FIELD", "UF_CRM_1753880349381")      # Преподаватель/тренер (телефон в значении отбрасывается)

SELECT = [
    "ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "CLOSEDATE", "DATE_CREATE",
    "CONTACT_ID", "COMPANY_ID", "ASSIGNED_BY_ID",
    F_CITY, F_BRANCH_NEW, F_BRANCH_RECURRING, F_TRAINER,
]

PAGE_WORKERS = 20          # листинг сделок — всего 5-6 batch-запросов даже за всю историю, можно смело
PRODUCT_ROWS_WORKERS = 10  # товарные строки — тысячи batch-запросов; слишком высокий параллелизм здесь
                            # приводит к перегрузке портала и резкому разбросу времени ответа отдельных
                            # пачек (замерено: при 20 воркерах отдельные пачки из 50 команд иногда
                            # отвечали 60-70с вместо обычных 1-2с)
BATCH_CMD_SIZE = 50


class BitrixError(RuntimeError):
    pass


def _call(method: str, params=None, retries=3):
    if not WEBHOOK.startswith("http"):
        raise BitrixError("BITRIX_WEBHOOK_URL не задан в .env")
    url = WEBHOOK + method
    last_exc = None
    for attempt in range(retries):
        try:
            r = requests.post(url, json=params or {}, timeout=60)
            data = r.json()
        except Exception as exc:  # сеть/парсинг ответа
            last_exc = exc
            time.sleep(1 + attempt)
            continue
        if "error" in data:
            if str(data["error"]).upper() == "QUERY_LIMIT_EXCEEDED" and attempt < retries - 1:
                time.sleep(1.5 + attempt)
                continue
            raise BitrixError(f"{method}: {data.get('error_description') or data['error']}")
        return data
    raise BitrixError(f"{method}: {last_exc}")


def _rows(result):
    if isinstance(result, dict):
        return [result]
    return list(result or [])


def _flatten(prefix: str, value, out: dict) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}[{k}]", v, out)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    else:
        out[prefix] = value


def _to_query(params: dict) -> str:
    flat: dict = {}
    for k, v in params.items():
        _flatten(k, v, flat)
    return urllib.parse.urlencode(flat, doseq=True)


def _run_batch(commands: dict[str, str]) -> dict:
    """commands: {ключ команды: "method?querystring"}. Одна HTTP-пачка, до BATCH_CMD_SIZE команд."""
    return _call("batch", {"halt": 0, "cmd": commands})


def _run_batches(commands: dict[str, str], workers: int = PAGE_WORKERS) -> dict[str, list]:
    """Много команд сразу: бьёт на пачки по BATCH_CMD_SIZE, гоняет их параллельно.
    Задержка одного запроса к этому порталу — единицы секунд, поэтому решающий
    фактор — число HTTP-обращений, а не объём данных в каждом: пачка из 50
    вложенных команд (~1.5с) на порядки быстрее 50 последовательных запросов."""
    keys = list(commands)
    chunks = [keys[i:i + BATCH_CMD_SIZE] for i in range(0, len(keys), BATCH_CMD_SIZE)]
    if not chunks:
        return {}

    def fetch(chunk_keys):
        return _run_batch({k: commands[k] for k in chunk_keys})

    with ThreadPoolExecutor(max_workers=min(workers, len(chunks))) as pool:
        responses = list(pool.map(fetch, chunks))

    out: dict[str, list] = {}
    for data in responses:
        results = (data.get("result") or {}).get("result") or {}
        if isinstance(results, dict):
            for key, res in results.items():
                out[key] = _rows(res)
    return out


def _list_all(method: str, params: dict) -> list[dict]:
    """Все страницы списка: первая — обычным запросом (даёт total), остальные —
    через batch (см. _run_batches): для отчёта за всю историю это сотни
    страниц, и гонять их по одной, даже параллельно, слишком медленно на
    задержке этого портала."""
    first = _call(method, {**params, "start": 0})
    out = _rows(first.get("result"))
    total = int(first.get("total") or 0)
    page = len(out)
    if not out or total <= page:
        return out

    offsets = list(range(page, total, page))
    query = _to_query(params)
    commands = {f"p{off}": f"{method}?{query}&start={off}" for off in offsets}
    pages = _run_batches(commands)
    for off in offsets:
        out.extend(pages.get(f"p{off}", []))
    return out


def fetch_won_deals() -> list[dict]:
    """Все успешные сделки воронок «Новые клиенты» и «Постоянные клиенты», за всё время.

    За всё время, а не за выбранный в отчёте период: чтобы понять, купил ли
    клиент второй абонемент, нужна вся его история, а не только сделки внутри
    отображаемого периода — вторая покупка вполне может лежать за его границами.
    """
    out = []
    for cat in CATEGORIES:
        out.extend(_list_all("crm.deal.list", {
            "filter": {"CATEGORY_ID": cat, "STAGE_ID": STAGE_WON[cat]},
            "select": SELECT,
        }))
    return out


def product_rows(deal_ids) -> dict[int, list[dict]]:
    """Товарные строки сделок батчем crm.deal.productrows.get, по 50 сделок за HTTP-запрос."""
    ids = sorted({int(x) for x in deal_ids if str(x).isdigit()})
    out = {i: [] for i in ids}
    if not ids:
        return out

    commands = {f"d{did}": f"crm.deal.productrows.get?id={did}" for did in ids}
    results = _run_batches(commands, workers=PRODUCT_ROWS_WORKERS)
    for key, rows in results.items():
        try:
            did = int(str(key).lstrip("d"))
        except (TypeError, ValueError):
            continue
        for row in rows:
            if isinstance(row, dict):
                out[did].append({"PRODUCT_NAME": row.get("PRODUCT_NAME") or ""})
    return out


def city_enum() -> dict[str, str]:
    """ID значения → подпись для поля «Город (отчёт)»."""
    data = _call("crm.deal.userfield.list", {"filter": {"FIELD_NAME": F_CITY}})
    field = (data.get("result") or [{}])[0]
    return {str(i["ID"]): i["VALUE"] for i in (field.get("LIST") or [])}


def user_names(ids) -> dict[str, str]:
    """ID пользователя → «Фамилия Имя», батчем по 50."""
    ids = sorted({str(int(x)) for x in ids if str(x).isdigit() and int(x) > 0})
    out: dict[str, str] = {}
    if not ids:
        return out
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        for u in _list_all("user.get", {"filter": {"@ID": chunk}, "select": ["ID", "NAME", "LAST_NAME"]}):
            uid = str(u.get("ID") or "")
            name = " ".join(p.strip() for p in (u.get("LAST_NAME") or "", u.get("NAME") or "") if p and p.strip())
            if uid and name:
                out[uid] = name
    return out
