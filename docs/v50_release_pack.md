# FLASHIN v50 Release Pack

Created at: 2026-07-02T19:00:14.122172

## Entrypoints

- simple_start: `scripts/start_simple.sh`
- unified_launch: `scripts/launch.py`
- production_deploy: `scripts/deploy_production.sh`
- rollback: `scripts/rollback.sh`
- readiness_gate: `scripts/readiness_gate.py`

## Must read

- `README.md`
- `docs/v49_unified_system_map.md`
- `docs/v49_final_gap_analysis.md`
- `docs/v50_final_handover.md`
- `docs/pilot/live_pilot_runner.md`

## No-go if

- Telegram auth fails
- YooKassa webhook fails
- MoySklad stock mapping is wrong
- Refund does not return money/points
- Fulfillment task is not created after payment
- Backup restore was not tested
