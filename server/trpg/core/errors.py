#!/usr/bin/env python3
"""桌子的共用异常类（各层都抛、各入口都接，**同一個類**）。

`RuleError`＝规则拒绝（engine 与 semic 都要抛它）；`TellError`＝私讯验证失败
（REST 依 `status` 回码、MCP 转成工具错误文字——同一個行為不養兩份）

为什么单独一个模块：`semic.py` 是被 `engine.py` import 的（mixin），
semic 不能再 import engine（会循环）。两边各自定义一个同名 ValueError 的后果是
——server 只 `except engine.RuleError`，semic 抛的那个掉进 `except Exception`，
规则拒绝变成 **HTTP 500 ＋ 内部类名前缀**（前端/监控分不清「你坏了我」与「你不能做」）。

把它放这里：engine 与 semic 都 `from errors import RuleError`，同一个类。
"""

from __future__ import annotations


class RuleError(ValueError):
    """调用方参数/规则问题（动作不在列表、额度用完、条件不满足）——不是引擎故障。"""


class TellError(Exception):
    """私訊（`/api/tell` 與 MCP `table_tell`）的驗證失敗。

    `status` 給 REST 端決定回碼（400／403／404／409），MCP 端只把它當一行錯誤文字
    ——同一個行為、同一份驗證，不養第二套。
    """

    def __init__(self, msg: str, status: int = 400) -> None:
        super().__init__(msg)
        self.status = status
