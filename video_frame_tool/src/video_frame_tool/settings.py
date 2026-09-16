"""用户配置读写（settings.json）。

按 DEFAULT_SETTINGS 做深合并，老配置缺字段自动取默认值，不需要版本迁移。"""

import json
import os

from .constants import DEFAULT_SETTINGS
from .platform_compat import config_file_path

# ============================================================================
# 一·五、用户配置读写（"记住上次选择"的核心实现）
# ============================================================================
# 配置文件位置由 user_config_dir() 按平台决定，内容为 JSON：
#   paths   —— 首图目录 / 主图目录 / 视频目录 / 画中画目录（上次选择的路径）
#   pip     —— 画中画几何、掐头去尾、加速
#   random  —— 抗查重随机化开关
#   run     —— 并发数、编码档位、首帧叠加
# 任何路径都不写死在代码里，全部由用户选择后记录、下次启动回填。
# ============================================================================

def _deep_merge(base, override):
    """
    递归合并字典：override 里有的键覆盖 base，没有的沿用 base。
    用于兼容"旧版本配置文件缺少新字段"的情况。
    """
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings():
    """
    读取用户配置；文件不存在或内容损坏时返回默认配置。
    返回值结构与 DEFAULT_SETTINGS 完全一致（缺失字段自动补默认）。

    兼容性处理：老版本用 run.include_first（布尔：是否在首帧也叠加产品图），
    新版本改为 run.prod_start（从第几帧开始叠加）。读取到老配置时换算过去，
    这样用户的旧选择不会丢。
    """
    default = json.loads(json.dumps(DEFAULT_SETTINGS))     # 深拷贝一份默认配置
    try:
        with open(config_file_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return default

    merged = _deep_merge(default, data)
    try:
        raw_run = data.get("run", {}) if isinstance(data, dict) else {}
        if "prod_start" not in raw_run and raw_run.get("include_first"):
            merged["run"]["prod_start"] = 0                # 老配置勾了"首帧也叠加" → 从第 0 帧起
    except Exception:
        pass
    return merged


def save_settings(data):
    """
    原子写入用户配置：先写 .tmp 再 os.replace，避免写一半崩溃导致配置损坏。
    保存失败不影响主流程（返回 False 即可，不抛异常）。
    """
    path = config_file_path()
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False
