FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# پوشهٔ data باید بین ری‌استارت‌ها باقی بماند (وضعیت ریسک/تعداد معاملات کاغذی را دارد)
VOLUME ["/app/data"]

CMD ["python", "bot.py"]
