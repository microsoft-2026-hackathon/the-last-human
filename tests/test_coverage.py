import json
from pathlib import Path

import pytest

from lasthuman.server.coverage import Seed, SeedError, ZoneCounts, actions_for, finalize, load_seed, risk_rank

SEED_PATH = Path(__file__).resolve().parents[1] / "docs" / "demo" / "dashboard-seed.json"


def test_bundled_demo_seed_loads_and_is_consistent():
    seed = load_seed(SEED_PATH)
    assert seed.merged == 41 and seed.gated == 13 and seed.attested == 11 and seed.forced == 2
    assert {z.zone for z in seed.zones} >= {"sample-app/app/auth/", "docs/", "src/lasthuman/"}
    assert seed.owners["sample-app/app/auth/"] == "@daeungo1"


@pytest.mark.parametrize(
    "payload, message",
    [
        ("[]", "root must be an object"),
        ('{"zones": [{"zone": "a/", "gated": 2, "merged": 1}]}', "gated must not exceed merged"),
        ('{"zones": [{"zone": "a/", "merged": 3, "gated": 2, "attested": 2, "forced": 1}]}', "must not exceed gated"),
        ('{"zones": [{"zone": "a/"}, {"zone": "a/"}]}', "unique"),
        ('{"zones": [{"zone": "a/", "answerers": -1}]}', "integer >= 0"),
        ('{"totals": {"merged": 1, "gated": 2}}', "inconsistent"),
    ],
)
def test_seed_validation_rejects_bad_shapes(tmp_path: Path, payload: str, message: str):
    path = tmp_path / "seed.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(SeedError, match=message):
        load_seed(path)


def test_finalize_seeds_every_codeowners_zone_and_sorts_by_risk():
    live = {
        "app/auth/": ZoneCounts(zone="app/auth/", merged=1, gated=1, attested=0, forced=1, answerers=0, prs=[7]),
        "app/orders/": ZoneCounts(zone="app/orders/", merged=2, gated=2, attested=2, answerers=2, prs=[8, 9]),
        "docs/": ZoneCounts(zone="docs/", merged=3, prs=[4, 5, 6]),
    }
    payload = finalize(
        live,
        all_zones=["app/auth/", "app/orders/", "app/db/", "docs/"],
        owners={"app/auth/": "@owner-a", "app/db/": "@owner-b"},
        totals={"merged": 6, "gated": 3, "attested": 2, "forced": 1, "waiting": 1},
        seed=None,
    )
    order = [zone["zone"] for zone in payload["zones"]]
    # gated zones first (fewest answerers, most exceptions), then merged-below-threshold, then untouched
    assert order == ["app/auth/", "app/orders/", "docs/", "app/db/"]
    auth = payload["zones"][0]
    assert auth["owner"] == "@owner-a" and auth["forced"] == 1 and auth["prs"] == [7]
    assert auth["sample_state"] == "small_sample" and auth["rate"] is None
    untouched = payload["zones"][-1]
    assert untouched["zone"] == "app/db/" and untouched["merged"] == 0 and untouched["answerers"] == 0
    assert payload["zero_answerer_zones"] == 1
    assert payload["forced_total"] == 1 and payload["waiting_total"] == 1
    assert payload["attested_rate"] is None
    assert payload["demo_seeded"] is False
    assert payload["actions"] == [
        {"zone": "app/auth/", "action": "Verify 1 exception after the fact", "owner": "@owner-a", "from": 0, "to": 1},
    ]
    dumped = json.dumps(payload)
    assert "actor" not in dumped and "login" not in dumped


def test_finalize_adds_seed_on_top_of_live_counts(tmp_path: Path):
    path = tmp_path / "seed.json"
    path.write_text(
        json.dumps(
            {
                "totals": {"merged": 10, "gated": 6, "attested": 5, "forced": 1, "waiting": 0},
                "zones": [
                    {
                        "zone": "app/auth/", "owner": "@seed",
                        "merged": 1, "gated": 1, "forced": 1, "answerers": 0, "prs": [27],
                    },
                    {
                        "zone": "app/orders/", "owner": "@seed",
                        "merged": 5, "gated": 5, "attested": 5, "answerers": 2, "prs": [1, 2],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    seed = load_seed(path)
    assert isinstance(seed, Seed)
    live = {"app/auth/": ZoneCounts(zone="app/auth/", merged=1, gated=1, attested=1, answerers=1, prs=[13])}
    payload = finalize(
        live,
        all_zones=["app/auth/", "app/orders/"],
        owners={"app/auth/": "@live"},
        totals={"merged": 1, "gated": 1, "attested": 1, "forced": 0, "waiting": 0},
        seed=seed,
    )
    by_zone = {zone["zone"]: zone for zone in payload["zones"]}
    auth = by_zone["app/auth/"]
    assert auth["gated"] == 2 and auth["attested"] == 1 and auth["forced"] == 1 and auth["answerers"] == 1
    assert auth["prs"] == [27, 13]
    assert auth["owner"] == "@live"  # live CODEOWNERS wins over the seed
    orders = by_zone["app/orders/"]
    assert orders["owner"] == "@seed" and orders["rate"] == 1.0 and orders["sample_state"] == "measured"
    assert payload["merged_total"] == 11 and payload["gated_total"] == 7 and payload["attested_total"] == 6
    assert payload["attested_rate"] == pytest.approx(6 / 7)
    assert payload["demo_seeded"] is True
    assert payload["zero_answerer_zones"] == 0


def test_risk_rank_and_actions_prefer_gated_zones():
    thin = ZoneCounts(zone="b/", gated=2, attested=2, answerers=1, merged=2)
    zero = ZoneCounts(zone="a/", gated=1, attested=0, forced=1, answerers=0, merged=1)
    healthy = ZoneCounts(zone="c/", gated=6, attested=6, answerers=3, merged=6)
    quiet = ZoneCounts(zone="d/", merged=4)
    untouched = ZoneCounts(zone="e/")
    ordered = sorted([healthy, quiet, thin, untouched, zero], key=risk_rank)
    assert [z.zone for z in ordered] == ["a/", "b/", "c/", "d/", "e/"]
    assert [a["zone"] for a in actions_for(ordered)] == ["a/", "b/"]
