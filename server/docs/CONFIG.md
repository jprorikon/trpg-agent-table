# 規則與配置（`config.json`）

> 從 `README.md` 拆出（2026-10-20）。全部旋鈕與預設＝`config.example.json`；機制包怎麼覆蓋＝`packs/README.md`。

## 规则与配置（`config.json`）

```jsonc
"rules": {
  "name": "minimal-d20",
  "attributes": ["str","agi","wil"], "attr_default": 2, "attr_range": [1,5],
  "hp":     {"base": 8, "per_str": 2},                  // HP = base + per_str*str
  "check":  {"die": 20, "attr_multiplier": 2, "dc_default": 12},  // d20 + 属性*2 vs DC
  "damage": {"die": 6, "attr": "str", "attr_multiplier": 1},      // 伤害 = d6 + str
  "defend_bonus": 3,                                    // 举盾期间被打，DC +3
  "actions": ["move","attack","check","defend","use","other"]
},
"turn":  {"seconds": 150, "policy_on_timeout": "wait",   // wait|pass|default_act
          "wait_extra_seconds": 90, "max_waits": 2,
          "end_turn_on_action": true,
          "auto_start_first_turn": true, "min_seats_to_start": 2},
"seed": 20261005,        // 骰子种子：同 seed + 同 roll_seq ⇒ 同一个骰值（可复算/可复盘）
"auth": {"gm_token": "..."}
```
换规则系统（比如接现成 SRD）＝ 改这段配置 + 在 `trpg/engine/` 里加结算分支，**不动上层**。
