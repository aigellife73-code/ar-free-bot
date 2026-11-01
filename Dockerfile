FROM mcr.microsoft.com/playwright/python:v1.64.1-jammy

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir python-telegram-bot playwright qrcode pillow
RUN python -m playwright install chromium --with-deps

EXPOSE 8000

CMD ["python", "bot.py"]
