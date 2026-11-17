from __future__ import annotations

from climateledger.registry import ContractError
from support import LedgerCase


class UnitIntervalTests(LedgerCase):
    """Supplier keeps unit identifier raw_ppb but switches calibration at 2026-06."""

    def register_eras(self):
        self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                    "factor": "0.001", "valid_to": "2026-06"})
        self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                    "factor": "0.002", "valid_from": "2026-06"})

    def test_adjacent_intervals_coexist(self):
        self.register_eras()
        rules = self.service.store.get_units("co2_ppm", "raw_ppb")
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0].valid_to, "2026-06")
        self.assertEqual(rules[1].valid_from, "2026-06")
        events = [event for event in self.service.events("co2_ppm")
                  if event["kind"] == "register_unit"]
        self.assertEqual(events[-1]["detail"]["valid_from"], "2026-06")

    def test_identical_rule_registration_is_idempotent(self):
        self.register_eras()
        self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                    "factor": "0.001", "valid_to": "2026-06"})
        self.assertEqual(len(self.service.store.get_units("co2_ppm", "raw_ppb")), 2)

    def test_overlapping_interval_rejected_with_evidence(self):
        self.register_eras()
        with self.assertRaisesRegex(ContractError, "rule_overlap") as caught:
            self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                        "factor": "0.003", "valid_from": "2026-03",
                                        "valid_to": "2026-09"})
        self.assertIn("[2026-03, 2026-09)", str(caught.exception))
        self.assertIn("2026-06", str(caught.exception))
        self.assertIn("factor=0.001", str(caught.exception))
        self.assertEqual(len(self.service.store.get_units("co2_ppm", "raw_ppb")), 2)

    def test_same_interval_new_factor_rejected(self):
        self.register_eras()
        with self.assertRaisesRegex(ContractError, "rule_overlap"):
            self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                        "factor": "0.005", "valid_to": "2026-06"})

    def test_unbounded_rule_overlaps_any_interval(self):
        with self.assertRaisesRegex(ContractError, "rule_overlap"):
            self.service.register_unit({"indicator": "co2_ppm", "source_unit": "ppb",
                                        "factor": "0.002", "valid_from": "2026-06"})

    def test_interval_bounds_must_be_ordered(self):
        with self.assertRaisesRegex(ContractError, "rule_interval"):
            self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                        "factor": "0.001", "valid_from": "2026-08",
                                        "valid_to": "2026-06"})

    def test_interval_bounds_must_share_cadence(self):
        with self.assertRaisesRegex(ContractError, "rule_interval"):
            self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                        "factor": "0.001", "valid_from": "2026-01",
                                        "valid_to": "2027"})

    def test_interval_bound_must_match_indicator_cadence(self):
        with self.assertRaisesRegex(ContractError, "rule_interval"):
            self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                        "factor": "0.001", "valid_from": "2026"})
        with self.assertRaisesRegex(ContractError, "rule_interval"):
            self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                        "factor": "0.001", "valid_to": "2026-13"})

    def test_conversion_follows_observation_period(self):
        self.register_eras()
        batch, _ = self.ingest("eras", self.rows(428000, 428000, unit="raw_ppb", start=5))
        self.assertEqual(batch["status"], "accepted")
        rows = self.service.batch(batch["id"])["measurements"]
        self.assertEqual([row["period"] for row in rows], ["2026-05", "2026-06"])
        self.assertEqual([row["value"] for row in rows], ["428.00", "856.00"])
        self.assertEqual([row["factor"] for row in rows], ["0.001", "0.002"])
        self.assertEqual(rows[0]["valid_to"], "2026-06")
        self.assertEqual(rows[1]["valid_from"], "2026-06")

    def test_period_without_rule_is_rejected_with_evidence(self):
        self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                    "factor": "0.002", "valid_from": "2026-06"})
        batch, _ = self.ingest("rule_gap", self.rows(428000, unit="raw_ppb"))
        self.assertEqual(batch["status"], "rejected")
        finding = next(item for item in batch["findings"] if item["code"] == "unit_rule_gap")
        self.assertEqual(finding["line"], 1)
        self.assertIn("2026-01", finding["detail"])
        self.assertIn("[2026-06, *)", finding["detail"])

    def test_late_old_period_data_uses_contemporary_rule(self):
        self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                    "factor": "0.001", "valid_to": "2026-06"})
        first, _ = self.ingest("era_one", self.rows(428000, unit="raw_ppb", start=5))
        self.service.publish(first["id"], first["revision"])
        self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                    "factor": "0.002", "valid_from": "2026-06"})
        late, _ = self.ingest("late_old", self.rows(428000, unit="raw_ppb", start=5))
        self.assertEqual(late["status"], "accepted")
        row = self.service.batch(late["id"])["measurements"][0]
        self.assertEqual(row["value"], "428.00")
        self.assertEqual(row["factor"], "0.001")
        changed, _ = self.ingest("late_changed", self.rows(429000, unit="raw_ppb", start=5))
        self.assertEqual(changed["status"], "review")
        self.assertIn("revised_value", {item["code"] for item in changed["findings"]})

    def test_new_rule_does_not_rewrite_history(self):
        self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                    "factor": "0.001", "valid_to": "2026-06"})
        self.release("era_one", self.rows(428000, unit="raw_ppb", start=5))
        before = self.service.export(1)
        hashes_before = {row["payload_hash"] for row in self.service.batches()}
        self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                    "factor": "0.002", "valid_from": "2026-06"})
        self.assertEqual(self.service.export(1), before)
        self.assertEqual(before["entries"][0]["value"], "428.00")
        self.assertEqual({row["payload_hash"] for row in self.service.batches()}, hashes_before)

    def test_replay_cross_boundary_batch(self):
        self.register_eras()
        batch, manifest = self.ingest("boundary", self.rows(428000, 428000, unit="raw_ppb", start=5))
        self.service.publish(batch["id"], batch["revision"])
        result = self.service.replay(manifest, batch["id"])
        self.assertTrue(result["reproducible"])
        self.assertEqual([row["replayed_rule"]["factor"] for row in result["row_comparison"]],
                         ["0.001", "0.002"])
        entries = self.service.export(1)["entries"]
        self.assertEqual([row["value"] for row in entries], ["428.00", "856.00"])

    def test_replay_after_new_rule_stays_reproducible(self):
        self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                    "factor": "0.001", "valid_to": "2026-06"})
        batch, manifest = self.ingest("era_one", self.rows(428000, unit="raw_ppb", start=5))
        self.service.register_unit({"indicator": "co2_ppm", "source_unit": "raw_ppb",
                                    "factor": "0.002", "valid_from": "2026-06"})
        result = self.service.replay(manifest, batch["id"])
        self.assertTrue(result["reproducible"])
        self.assertEqual(result["row_comparison"][0]["replayed_rule"]["factor"], "0.001")

    def test_yearly_indicator_intervals(self):
        self.service.register_unit({"indicator": "fire_area", "source_unit": "kha",
                                    "factor": "1000", "valid_to": "2026"})
        self.service.register_unit({"indicator": "fire_area", "source_unit": "kha",
                                    "factor": "1100", "valid_from": "2026"})
        rows = [{"indicator": "fire_area", "period": "2025", "value": "2", "unit": "kha"},
                {"indicator": "fire_area", "period": "2026", "value": "2", "unit": "kha"}]
        batch, _ = self.ingest("yearly_eras", rows)
        self.assertEqual(batch["status"], "accepted")
        rows = self.service.batch(batch["id"])["measurements"]
        self.assertEqual([row["value"] for row in rows], ["2000", "2200"])
