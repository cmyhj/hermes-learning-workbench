"""wbl_core — wb-learn 学习模块的内核（不依赖 FastAPI，可单独单测）。

拆分原因：plugin_api.py 只做路由与参数校验，扫描/映射/题库/资料/只读外链各自独立，
这样测试可以直接 import 某一块，不必为了测路径边界去启服务。

模块：
* ``paths``      —— 根目录登记、路径安全（resolve + is_relative_to + 链接/junction 判定）
* ``catalog``    —— 课程/自主学习对象发现 + Obsidian 笔记关联 + 本地映射持久化
* ``quiz_schema``—— 标准题库 JSON schema 校验/归一，以及既有 txt 题库格式解析
* ``quiz_store`` —— quiz-library 读取、草稿 staging、原子保存
* ``materials``  —— 只读资料清单与安全文本预览（格式能力如实声明）
* ``links``      —— 只读 Zotero collections 与 GitHub 仓库
* ``study``      —— 自主学习子条目（论文/仓库/视频/网站）数据模型与 CRUD
* ``records``    —— 点击次数学习记录（原子自增 + 聚合）
* ``repo_ops``   —— 从论文找仓库、克隆、VSCode 打开（显式确认，绝不自动）

## 为什么包内一律用相对导入
Hermes 的 ``plugin_isolation.import_user_code()`` 用
``spec_from_file_location("hermes_dashboard_plugin_<name>", plugin_api.py)`` 执行后端，
即 **plugin_api.py 是顶层模块、没有父包**。所以下面两种常见写法在本插件里都不成立：

* ``from .wbl_core import ...`` —— 无父包，ImportError；
* 裸 ``import wbl_core``      —— 走宿主 sys.path；真存在同名第三方包时会静默加载错的那个。

``plugin_api.py`` 因此按**文件位置**把本包注册成 ``wb_learn_volume_core``
（见其中 ``_load_core_package()``）：不改 sys.path、不会撞名。
本包内部全用相对导入，所以外层模块名叫 ``wbl_core``（测试直接 import）还是
``wb_learn_volume_core``（Hermes 按路径加载）都能正确解析。
"""

from __future__ import annotations

VOLUME_VERSION = "2.0.0"

# 子模块在这里显式导入：让 ``_core.catalog`` 这种按属性取用的写法成立，
# 也让「包本身可用」能被一次 import 验证到，而不是等第一个请求才炸。
from . import catalog, links, materials, paths, quiz_schema, quiz_store, records, repo_ops, study  # noqa: E402,F401

__all__ = [
    "VOLUME_VERSION",
    "catalog",
    "links",
    "materials",
    "paths",
    "quiz_schema",
    "quiz_store",
    "records",
    "repo_ops",
    "study",
]
