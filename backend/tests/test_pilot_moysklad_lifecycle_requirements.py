from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_live_lifecycle import BASE_REQUIRED_SCENARIOS, required_scenarios  # noqa: E402


REQUIRED_MOYSKLAD_OUTBOUND = {
    "moysklad_customerorder_outbound",
    "moysklad_demand_outbound",
    "moysklad_salesreturn_outbound",
}


def test_live_lifecycle_requires_all_moysklad_outbound_documents():
    assert REQUIRED_MOYSKLAD_OUTBOUND <= set(BASE_REQUIRED_SCENARIOS)
    assert REQUIRED_MOYSKLAD_OUTBOUND <= set(
        required_scenarios(
            {
                "MEILISEARCH_ENABLED": "false",
                "MEDIA_STORAGE": "local",
            }
        )
    )
