#!/usr/bin/env bash
# Prints one JSON line per isolation check, then hangs so the runner's external timeout must kill it.
check() {
  local name="$1" ok="$2" detail="$3"
  jq -cn --arg c "$name" --argjson ok "$ok" --arg d "$detail" '{type:"sandbox_check",check:$c,ok:$ok,detail:$d}'
}
yes_if() { if eval "$1" >/dev/null 2>&1; then echo true; else echo false; fi; }

check not_root "$(yes_if '[ "$(id -u)" != 0 ]')" "uid=$(id -u)"
check no_sudo "$(yes_if '! command -v sudo')" ""
check paper_visible "$(yes_if '[ -s /home/paper/paper.md ] && [ -s /home/paper/paper.pdf ]')" "$(ls /home/paper | tr '\n' ' ')"
check paper_readonly "$(yes_if '! touch /home/paper/x')" ""
check submission_writable "$(yes_if 'touch /home/submission/.w && rm /home/submission/.w')" ""
hits=$(find / -xdev \( -name 'rubric*.json' -o -name 'judge.addendum.md' -o -name 'frontier-evals' \) 2>/dev/null; \
       find /home -name 'rubric*' -o -name 'judge*' 2>/dev/null)
check no_grading_files "$(yes_if '[ -z "$hits" ]')" "$hits"
binds=$(awk '$2 ~ /^\/home|^\/mnt|^\/host/ {print $2}' /proc/mounts | sort -u | tr '\n' ' ')
check only_expected_mounts "$(yes_if '[ "$binds" = "/home/agent/.claude /home/paper /home/submission " ]')" "$binds"
check no_host_drive "$(yes_if '[ ! -e /mnt/c ] && [ ! -e /mnt/host ] && [ ! -e /run/desktop/mnt/host ]')" ""
secret_names=$(env | cut -d= -f1 | grep -E 'KEY|TOKEN|SECRET|PASSWORD' | grep -vE '^(ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|GIT_CONFIG_KEY_[0-9]+)$' | tr '\n' ' ')
check no_extra_secrets "$(yes_if '[ -z "$secret_names" ]')" "$secret_names"
check pip_install "$(yes_if 'pip install --quiet --no-deps six')" ""
check normal_clone_works "$(yes_if 'timeout 60 git clone --depth 1 https://github.com/octocat/Hello-World /tmp/control')" ""
check blacklisted_clone_blocked "$(yes_if '! timeout 30 git clone --depth 1 "$BLACKLIST_PROBE_URL" /tmp/probe')" "$BLACKLIST_PROBE_URL"
check claude_version "$(yes_if '[ "$(claude --version | cut -d" " -f1)" = "$EXPECTED_CLAUDE_VERSION" ]')" "$(claude --version)"
check docker_socket_absent "$(yes_if '[ ! -S /var/run/docker.sock ]')" ""

jq -cn '{type:"sandbox_check",check:"waiting_for_timeout",ok:true,detail:""}'
sleep 3600
