"""packs 的共用小工具（模型與合併都用到；不依賴 Pack／PackView，避免循環）。"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Tuple

from trpg.rules.pack_kernel import PackError


def _canon(value: Any) -> str:
    """穩定序列化（算 hash 用；鍵序與空白不影響）。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _as_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    return [str(x).strip() for x in raw if str(x).strip()]


def _deep_fill(base: Optional[Dict[str, Any]], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """把 `defaults` **補進** `base` 的缺口（**base 有的鍵一律贏**；同為物件才往下遞迴）。

    深合併是必要的：`rules.check.dc_default` 這種要能被單獨覆寫，不能整段被 config 蓋掉
    （淺合併會讓 config 只寫 `check.dc_default` 時把 `check.die` 弄丟）。值一律 deepcopy——
    免得動到包本身的資料。
    """
    out: Dict[str, Any] = dict(base or {})
    for k, v in (defaults or {}).items():
        if k not in out:
            out[k] = copy.deepcopy(v)
        elif isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_fill(out[k], v)
    return out


def _merge_defaults(packs: List[Pack]) -> Dict[str, Any]:
    """所有包的 `defaults` 併起來（**節拍包的 `pacing`／`turn` 也算**；同段同鍵不同值 ⇒ 當場叫）。"""
    out: Dict[str, Any] = {}
    for p in packs:
        blocks: Dict[str, Any] = {k: v for k, v in p.defaults.items()}
        m = p.mode
        if m:
            for sec in ("pacing", "turn"):
                if isinstance(m.get(sec), dict):
                    blocks[sec] = _deep_fill(blocks.get(sec) or {}, m[sec])
        for sec, blk in blocks.items():
            if not isinstance(blk, dict):
                continue
            cur = out.get(sec)
            if cur is None:
                out[sec] = {k: copy.deepcopy(v) for k, v in blk.items()}
                continue
            for k, v in blk.items():
                if k in cur and cur[k] != v:
                    raise PackError(
                        f"預設 {sec}.{k} 被兩個包寫成不同值（{p.id}）——"
                        f"① 同一個家有兩個作者：留一個；"
                        f"② 你要換**整套規則**（例如 d20core ↔ 類 DND）：這是兩個互斥的規則包，"
                        f"請在其中一個宣告 `conflicts`（見 packs/README.md「換整套規則」）")
                cur.setdefault(k, copy.deepcopy(v))
    return out


def _union(packs: List[Pack], key: str) -> Tuple[str, ...]:
    out: List[str] = []
    for p in packs:
        for x in _as_list(p.provides.get(key)):
            if x not in out:
                out.append(x)
    return tuple(out)


def _merge_actions(packs: List[Pack]) -> List[Dict[str, Any]]:
    """動作條目並集（同 id 只留第一份；後面的包要覆寫文案＝**同名包的劇本層覆蓋**）。"""
    out: List[Dict[str, Any]] = []
    seen: Dict[str, Dict[str, Any]] = {}
    for p in packs:
        for it in p.provides.get("actions") or []:
            if isinstance(it, dict):
                ent = {k: v for k, v in it.items() if k in ("id", "label", "desc", "modes")}
                ent["id"] = str(ent.get("id") or "")
                ent.setdefault("label", "")
                ent.setdefault("desc", "")
            else:
                ent = {"id": str(it), "label": "", "desc": ""}
            if not ent["id"] or ent["id"] in seen:
                continue
            seen[ent["id"]] = ent
            out.append(ent)
    return out

