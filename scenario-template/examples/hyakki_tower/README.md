# 百鬼塔（完整真例）

這是一份**可以直接起一張桌**的模組，形狀與引擎的三層一致：

    module.json  characters.json          世界層（模組名／角色表）
    scenarios/main/                        劇本層（定義，共享）
        scenario.json board.json seats.json goals.json clues.json scene1.json
        config.example.json                桌的旋鈕底稿（起桌時抄一份成 config.json）
        packs/hyakki_world/                作品層包（怪物／道具／商店表）

起桌（引擎側）：把整個 `hyakki_tower/` 放到引擎的 `modules/` 底下，然後在前台按「＋ 開一張新桌」，
或 `python3 tools/migrate_to_modules.py` 那類工具（見引擎倉 README）。
