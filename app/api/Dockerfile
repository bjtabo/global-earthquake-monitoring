FROM python:3.14-slim@sha256:0514bc76712d64f8bb48adcb95b1ab0d88c69fee95d62fe4c18ee948bee9b1e1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
ADD https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem /srv/rds-ca.pem
RUN chmod 0644 /srv/rds-ca.pem
COPY shared/ shared/
COPY api/ ./
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--no-access-log", "--timeout-graceful-shutdown", "20", \
     "--timeout-keep-alive", "65"]
