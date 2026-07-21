"""打包配置契约：datas 目标路径必须与 resource_dir() 的 frozen 根对齐。

resource_dir() 在 frozen 时返回 sys._MEIPASS 本身，其直接子目录须为
templates/ 与 static/。若 .spec 把它们打到 _MEIPASS/app/ 下，开发模式无异常，
但打包产物每个模板都 404。本测试守护这条契约。
"""
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _spec_text() -> str:
    with open(os.path.join(REPO_ROOT, "reflow.spec"), encoding="utf-8") as f:
        return f.read()


def _datas_pairs() -> list[tuple[str, str]]:
    """解析 .spec 里 datas 列表中的 (源, 目标) 二元组。"""
    text = _spec_text()
    m = re.search(r"datas\s*=\s*\[(.*?)\]", text, re.S)
    assert m, "reflow.spec 中未找到 datas 列表"
    return re.findall(r"\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)", m.group(1))


def test_templates_and_static_target_meipass_root():
    """模板与静态文件必须落在打包根目录下，目标路径不含 app/ 前缀。"""
    pairs = dict(_datas_pairs())
    assert pairs.get("app/templates") == "templates", pairs
    assert pairs.get("app/static") == "static", pairs


def test_entry_point_is_desktop_module():
    """打包入口必须是桌面启动器，不是 uvicorn 命令行。"""
    assert "app/desktop.py" in _spec_text()


def test_onedir_mode():
    """必须是 onedir：未签名的单文件 exe 杀软误报率明显更高。"""
    text = _spec_text()
    assert "COLLECT(" in text, "缺少 COLLECT，说明不是 onedir 模式"
