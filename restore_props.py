"""从备份里恢复被删掉的"道具动画"。

背景：为了干掉那个反复出现的"锤子砸头"，我们把动作文件里的道具曲线删掉了
（锤子 / 猫手 / 星星 / 泡泡 / 手机…… 共 135 条），原文件备份在 web/model_backup_props/。
想再要回来的话跑这个脚本。

用法：
    python restore_props.py --list              看看有哪些可以恢复
    python restore_props.py --motions 自拍简单   只恢复指定动作（留空 = 全部）
    python restore_props.py --all               整个模型目录都还原成备份版本
"""

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "web", "model")
BACKUP = os.path.join(HERE, "web", "model_backup_props")


def curve_count(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as fh:
            return len(json.load(fh).get("Curves") or [])
    except Exception:
        return -1


def list_backup() -> None:
    if not os.path.isdir(BACKUP):
        print("没有找到备份目录:", BACKUP)
        return
    print("备份目录:", BACKUP)
    for root, _dirs, files in os.walk(BACKUP):
        for name in sorted(files):
            if not name.endswith(".motion3.json"):
                continue
            rel = os.path.relpath(os.path.join(root, name), BACKUP)
            back = curve_count(os.path.join(BACKUP, rel))
            now = curve_count(os.path.join(MODEL, rel))
            flag = "可恢复" if back > now else "已是最新"
            print("  %-30s 备份 %3d 条曲线 / 现在 %3d 条  → %s" % (rel, back, max(now, 0), flag))


def rebuild_settings() -> None:
    """恢复动作文件之后，把 settings.json 里的动作清单重新生成一遍。"""
    path = os.path.join(MODEL, "settings.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    groups = {
        "Idle": ["motions/idle.motion3.json", "aidale.motion3.json"],
        "Tap": [
            "motions/自拍简单.motion3.json",
            "motions/开盖.motion3.json",
            "motions/喷水.motion3.json",
            "motions/chuipaopao.motion3.json",
        ],
        "Show": ["motions/自拍.motion3.json", "motions/番茄酱.motion3.json"],
    }
    motions = {}
    for group, files in groups.items():
        entries = []
        for rel in files:
            full = os.path.join(MODEL, rel.replace("/", os.sep))
            if os.path.exists(full) and curve_count(full) >= 3:
                entries.append({"File": rel, "FadeInTime": 0.6, "FadeOutTime": 0.6})
        if entries:
            motions[group] = entries
    data.setdefault("FileReferences", {})["Motions"] = motions
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print("动作清单已重建:", {g: len(v) for g, v in motions.items()})


def restore(only: list[str]) -> None:
    if not os.path.isdir(BACKUP):
        print("没有找到备份目录:", BACKUP)
        return
    restored = 0
    for root, _dirs, files in os.walk(BACKUP):
        for name in files:
            if not name.endswith(".motion3.json"):
                continue
            if only and not any(key in name for key in only):
                continue
            rel = os.path.relpath(os.path.join(root, name), BACKUP)
            dst = os.path.join(MODEL, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(BACKUP, rel), dst)
            restored += 1
            print("  已恢复:", rel)
    print("共恢复 %d 个动作文件" % restored)
    rebuild_settings()
    print("重启桌宠后生效（记得在设置里勾选你想看到的动作）。")


def main() -> None:
    parser = argparse.ArgumentParser(description="恢复被删掉的道具动画")
    parser.add_argument("--list", action="store_true", help="列出备份里有什么")
    parser.add_argument("--motions", nargs="*", default=None, help="只恢复名字里含这些关键字的动作")
    parser.add_argument("--all", action="store_true", help="整个模型目录还原成备份版本")
    args = parser.parse_args()

    if args.list:
        list_backup()
        return
    if args.all:
        if not os.path.isdir(BACKUP):
            print("没有找到备份目录:", BACKUP)
            return
        shutil.rmtree(MODEL)
        shutil.copytree(BACKUP, MODEL)
        print("整个模型目录已还原成备份版本（道具动画全部回来）。")
        return
    restore(args.motions or [])


if __name__ == "__main__":
    main()
