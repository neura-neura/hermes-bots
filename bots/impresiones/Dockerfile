FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PTB_TIMEDELTA=1
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY impresion_bot ./impresion_bot
RUN pip install --no-cache-dir --no-deps .
CMD ["python", "-m", "impresion_bot", "run"]
