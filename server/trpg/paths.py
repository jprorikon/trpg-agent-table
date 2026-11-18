"""套件共用的路徑常數（唯一從 `__file__` 推 repo 根的地方）。"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "vendor")
