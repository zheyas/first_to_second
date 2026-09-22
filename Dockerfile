FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Render задаёт свой $PORT в рантайме — слушаем именно его (с фолбэком 8000
# для локального docker run без переменных окружения). --timeout увеличен со
# стандартных 30с: /api/dashboard последовательно делает несколько
# постраничных запросов к Bitrix24 (новые клиенты + пробные + постоянные
# клиенты) с retry-логикой на стороне bitrix.py (см. там) — в худшем случае
# это заметно дольше 30 секунд, а воркер без запаса убивается посреди
# запроса, и клиент получает пустой 500 вместо ответа.
CMD ["sh", "-c", "gunicorn -w 2 --timeout 180 -b 0.0.0.0:${PORT:-8000} app:app"]
