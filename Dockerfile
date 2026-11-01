FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir python-telegram-bot playwright qrcode pillow

CMD ["python", "bot.py"]
