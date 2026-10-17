from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app_config import (
    DEFAULTS,
    action_for,
    hotkey_sets,
    read_config_file,
    validate_config,
    write_config_file,
)


class AppConfigTests(unittest.TestCase):
    def configured(self, **changes):
        data = {
            **DEFAULTS,
            "sy_low": "t",
            "xl_low": "y",
            "target_low_key": "f5",
            "target_ally_key": "f6",
            "target_delay_ms": "70",
            "bl1": "b",
            "bl2": "f12",
            **changes,
        }
        return validate_config(data)

    def test_legacy_file_gains_optional_defaults_without_changing_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.ini"
            path.write_text(
                "[SETTINGS]\ntoggle = f4\nsy1 = a\nsy2 = f10\n"
                "xl1 = s\nxl2 = f9\nhx1 = q\nhx2 = f11\n"
                "delay_ms = 60\nloop_interval_s = 1.4\nunknown_setting = ignored\n",
                encoding="utf-8-sig",
            )
            loaded = read_config_file(path)
            cfg = validate_config(loaded)
        self.assertNotIn("unknown_setting", loaded)
        self.assertEqual((cfg["toggle"], cfg["sy1"], cfg["xl1"]), ("f4", "a", "s"))
        self.assertEqual((cfg["sy_low"], cfg["xl_low"], cfg["target_mode"]), ("", "", "none"))
        self.assertEqual(cfg["ally_hx_low"], "0")
        self.assertEqual(action_for(cfg, "a"), ((("f10", 0.0), ("f11", 0.06)), True, 1.4))
        self.assertEqual(action_for(cfg, "s"), ((("f9", 0.0), ("f11", 0.06)), True, 1.4))

    def test_config_write_read_roundtrip_preserves_all_new_controls(self):
        cfg = self.configured(target_mode="ally", ally_hx_low="1", loop_interval_s="1.7")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.ini"
            path.write_text("old contents", encoding="utf-8")
            write_config_file(path, {**cfg, "unrecognized": "discard"})
            loaded = read_config_file(path)
            self.assertEqual(loaded, cfg)
            self.assertFalse(path.with_suffix(".ini.tmp").exists())
        self.assertEqual(validate_config(loaded), cfg)

    def test_missing_or_unrelated_file_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.ini"
            self.assertEqual(read_config_file(path), {})
            path.write_text("[OTHER]\nsy1 = z\n", encoding="utf-8")
            self.assertEqual(read_config_file(path), {})

    def test_keys_are_normalized_before_collision_checks_and_execution(self):
        cfg = self.configured(sy_low=" T ", target_low_key=" F5 ")
        self.assertEqual(cfg["sy_low"], "t")
        self.assertEqual(cfg["target_low_key"], "f5")
        self.assertEqual(action_for(cfg, "t")[0][0], ("f5", 0))

    def test_ordinary_hot_keys_obey_each_target_mode(self):
        for skill, trigger, output in (("上元", "e", "f10"), ("翔舞", "3", "f9")):
            for mode, expected in (
                ("none", ((output, 0), ("f11", 0.04))),
                ("lowest", (("f5", 0), (output, 0.07), ("f11", 0.04))),
                ("ally", (("f6", 0), (output, 0.07), ("f11", 0.04))),
            ):
                with self.subTest(skill=skill, mode=mode):
                    cfg = self.configured(target_mode=mode)
                    self.assertEqual(action_for(cfg, trigger), (expected, True, 1.2))

    def test_ally_hot_can_switch_to_lowest_before_hx(self):
        cfg = self.configured(target_mode="ally", ally_hx_low="1")
        for trigger, output in (("e", "f10"), ("3", "f9")):
            with self.subTest(trigger=trigger):
                self.assertEqual(
                    action_for(cfg, trigger),
                    ((("f6", 0), (output, 0.07), ("f5", 0.04), ("f11", 0.07)), True, 1.2),
                )

    def test_dedicated_lowest_keys_override_all_modes_and_ally_checkbox(self):
        for mode in ("none", "lowest", "ally"):
            for checkbox in ("0", "1"):
                cfg = self.configured(target_mode=mode, ally_hx_low=checkbox)
                for trigger, output in (("t", "f10"), ("y", "f9")):
                    with self.subTest(mode=mode, checkbox=checkbox, trigger=trigger):
                        self.assertEqual(
                            action_for(cfg, trigger),
                            ((("f5", 0), (output, 0.07), ("f11", 0.04)), True, 1.2),
                        )

    def test_ally_checkbox_has_no_effect_on_other_modes(self):
        for mode in ("none", "lowest"):
            with self.subTest(mode=mode):
                unchecked = self.configured(target_mode=mode, ally_hx_low="0")
                checked = self.configured(target_mode=mode, ally_hx_low="1")
                self.assertEqual(action_for(unchecked, "e"), action_for(checked, "e"))

    def test_other_skills_retain_original_key_and_only_append_hx(self):
        for mode in ("none", "lowest", "ally"):
            cfg = self.configured(target_mode=mode, ally_hx_low="1")
            suppressed, watched = hotkey_sets(cfg)
            for trigger in ("r", "2", "b"):
                with self.subTest(mode=mode, trigger=trigger):
                    self.assertNotIn(trigger, suppressed)
                    self.assertIn(trigger, watched)
                    self.assertEqual(action_for(cfg, trigger), ((("f11", 0.04),), False, 1.2))
            # Direct 回雪 remains entirely under the game's own bindings.
            for trigger in ("q", "f11"):
                self.assertNotIn(trigger, suppressed | watched)
                self.assertIsNone(action_for(cfg, trigger))

    def test_only_hot_triggers_are_suppressed(self):
        cfg = self.configured(target_mode="ally", ally_hx_low="1")
        suppressed, watched = hotkey_sets(cfg)
        self.assertEqual(suppressed, {"e", "3", "t", "y"})
        self.assertEqual(watched, {"r", "2", "b"})
        for trigger in ("", "f5", "f6", "f8", "f7", "f12", "z"):
            with self.subTest(trigger=trigger):
                self.assertIsNone(action_for(cfg, trigger))

    def test_default_blank_new_triggers_preserve_legacy_behavior(self):
        cfg = validate_config({})
        self.assertEqual(hotkey_sets(cfg), ({"e", "3"}, {"r", "2"}))
        self.assertEqual(action_for(cfg, "e"), ((("f10", 0), ("f11", 0.04)), True, 1.2))
        self.assertEqual(action_for(cfg, "3"), ((("f9", 0), ("f11", 0.04)), True, 1.2))
        self.assertIsNone(action_for(cfg, ""))

    def test_numeric_fields_reject_invalid_nonfinite_and_out_of_range_values(self):
        for field in ("delay_ms", "target_delay_ms", "loop_interval_s"):
            for value in ("garbage", "nan", "inf", "-inf", "-1", "", "1e100"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.configured(**{field: value})
        for value in ("0", "0.049"):
            with self.subTest(loop_interval=value), self.assertRaises(ValueError):
                self.configured(loop_interval_s=value)
        self.configured(delay_ms="0", target_delay_ms="0", loop_interval_s="0.05")

    def test_timing_upper_limits_are_validated_before_execution(self):
        self.configured(delay_ms="60000", target_delay_ms="60000", loop_interval_s="3600")
        for field, value in (("delay_ms", "60001"), ("target_delay_ms", "60001"),
                             ("loop_interval_s", "3601")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.configured(**{field: value})

    def test_different_skills_cannot_share_game_output_or_direct_hx_key(self):
        for changes in ({"sy2": "f11"}, {"sy2": "f9"}, {"sy2": " Q "},
                        {"wm2": "f7"}, {"bl2": "f8"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.configured(**changes)
        self.assertEqual(self.configured(hx2="q")["hx2"], "q")

    def test_foreground_guard_defaults_off_and_roundtrips(self):
        self.assertEqual(validate_config({})["foreground_guard"], "0")
        cfg = self.configured(foreground_guard="1")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.ini"
            write_config_file(path, cfg)
            self.assertEqual(validate_config(read_config_file(path)), cfg)
        with self.assertRaises(ValueError):
            self.configured(foreground_guard="yes")

    def test_required_selectors_match_enabled_actions(self):
        for changes in (
            {"target_low_key": ""},
            {"sy_low": "", "xl_low": "", "target_mode": "lowest", "target_low_key": ""},
            {"target_mode": "ally", "target_ally_key": ""},
            {"sy_low": "", "xl_low": "", "target_mode": "ally", "ally_hx_low": "1", "target_low_key": ""},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.configured(**changes)
        self.configured(sy_low="", xl_low="", target_mode="none", target_low_key="", target_ally_key="")
        self.configured(sy_low="", xl_low="", target_mode="ally", ally_hx_low="0", target_low_key="")

    def test_dedicated_low_only_does_not_require_unused_ally_selector(self):
        cfg = self.configured(sy1="", xl1="", target_mode="ally", target_ally_key="", ally_hx_low="1")
        self.assertEqual(
            action_for(cfg, "t"),
            ((("f5", 0), ("f10", 0.07), ("f11", 0.04)), True, 1.2),
        )

    def test_no_hot_triggers_do_not_require_selectors(self):
        self.configured(sy1="", xl1="", sy_low="", xl_low="", target_mode="ally",
                        target_low_key="", target_ally_key="", ally_hx_low="1")

    def test_trigger_duplicates_and_output_collisions_are_rejected(self):
        for changes in (
            {"sy_low": " E "},
            {"toggle": "e"},
            {"xl1": "e"},
            {"sy2": "t"},
            {"hx2": "r"},
            {"wm2": "t"},
            {"fx2": "y"},
            {"bl2": "e"},
            {"target_low_key": "e"},
            {"target_low_key": "f10"},
            {"target_ally_key": "q"},
            {"target_ally_key": "f5"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.configured(**changes)

    def test_direct_hx_primary_key_cannot_be_repurposed_as_a_trigger(self):
        for field in ("toggle", "sy1", "xl1", "sy_low", "xl_low", "wm1", "fx1", "bl1"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.configured(**{field: "q"})

    def test_invalid_required_keys_modes_and_checkbox_are_rejected(self):
        for changes in (
            {"toggle": ""},
            {"sy_low": "not-a-key"},
            {"sy2": ""},
            {"xl2": ""},
            {"hx2": ""},
            {"target_mode": "invalid"},
            {"ally_hx_low": "yes"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.configured(**changes)


if __name__ == "__main__":
    unittest.main()
