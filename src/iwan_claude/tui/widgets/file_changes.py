# -*- coding: utf-8 -*-  # noqa: UP009 - 与同目录 widget 模块保持声明一致
# ---------------------------------------------------------------------------
# 文件变更回滚面板控件（S9 Part C2：CLI/TUI 双确认面板的 TUI 侧）
# ---------------------------------------------------------------------------
# 【学习要点】
# 1. 与 PermissionSelect/TrustSelect 同族但焦点策略相反：这个面板是用户
#    主动召唤的（F7 / /files），所以挂载即 focus——和被动弹窗"绝不抢焦点"
#    是同一条设计原则（谁发起谁持有键盘）的两个方向。
# 2. 还原是"批量 + 显式确认"两段：勾选（space/a）只改面板状态，真正的
#    files.restore RPC 只在 r/f 落键后由宿主 App 发出——控件层零 IPC，
#    和审批控件一样只 post_message，协议耦合全部留在 app 侧一份实现。
# 3. 冲突行不挡路但要说清：conflict 文件照样可选，force 档（f 键）是独立
#    入口——面板不做父集判断，跳过/强行的裁决权留给 ShadowStore.restore，
#    UI 只负责把 flag 亮出来让人做决定。
# ---------------------------------------------------------------------------

from __future__ import annotations

import logging
from typing import Any

from textual import events
from textual.message import Message
from textual.widgets import Static

log = logging.getLogger(__name__)


class FileChangesSelect(Static):
    """
    文件变更列表 + 勾选还原面板（数据由宿主 App 经 files.changes 注入）

    行格式：❯ [x] 路径  [标记...]  (工具)。无勾选时 r/f 作用于全部行——
    "直接按 r"等于"回滚这一 run 的全部文件变更"，与 CLI 的 restore 默认一致。
    """

    # 用户召唤型面板：挂载即抢焦点（与 TrustSelect 的被动不聚焦相反）
    can_focus = True

    # 自动高度内联样式，与其余审批控件一致
    DEFAULT_CSS = """
    FileChangesSelect {
        height: auto;
        padding: 0 2;
        margin-bottom: 1;
    }
    """

    class RestoreRequested(Message):
        """用户请求还原：paths 为勾中的原始路径（空 = 全部），force 为冲突强推档"""

        # 打包宿主 App 发 files.restore 所需的全部参数
        def __init__(self, widget: FileChangesSelect, run_id: str,
                     paths: list[str], force: bool) -> None:
            self.widget = widget
            self.run_id = run_id
            self.paths = paths
            self.force = force
            super().__init__()

    class Dismissed(Message):
        """esc 关闭面板：宿主 App 负责 remove + 归还焦点给输入框"""

        # 只带控件引用
        def __init__(self, widget: FileChangesSelect) -> None:
            self.widget = widget
            super().__init__()

    # 注入 run_id 与 changes（files.changes 结果的 dict 列表）
    def __init__(self, run_id: str, changes: list[dict[str, Any]]) -> None:
        super().__init__("")
        self._run_id = run_id
        self._changes: list[dict[str, Any]] = list(changes)
        self._cursor = 0
        self._selected: set[int] = set()

    # 挂载：渲染并 focus——键盘归面板
    def on_mount(self) -> None:
        self.update(self._render_ui())
        self.focus()

    # 还原后刷新：换数据并夹紧光标/勾选，避免越界指向已消失的行
    def set_changes(self, run_id: str, changes: list[dict[str, Any]]) -> None:
        self._run_id = run_id
        self._changes = list(changes)
        self._cursor = min(self._cursor, max(len(self._changes) - 1, 0))
        self._selected = {i for i in self._selected if i < len(self._changes)}
        self.update(self._render_ui())

    # 当前 run_id（宿主 App 刷新结果后配对用）
    @property
    def run_id(self) -> str:
        return self._run_id

    # 行尾标记：新建/未拍全/外部改动——还原前必须让人看见的三个真相
    @staticmethod
    def _flags(c: dict[str, Any]) -> str:
        flags: list[str] = []
        if c.get("was_new"):
            flags.append("新建")
        if not c.get("captured", True):
            flags.append("未拍全")
        if c.get("conflict"):
            flags.append("外部改动")
        return f"  [yellow][{','.join(flags)}][/yellow]" if flags else ""

    # 生成标题 + 全行列表 + 键位提示
    def _render_ui(self) -> str:
        head = (f"[bold cyan]📁 文件变更[/bold cyan]  "
                f"[dim]run={self._run_id or '-'}[/dim]  "
                f"({len(self._changes)} 项)")
        lines = [head]
        if not self._changes:
            lines.append("[dim]  （无记录）[/dim]")
        for i, c in enumerate(self._changes):
            cursor = "❯" if i == self._cursor else " "
            box = "x" if i in self._selected else " "
            sel = "[bold cyan]" if i == self._cursor else ""
            end = "[/bold cyan]" if i == self._cursor else ""
            lines.append(
                f"  {cursor} [{box}] {sel}{c.get('path', '')}{end}"
                f"{self._flags(c)}  [dim]({c.get('tool', '')})[/dim]"
            )
        lines.append(
            "[dim]  ↑↓ 移动   space 勾选   a 全选/清空   r 还原勾中(未勾=全部)   "
            "f 强制还原(含外部改动)   esc 关闭[/dim]"
        )
        return "\n".join(lines)

    # 键盘路由：导航 / 勾选 / 还原 / 关闭（仅聚焦时到达，见 on_mount focus）
    def on_key(self, event: events.Key) -> None:
        key = event.key
        if key in ("up", "k"):
            event.stop()
            if self._changes:
                self._cursor = (self._cursor - 1) % len(self._changes)
                self.update(self._render_ui())
        elif key in ("down", "j"):
            event.stop()
            if self._changes:
                self._cursor = (self._cursor + 1) % len(self._changes)
                self.update(self._render_ui())
        elif key in ("space", "enter"):
            event.stop()
            if self._changes:
                if self._cursor in self._selected:
                    self._selected.discard(self._cursor)
                else:
                    self._selected.add(self._cursor)
                self.update(self._render_ui())
        elif key == "a":
            event.stop()
            # 全选 ↔ 清空：一次 a 就能在"全回滚"与"逐个挑"之间切换
            if len(self._selected) == len(self._changes):
                self._selected.clear()
            else:
                self._selected = set(range(len(self._changes)))
            self.update(self._render_ui())
        elif key == "r":
            event.stop()
            self._request_restore(force=False)
        elif key == "f":
            event.stop()
            self._request_restore(force=True)
        elif key == "escape":
            event.stop()
            self.post_message(self.Dismissed(self))

    # 汇算目标路径（未勾 = 全部）并发布 RestoreRequested；空列表直接忽略
    def _request_restore(self, *, force: bool) -> None:
        idxs = sorted(self._selected) if self._selected else list(
            range(len(self._changes)))
        paths = [str(self._changes[i].get("path", "")) for i in idxs]
        paths = [p for p in paths if p]
        if not paths:
            return
        log.debug("FileChangesSelect restore run=%s paths=%d force=%s",
                  self._run_id, len(paths), force)
        self.post_message(self.RestoreRequested(self, self._run_id, paths, force))
