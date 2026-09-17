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
#   paths   —— 视频目录 / 片头目录 / 片尾目录（上次选择的路径）
#   fission —— 片头/片尾/混淆三个独立开关 + 数量 + 混淆份数
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
    """
    default = json.loads(json.dumps(DEFAULT_SETTINGS))     # 深拷贝一份默认配置
    try:
        with open(config_file_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return default

    merged = _deep_merge(default, data)
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
