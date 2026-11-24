from __future__ import annotations

# YH-ORIGIN: Yukki / ssyb | CFG-01 | publication trace; see NOTICE.md.
import configparser
import math
from pathlib import Path


KEY_OPTIONS = ["", *list("abcdefghijklmnopqrstuvwxyz"), *list("1234567890"),
               *(f"f{i}" for i in range(1, 13)), *(f"num{i}" for i in range(10)), "tab", "space"]
SKILLS = (("上元点鬟", "sy"), ("翔鸾舞柳", "xl"), ("回雪飘摇", "hx"),
          ("风袖低昂", "fx"), ("王母挥袂", "wm"), ("婆罗门", "bl"))
TARGET_MODES = {"none": "不选人", "lowest": "选最低血量", "ally": "选盟友"}
DEFAULTS = {
    "toggle": "f3", "delay_ms": "40", "loop_interval_s": "1.2",
    "hx1": "q", "hx2": "f11", "sy1": "e", "sy2": "f10",
    "xl1": "3", "xl2": "f9", "wm1": "r", "wm2": "f8",
    "fx1": "2", "fx2": "f7", "bl1": "", "bl2": "",
    "sy_low": "", "xl_low": "", "target_mode": "none",
    "target_low_key": "", "target_ally_key": "", "target_delay_ms": "40",
    "ally_hx_low": "0", "foreground_guard": "0",
}
KEY_FIELDS = ("toggle", *(f"{prefix}{n}" for _, prefix in SKILLS for n in (1, 2)),
              "sy_low", "xl_low", "target_low_key", "target_ally_key")
TRIGGER_LABELS = {"toggle": "总开关", "sy1": "上元普通键", "xl1": "翔舞普通键",
                  "sy_low": "上元最低血量键", "xl_low": "翔舞最低血量键",
                  "wm1": "王母普通键", "fx1": "风袖普通键", "bl1": "婆罗门普通键"}

FEITIAN_GROUPS = (
    (("ft_toggle", "刷血总开关", "f3"), ("ft_begin", "非橙武爆发", "f4"),
     ("ft_confirm", "通用确认／恢复", "f5")),
    (("ft_pet", "蝶鸾（宠物栏）", "f10"), ("ft_heal", "醉舞九天", "f11"),
     ("ft_target_key", "选择最低血量队友", "f12")),
    (("ft_self", "选择自己", "f1"), ("ft_snake", "灵蛇引", "1"), ("ft_scorpion", "圣蝎引", "2")),
    (("ft_sacrifice", "蛊虫献祭", "3"), ("ft_nuwa", "女娲补天", "4"), ("ft_butterfly", "碧蝶引", "5")),
)
DEFAULTS.update({key: default for group in FEITIAN_GROUPS for key, _, default in group})
DEFAULTS.update(school="yunchang", ft_loop_ms="100", ft_burst_ms="1500", ft_plant="")
DEFAULTS.update(ft_use_macro="0", ft_macro_key="")
CW_FIELDS = (("ft_cw_begin", "橙武爆发触发键"), ("ft_weapon", "游戏：橙武特效"),
             ("ft_shengshou", "游戏：圣手织天"))
DEFAULTS.update(dict.fromkeys((key for key, _ in CW_FIELDS), ""))


def feitian_steps(cfg, group):
    delay = float(cfg["ft_loop_ms" if group == 1 else "ft_burst_ms"]) / 1000
    return tuple((cfg[key], delay if i else 0.0)
                 for i, (key, _, _) in enumerate(FEITIAN_GROUPS[group]))


def cw_finisher(cfg):
    gap = float(cfg["ft_loop_ms"]) / 1000
    steps = [(cfg["ft_weapon"], 0), (cfg["ft_target_key"], gap)]
    for index in range(4):
        steps.append((cfg["ft_shengshou"], gap if index == 0 else max(gap, 2 - 3 * gap)))
        steps.extend((cfg[key], gap) for key in ("ft_pet", "ft_heal", "ft_target_key"))
    return tuple(steps)


def normalize_key(key: str) -> str:
    return (key or "").strip().lower()


def read_config_file(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig") as stream:
        parser.read_file(stream)
    if not parser.has_section("SETTINGS"):
        return {}
    return {k: v for k, v in parser.items("SETTINGS") if k in DEFAULTS}


def write_config_file(path: Path, data: dict[str, str]) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser["SETTINGS"] = {k: data.get(k, default) for k, default in DEFAULTS.items()}
    # Replace only after the complete file has been written successfully.
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        parser.write(stream)
    temporary.replace(path)


def validate_config(data: dict[str, str]) -> dict[str, str]:
    cfg = {**DEFAULTS, **data}
    if cfg["school"] not in {"yunchang", "feitian"}:
        raise ValueError("请选择有效的流派。")
    used_feitian = {}
    if cfg["ft_use_macro"] not in {"0", "1"}:
        raise ValueError("请选择有效的爆发模式。")
    macro_mode = cfg["ft_use_macro"] == "1"
    for group in FEITIAN_GROUPS:
        for field, label, _ in group:
            key = cfg[field] = normalize_key(cfg[field])
            if macro_mode and group in FEITIAN_GROUPS[2:] and field != "ft_self":
                continue
            if not key or key not in KEY_OPTIONS:
                raise ValueError(f"请设置飞天承华的{label}按键。")
            if key in used_feitian:
                raise ValueError(f"飞天承华：{label}与{used_feitian[key]}不能使用同一个按键。")
            used_feitian[key] = label
    optional = (("ft_plant", "迷仙引梦"), *CW_FIELDS)
    if macro_mode:
        if not normalize_key(cfg["ft_macro_key"]):
            raise ValueError("请设置游戏内爆发宏快捷键。")
        optional += (("ft_macro_key", "游戏内爆发宏"),)
    for field, label in optional:
        key = cfg[field] = normalize_key(cfg[field])
        if key not in KEY_OPTIONS:
            raise ValueError(f"请选择有效的{label}快捷键。")
        if key in used_feitian:
            raise ValueError(f"{label}与{used_feitian[key]}不能使用同一个按键。")
        if key:
            used_feitian[key] = label
    if cfg["ft_cw_begin"] and not (cfg["ft_weapon"] and cfg["ft_shengshou"]):
        raise ValueError("设置橙武爆发触发键后，请同时绑定橙武特效和圣手织天。")
    for field in KEY_FIELDS:
        cfg[field] = normalize_key(cfg[field])
        if cfg[field] not in KEY_OPTIONS:
            raise ValueError(f"不支持的按键：{cfg[field]}")
    if not cfg["toggle"]:
        raise ValueError("请设置总开关键。")
    for field, label, minimum, maximum in (("ft_loop_ms", "刷血按键间隔(ms)", 10, 60000),
                                           ("ft_burst_ms", "爆发按键间隔(ms)", 10, 60000),
                                           ("delay_ms", "接回雪延迟(ms)", 0, 60000),
                                           ("target_delay_ms", "选人后等待(ms)", 0, 60000),
                                           ("loop_interval_s", "二段回雪时间(s)", 0.05, 3600)):
        try:
            value = float(cfg[field])
        except (ValueError, TypeError):
            raise ValueError(f"{label}必须填写数字。") from None
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"{label}必须是 {minimum:g} 至 {maximum:g} 之间的有限数字。")
    if cfg["target_mode"] not in TARGET_MODES:
        raise ValueError("请选择有效的选人模式。")
    if cfg["ally_hx_low"] not in {"0", "1"}:
        raise ValueError("回雪前选最低血量的配置无效。")
    if cfg["foreground_guard"] not in {"0", "1"}:
        raise ValueError("前台窗口保护的配置无效。")

    used: dict[str, str] = {}
    for field, label in TRIGGER_LABELS.items():
        key = cfg[field]
        if not key:
            continue
        if key in used:
            raise ValueError(f"{label}与{used[key]}不能使用同一个按键（{key.upper()}）。")
        used[key] = label
    if cfg["hx1"] and cfg["hx1"] in used:
        raise ValueError(f"{used[cfg['hx1']]}不能占用回雪普通键（{cfg['hx1'].upper()}）。")
    # DD output is not guaranteed to carry the Windows injected flag. Keep its
    # keys separate from physical triggers instead of ignoring real input for 250ms.
    outputs = {cfg[field] for field in (*(f"{p}2" for _, p in SKILLS), "target_low_key", "target_ally_key")}
    for key in outputs - {""}:
        if key in used:
            raise ValueError(f"游戏技能／选人快捷键 {key.upper()} 与{used[key]}冲突，请使用不同按键。")
    # A game key cannot cast two different skills. Include direct HX's primary
    # binding as well as all second bindings; two bindings of HX itself may match.
    game_keys: dict[str, tuple[str, str]] = {}
    for name, prefix in SKILLS:
        fields = (f"{prefix}1", f"{prefix}2") if prefix == "hx" else (f"{prefix}2",)
        for field in fields:
            key = cfg[field]
            if not key:
                continue
            if key in game_keys and game_keys[key][0] != prefix:
                raise ValueError(f"{name}与{game_keys[key][1]}不能使用同一个游戏快捷键（{key.upper()}）。")
            game_keys[key] = (prefix, name)
    if cfg["target_low_key"] and cfg["target_low_key"] == cfg["target_ally_key"]:
        raise ValueError("最低血量与选择盟友需要设置不同的游戏快捷键。")
    for target in ("target_low_key", "target_ally_key"):
        if cfg[target] and cfg[target] in {cfg[f"{p}{n}"] for _, p in SKILLS for n in (1, 2)}:
            raise ValueError("游戏选人快捷键不能与游戏技能键相同。")
    for prefix, label in (("sy", "上元"), ("xl", "翔舞")):
        if cfg[f"{prefix}1"] or cfg[f"{prefix}_low"]:
            if not cfg[f"{prefix}2"] or not cfg["hx2"]:
                raise ValueError(f"设置{label}触发键后，请同时设置该技能和回雪的游戏第二快捷键。")
    if any(cfg[f"{p}1"] for p in ("fx", "wm", "bl")) and not cfg["hx2"]:
        raise ValueError("请设置接回雪使用的游戏第二快捷键。")
    uses_ordinary_hot = bool(cfg["sy1"] or cfg["xl1"])
    low_needed = bool(cfg["sy_low"] or cfg["xl_low"] or
                      (uses_ordinary_hot and cfg["target_mode"] == "lowest") or
                      (uses_ordinary_hot and cfg["target_mode"] == "ally" and cfg["ally_hx_low"] == "1"))
    if low_needed and not cfg["target_low_key"]:
        raise ValueError("请填写游戏中“选择最低血量”的快捷键。")
    if uses_ordinary_hot and cfg["target_mode"] == "ally" and not cfg["target_ally_key"]:
        raise ValueError("请填写游戏中“选择盟友”的快捷键。")
    return cfg


def hotkey_sets(cfg: dict[str, str]) -> tuple[set[str], set[str]]:
    suppressed = {cfg[k] for k in ("sy1", "xl1", "sy_low", "xl_low") if cfg[k]}
    watched = {cfg[k] for k in ("wm1", "fx1", "bl1") if cfg[k]}
    return suppressed, watched


def action_for(cfg: dict[str, str], trigger: str):
    # Yukki / ssyb [CFG-02]: keep target selection and skill order in one plan.
    """Return immutable key/delay steps, repeat flag and repeat interval."""
    delay = float(cfg["delay_ms"]) / 1000.0
    select_delay = float(cfg["target_delay_ms"]) / 1000.0
    for prefix in ("sy", "xl"):
        ordinary, lowest = cfg[f"{prefix}1"], cfg[f"{prefix}_low"]
        if trigger and trigger in {ordinary, lowest}:
            mode = "lowest" if trigger == lowest else cfg["target_mode"]
            selector = cfg["target_low_key"] if mode == "lowest" else cfg["target_ally_key"] if mode == "ally" else ""
            steps = []
            if selector:
                steps.append((selector, 0.0))
            steps.append((cfg[f"{prefix}2"], select_delay if selector else 0.0))
            if mode == "ally" and cfg["ally_hx_low"] == "1":
                steps.extend(((cfg["target_low_key"], delay), (cfg["hx2"], select_delay)))
            else:
                steps.append((cfg["hx2"], delay))
            return tuple(steps), True, float(cfg["loop_interval_s"])
    if trigger and trigger in {cfg[k] for k in ("wm1", "fx1", "bl1")}:
        return ((cfg["hx2"], delay),), False, float(cfg["loop_interval_s"])
    return None
