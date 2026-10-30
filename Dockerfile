FROM node:24-bookworm-slim AS node
FROM ubuntu:24.04

ARG TARGETARCH
ARG ACCEPT_ANDROID_SDK_LICENSES=yes
ARG BANK_UID=1000
ARG BANK_GID=1000
ARG PLAYWRIGHT_VERSION=1.63.0
ARG SVGO_VERSION=4.1.0
ARG AGENT_BROWSER_VERSION=0.38.1
ARG GH_VERSION=2.102.0
ARG GH_SHA256=7e54a307f90afdc59796c325ec0c49fb09e6c18537727207a8ac7513584ea5b0
ARG ANDROID_TOOLS_ARCHIVE=commandlinetools-linux-16111833_latest.zip
ARG ANDROID_TOOLS_SHA256=0877a1d048fe4a24efe2eff536ca4223f7adeb58648bb81909d33c446918cfa8
ARG ANDROID_PLATFORM=android-37.0
ARG ANDROID_BUILD_TOOLS=36.0.0
ARG ANDROID_SYSTEM_IMAGE=system-images;android-36;google_apis;x86_64

SHELL ["/bin/bash", "-e", "-o", "pipefail", "-c"]
RUN test "$TARGETARCH" = amd64 || { echo 'Android SDK image requires linux/amd64' >&2; exit 1; }; \
    test "$ACCEPT_ANDROID_SDK_LICENSES" = yes || { \
      echo 'Read the Android SDK licenses, then build with --build-arg ACCEPT_ANDROID_SDK_LICENSES=yes' >&2; exit 1; }

COPY --from=node /usr/local/ /usr/local/
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl git openssh-client jq ripgrep unzip zip xz-utils \
      python3 python3-venv openjdk-21-jdk-headless librsvg2-bin libnss3-tools \
      libgl1 libegl1 libglu1-mesa libpulse0 libdbus-1-3 libfontconfig1 \
      libxcb-cursor0 libxkbcommon0 fonts-dejavu-core fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

RUN curl --fail --location --retry 3 "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.deb" -o /tmp/gh.deb \
    && echo "$GH_SHA256  /tmp/gh.deb" | sha256sum --check --strict \
    && dpkg -i /tmp/gh.deb \
    && rm /tmp/gh.deb \
    && gh --version

ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 \
    ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    AGENT_BROWSER_EXECUTABLE_PATH=/usr/local/bin/bank-chromium \
    VIRTUAL_ENV=/opt/venv \
    HOME=/home/bank \
    GRADLE_USER_HOME=/home/bank/.gradle \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8
ENV PATH="/opt/venv/bin:/opt/android-sdk/cmdline-tools/latest/bin:/opt/android-sdk/platform-tools:/opt/android-sdk/emulator:${JAVA_HOME}/bin:${PATH}"

COPY .agents/skills/bank-icon-maintenance/requirements.txt /tmp/skill-requirements.txt
COPY docker/requirements.txt /tmp/launcher-requirements.txt
RUN python3 -m venv "$VIRTUAL_ENV" \
    && pip install --no-cache-dir -r /tmp/skill-requirements.txt -r /tmp/launcher-requirements.txt "playwright==$PLAYWRIGHT_VERSION" \
    && playwright install --with-deps chromium \
    && ln -s "$(python3 -c 'from playwright.sync_api import sync_playwright; p = sync_playwright().start(); print(p.chromium.executable_path); p.stop()')" "$AGENT_BROWSER_EXECUTABLE_PATH" \
    && npm install --global --ignore-scripts --no-audit --no-fund "svgo@$SVGO_VERSION" \
    && npm install --global --no-audit --no-fund "agent-browser@$AGENT_BROWSER_VERSION" \
    && agent-browser --version \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/* /tmp/skill-requirements.txt /tmp/launcher-requirements.txt

RUN if id ubuntu &>/dev/null; then userdel -r ubuntu; fi \
    && groupadd --gid "$BANK_GID" bank \
    && useradd --uid "$BANK_UID" --gid "$BANK_GID" --create-home --shell /bin/bash bank \
    && mkdir -p "$ANDROID_HOME/cmdline-tools" /sessions /workspace "$GRADLE_USER_HOME" \
    && curl --fail --location --retry 3 "https://dl.google.com/android/repository/$ANDROID_TOOLS_ARCHIVE" -o /tmp/android-tools.zip \
    && echo "$ANDROID_TOOLS_SHA256  /tmp/android-tools.zip" | sha256sum --check --strict \
    && unzip -q /tmp/android-tools.zip -d "$ANDROID_HOME/cmdline-tools" \
    && mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest" \
    && rm /tmp/android-tools.zip \
    && chown -R bank:bank "$ANDROID_HOME" /sessions /workspace /home/bank

USER bank
RUN printf 'android.builder.sdkDownload=false\norg.gradle.workers.max=2\n' > "$GRADLE_USER_HOME/gradle.properties"
RUN printf 'y\n%.0s' {1..100} | sdkmanager --sdk_root="$ANDROID_HOME" --licenses >/dev/null
RUN sdkmanager --sdk_root="$ANDROID_HOME" \
      platform-tools "platforms;$ANDROID_PLATFORM" \
      "build-tools;$ANDROID_BUILD_TOOLS"
ARG INSTALL_ANDROID_EMULATOR=no
RUN if [ "$INSTALL_ANDROID_EMULATOR" = yes ]; then \
      sdkmanager --sdk_root="$ANDROID_HOME" emulator "$ANDROID_SYSTEM_IMAGE"; \
    fi

# Project sources are supplied by the host Git worktree, never baked into the image.
RUN mkdir -p "$HOME/.local" "$HOME/.omp/agent"
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/bank-icon-entrypoint
COPY docker/cleanup-worktrees.py /usr/local/bin/cleanup-worktrees.py
COPY docker/prepare-steps.py /usr/local/bin/prepare-steps.py
COPY docker/prompts/runner.md /usr/local/share/bank-icon-maintenance/runner.md
ENV TMPDIR=/tmp
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/bank-icon-entrypoint"]
CMD ["run"]
