# `rules.example` —— 模組層規則包範例

本包示範**模組層機制包**的標準結構。同模組下的所有劇本可直接在 `config.example.json` 的 `packs.enabled` 中啟用：

```json
"packs": {
  "enabled": ["d20core", "board", "semic", "mode.turn", "rules.example", "my_world"]
}
```

## 提供的內容

1. `tables/skills.json`：技能表（定義消耗、難度、效果）
2. `tables/items.json`：道具表（定義回復、消耗效果）
3. `tables/levels.json`：等級成長表（經驗累積與升級屬性加成）
4. `tables/stars.json`：升星判據表（基於劇情事件的 AST 升星條件）
5. `text.json`：技能與升級相關提示文案
