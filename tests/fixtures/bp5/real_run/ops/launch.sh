#!/usr/bin/env bash
set -euo pipefail
export PATH=/home/stefan/wfc-ops/wfc-beta1-bp3-remediation-review-001-20261119-01/dev-env/bin:/home/stefan/wfc-ops/wfc-beta1-bp3-remediation-review-001-20261119-01/runtime-env/bin:/usr/local/bin:/usr/bin:/bin
export TMPDIR=/home/stefan/wfc-scratch/wfc-beta1-bp3-remediation-review-001-20261119-01
export TMP=/home/stefan/wfc-scratch/wfc-beta1-bp3-remediation-review-001-20261119-01
export TEMP=/home/stefan/wfc-scratch/wfc-beta1-bp3-remediation-review-001-20261119-01
exec /home/stefan/wfc-ops/wfc-beta1-bp3-remediation-review-001-20261119-01/runtime-env/bin/wfc \
  --repository /home/stefan/repos/spreadsheet-handling/workflow-coordinator \
  --state-root /home/stefan/wfc-state/wfc-beta1-bp3-remediation-review-001-20261119-01 \
  --expected-head b46d181b988ea0a4ae0f0534d16cc514ee632ed0 \
  --work-item governance/commissions/wfc-beta1-bp3-remediation-review-001/work-item.yaml \
  --profile governance/commissions/wfc-beta1-bp3-remediation-review-001/profile.yaml \
  --policy governance/commissions/wfc-beta1-bp3-remediation-review-001/policy.yaml \
  --components governance/self-development/components.yaml \
  --task governance/commissions/wfc-beta1-bp3-remediation-review-001/task.md \
  --execution-bindings /home/stefan/wfc-ops/wfc-beta1-bp3-remediation-review-001-20261119-01/execution-bindings.yaml \
  --timeout-seconds 3600.0 \
  --executable /home/stefan/.local/bin/claude
