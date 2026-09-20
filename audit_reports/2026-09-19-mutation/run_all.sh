#!/bin/zsh
# Sequential campaign orchestrator: resumes a campaign (retries INCONCLUSIVE
# baselines) then runs every campaign in priority order. The harness's
# campaign.lock guarantees one owner of the worktrees at a time.
# Usage: zsh run_all.sh [campaign ...]   (default: all ten)
set -e
cd "$(dirname "$0")/../../backend"
H="../audit_reports/2026-09-19-mutation/harness.py"
CAMPAIGNS=("$@")
if [ ${#CAMPAIGNS[@]} -eq 0 ]; then
  CAMPAIGNS=(c1 c2 c3 c4 c5 c6 c7 c9 c10 c8)
fi
for c in "${CAMPAIGNS[@]}"; do
  echo "=== ORCHESTRATOR: campaign $c $(date '+%H:%M:%S') ==="
  .venv/bin/python $H --campaign $c --workers 3 || echo "campaign $c exited $?"
done
echo "=== ORCHESTRATOR: all campaigns complete $(date '+%H:%M:%S') ==="
