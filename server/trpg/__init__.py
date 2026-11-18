"""TRPG 桌服務的 Python 套件（按功用分層；入口仍在 repo 根）。

`vendor/`（graphql-core／pg8000）跟著套件一起掛進 `sys.path`——任何 `import trpg.*`
都保證第三方依賴找得到，不必各模組自己插路徑。
"""
import os
import sys

from .paths import VENDOR

if os.path.isdir(VENDOR) and VENDOR not in sys.path:
    sys.path.append(VENDOR)
