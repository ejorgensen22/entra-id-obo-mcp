FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py obo.py server.py ./

ENV HOST=0.0.0.0
ENV PORT=8000
ENV AUTH_MODE=agentcore
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "server.py"]
