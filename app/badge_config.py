# PCB 版本类型 → badge CSS 类名映射
# 新增 PCB 类型：在此字典添加条目，并在 style.css 中同步添加对应变量和样式类
PCB_TYPE_CLASS: dict[str, str] = {
    "电性件": "badge--px-dianxingjian",
    "电性加强件": "badge--px-dianxingqiangjiajian",
    "鉴定件": "badge--px-jiandingjian",
    "正样": "badge--px-zhengyang",
    "特殊": "badge--px-special",
}


def pcb_badge_class(pcb_type: str) -> str:
    """将 PCB 版本类型映射为对应的 badge CSS 类名，未知类型返回通用样式。"""
    return PCB_TYPE_CLASS.get(pcb_type, "badge--px-unknown")
