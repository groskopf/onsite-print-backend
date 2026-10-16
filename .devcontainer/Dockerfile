FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# The inherited Yarn apt source has an expired signing key and is not needed.
RUN rm -f /etc/apt/sources.list.d/yarn.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends jq postgresql-client ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/onsite-print-venv \
    && chown -R vscode:vscode /opt/onsite-print-venv \
    && printf '%s\n' \
        'export VIRTUAL_ENV=/opt/onsite-print-venv' \
        'export PATH="$VIRTUAL_ENV/bin:$PATH"' \
        > /etc/profile.d/onsite-print-venv.sh

ENV VIRTUAL_ENV=/opt/onsite-print-venv
ENV PATH="/opt/onsite-print-venv/bin:${PATH}"
