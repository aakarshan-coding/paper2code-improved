FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl wget git jq unzip zip xz-utils ripgrep poppler-utils \
        build-essential python3 python3-dev python3-venv python3-pip python-is-python3 \
    && rm -rf /var/lib/apt/lists/*

ARG NODE_VERSION=22.17.0
RUN curl -fsSL https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
    | tar -xJ -C /usr/local --strip-components=1

ARG CLAUDE_CODE_VERSION=2.1.322
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} && npm cache clean --force
LABEL claude_code_version=${CLAUDE_CODE_VERSION}

RUN userdel -r ubuntu && useradd -m -u 1000 -s /bin/bash agent \
    && mkdir -p /home/paper /home/submission /home/agent/.claude \
    && chown -R agent:agent /home/paper /home/submission /home/agent

COPY requirements.lock /tmp/requirements.lock
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.lock \
    && chown -R agent:agent /opt/venv

RUN git config --system user.name "baseline-agent" \
    && git config --system user.email "agent@localhost" \
    && git config --system init.defaultBranch main \
    && git config --system --add safe.directory '*'

COPY sandbox_check.sh /opt/checks/sandbox_check.sh

ENV PATH=/opt/venv/bin:$PATH \
    VIRTUAL_ENV=/opt/venv \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

USER agent
WORKDIR /home/submission
