"""loading（自 Scenario 拆出）。"""

from __future__ import annotations
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
from trpg.rules import expr as expr
from trpg.engine import presentation as presentation
from .loader import ModuleError, _node_of, _pack_ctx, resolve_module_path, resolve_seat
from .common import EFFECT_KINDS, EFFECT_SPECS, NUMERIC_PRESET_KEYS


class ScenarioLoadingMixin:
    @classmethod
    def load(cls, home: str, *, name: Optional[str] = None,
             allowed_effects: Optional[Any] = None,
             bank: Optional[Dict[str, str]] = None) -> Optional["Scenario"]:
        """讀這一張桌的**劇本規則資料**（`scenario.json`；**舊名 `module.json` 相容**）。

        * `name` 省略＝自動解析（新名優先；退回舊名時往 stderr 留一行 warn，作者該改新名）。
        * `name` 明給＝照那個檔名讀（測試／工具要指定時用）。
        * **兩個都沒有＝沒有模組**（舊劇本照舊跑，判據走 legacy 分支）。

        `allowed_effects`＝這張桌啟用的機制包宣告的 effect（L0 傳進來）；沒給＝內核整池。
        """
        if name:
            p = os.path.join(home, name)
            if not os.path.exists(p):
                return None
            label = name
        else:
            p, used_old = resolve_module_path(home)
            if not p:
                return None
            label = os.path.basename(p)
            if used_old:
                print(f"[scenario] {home}：讀到舊名 module.json"
                      f"（權威名字是 scenario.json，建議改名）", file=sys.stderr)
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        sc = cls(data, path=p, allowed_effects=allowed_effects, bank=bank)
        errs = sc.validate()
        if errs:
            raise ModuleError(f"{label} 不合法：\n  - " + "\n  - ".join(errs[:12]))
        return sc


    def validate(self) -> List[str]:
        """回所有問題（空＝合法）。作者改完自己跑 `tools/check_campaign.py` 看同一份。"""
        d = self.data
        if not isinstance(d, dict):
            return ["最外層要是物件"]
        e: List[str] = []
        for block in ("requirements", "surface"):
            for key, raw in (d.get(block) or {}).items():
                e += [f"{block}.{key}: {x}" for x in expr.validate(_node_of(raw))]
        for i, t in enumerate(d.get("triggers") or []):
            where = f"triggers[{i}]({t.get('id') if isinstance(t, dict) else '?'})"
            if not isinstance(t, dict) or not t.get("id"):
                e.append(f"{where}: 每個觸發要有 id")
                continue
            if "when" in t:
                e += [f"{where}.when: {x}" for x in expr.validate(t["when"])]
            for j, eff in enumerate(t.get("then") or []):
                e += self._check_effect(eff, f"{where}.then[{j}]")
            if not (t.get("then")):
                e.append(f"{where}: then 不可以是空的")
        for i, en in enumerate(d.get("endings") or []):
            where = f"endings[{i}]({en.get('id') if isinstance(en, dict) else '?'})"
            if not isinstance(en, dict) or not en.get("id"):
                e.append(f"{where}: 每個結局要有 id")
                continue
            if en.get("kind") not in ("win", "lose", "neutral", None):
                e.append(f"{where}: kind 只能是 win／lose／neutral")
            e += [f"{where}.when: {x}" for x in expr.validate(en.get("when"))]
        for name, preset in (d.get("difficulty_presets") or {}).items():
            if not isinstance(preset, dict):
                e.append(f"difficulty_presets.{name}: 要是物件")
                continue
            for k, v in preset.items():
                if k in NUMERIC_PRESET_KEYS and not isinstance(v, (int, float)):
                    e.append(f"difficulty_presets.{name}.{k}: 要是數字")
        return e


    def _check_effect(self, eff: Any, where: str) -> List[str]:
        if not isinstance(eff, dict) or len(eff) != 1:
            return [f"{where}: effect 要是**單鍵**物件（{ '/'.join(EFFECT_KINDS) }）"]
        kind = next(iter(eff))
        if kind not in self.allowed:
            extra = "" if self.allowed == EFFECT_KINDS else \
                f"；內核有 {'/'.join(EFFECT_KINDS)}，但這張桌沒啟用會用到它的機制包"
            return [f"{where}: 不認識的 effect {kind!r}（可用：{'/'.join(self.allowed)}）{extra}"]
        body = eff[kind]
        if kind == "emit":
            if not isinstance(body, dict) or not body.get("type"):
                return [f"{where}.emit: 要物件且帶 type"]
            if body.get("text_key"):
                try:                                   # 句庫要有這把鑰匙（錯字當場叫）
                    presentation.text(str(body["text_key"]), self.bank)
                except KeyError:
                    return [f"{where}.emit.text_key: 句庫沒有 {body['text_key']!r}"]
        if kind == "fact_set" and not (isinstance(body, dict) and body.get("key")):
            return [f"{where}.fact_set: 要 {{key, value}}"]
        if kind == "ending" and not (isinstance(body, dict) and body.get("id")):
            return [f"{where}.ending: 要 {{id}}"]
        if kind == "difficulty_set" and not (isinstance(body, dict) and body.get("name")):
            return [f"{where}.difficulty_set: 要 {{name}}"]
        if kind == "condition_set":
            if not (isinstance(body, dict) and body.get("id")):
                return [f"{where}.condition_set: 要 {{id}}（＋可選 seat／value／turns／note）"]
            if not str(body.get("seat") or "me").strip():
                return [f"{where}.condition_set.seat: 空字串（省略＝me）"]
        # 通用形狀檢查（表驅動）：上面的專屬檢查沒抓到的，這裡按 `EFFECT_SPECS` 補
        spec = EFFECT_SPECS.get(kind) or {}
        for k in spec.get("required", ()):
            if not (isinstance(body, dict) and body.get(k) not in (None, "")):
                return [f"{where}.{kind}: 缺 {k}（要 {'／'.join(spec['required'])}"
                        f"{'＋可選 ' + '／'.join(spec['optional']) if spec.get('optional') else ''}）"]
        return []

