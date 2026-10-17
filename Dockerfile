FROM python:3.12-slim AS runtime

ENV VIRTUAL_ENV=/opt/onsite-print-venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

RUN python -m venv "${VIRTUAL_ENV}"
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
RUN python -m pip install --no-cache-dir .

FROM runtime AS development

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gh \
        git \
        jq \
        openssh-client \
        postgresql-client \
        ripgrep \
        sudo \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 vscode \
    && useradd --uid 1000 --gid 1000 --shell /bin/bash --create-home vscode \
    && echo "vscode ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/vscode \
    && chmod 0440 /etc/sudoers.d/vscode \
    && mkdir -p /home/vscode/.vscode-server/data \
    && chown -R vscode:vscode /home/vscode/.vscode-server \
    && chown -R vscode:vscode "${VIRTUAL_ENV}"

USER vscode

FROM runtime AS production

USER nobody
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
