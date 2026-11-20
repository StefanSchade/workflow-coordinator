#!/usr/bin/env bash
set -euo pipefail
export PATH=/home/stefan/wfc-ops/wfc-beta1-bp7-guidance-config-001-20261120-01/dev-env/bin:/home/stefan/wfc-ops/wfc-beta1-bp7-guidance-config-001-20261120-01/runtime-env/bin:/home/stefan/.local/bin:/home/stefan/.nvm/versions/node/v22.23.2/bin:/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games
export TMPDIR=/home/stefan/wfc-scratch/wfc-beta1-bp7-guidance-config-001-20261120-01
export TMP=/home/stefan/wfc-scratch/wfc-beta1-bp7-guidance-config-001-20261120-01
export TEMP=/home/stefan/wfc-scratch/wfc-beta1-bp7-guidance-config-001-20261120-01
exec /home/stefan/wfc-ops/wfc-beta1-bp7-guidance-config-001-20261120-01/runtime-env/bin/wfc \
  --repository /home/stefan/repos/spreadsheet-handling/workflow-coordinator \
  --state-root /home/stefan/wfc-state/wfc-beta1-bp7-guidance-config-001-20261120-01 \
  --expected-head 48b86b11da99c32b7d6650e91faf262e415aef6b \
  --work-item governance/commissions/wfc-beta1-bp7-guidance-config-001/work-item.yaml \
  --profile governance/commissions/wfc-beta1-bp7-guidance-config-001/profile.yaml \
  --policy governance/commissions/wfc-beta1-bp7-guidance-config-001/policy.yaml \
  --components governance/self-development/components.yaml \
  --task governance/commissions/wfc-beta1-bp7-guidance-config-001/task.md \
  --execution-bindings /home/stefan/wfc-ops/wfc-beta1-bp7-guidance-config-001-20261120-01/execution-bindings.yaml \
  --timeout-seconds 3600.0 \
  --executable /home/stefan/.local/bin/claude
