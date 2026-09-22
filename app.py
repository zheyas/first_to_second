"""Веб-приложение: конверсия первого абонемента во второй."""
import json
import logging
import os
import threading
from datetime import date, timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

import bitrix
import report

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev")

COLORS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "color.json")


def load_colors() -> list[dict]:
    """Диапазоны цвета бейджа конверсии. Читается на каждый запрос — правки в color.json применяются без рестарта."""
    try:
        with open(COLORS_FILE, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (FileNotFoundError, OSError, ValueError) as exc:
        logging.warning("color.json не прочитан: %s", exc)
        return []
    return [{"low": float(x["low"]), "high": float(x["high"]), "color": str(x["color"])}
            for x in raw if isinstance(x, dict) and {"low", "high", "color"} <= x.keys()]


def _default_range() -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=90)).isoformat(), today.isoformat()


def _params() -> tuple[str, str]:
    d_from, d_to = _default_range()
    date_from = request.args.get("date_from") or d_from
    date_to = request.args.get("date_to") or d_to
    return date_from, date_to


@app.route("/")
def index():
    date_from, date_to = _default_range()
    return render_template("index.html", date_from=date_from, date_to=date_to)


@app.route("/api/report")
def api_report():
    date_from, date_to = _params()
    if request.args.get("refresh") == "1":
        report.drop_cache()
    try:
        data = report.build(date_from, date_to)
        data["colors"] = load_colors()
        return jsonify(data)
    except bitrix.BitrixError as exc:
        return jsonify({"error": str(exc)}), 502


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


def _warmup() -> None:
    try:
        report.build(*_default_range())
    except Exception as exc:  # прогрев не должен мешать старту приложения
        logging.warning("Не удалось прогреть кэш: %s", exc)


if os.getenv("WARMUP", "1") == "1":
    threading.Thread(target=_warmup, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "3000")), debug=True)
