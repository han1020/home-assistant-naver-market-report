ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache bash jq python3 py3-pip tzdata

WORKDIR /app
COPY requirements.txt /app/
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

COPY naver_market_report.py /app/
COPY run.sh /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
