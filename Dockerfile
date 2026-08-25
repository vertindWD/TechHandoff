FROM golang:1.26.7-bookworm AS go-tools

RUN go install golang.org/x/tools/gopls@v0.23.0

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GOPLS_PATH=/usr/local/bin/gopls \
    GO_BINARY_PATH=/usr/local/go/bin/go \
    PATH=/usr/local/go/bin:${PATH}

COPY --from=go-tools /usr/local/go /usr/local/go
COPY --from=go-tools /go/bin/gopls /usr/local/bin/gopls

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir . \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir -p /app/data/proposals \
    && chown -R app:app /app/data

USER 10001:10001
EXPOSE 8787

CMD ["python", "-m", "tracker", "serve", "--host", "0.0.0.0", "--port", "8787"]
