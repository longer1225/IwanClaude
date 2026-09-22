"""
影子快照模块 - Layer 4 文件回滚（S9 Part C，设计见 docs/plans/S9_security_rollback_plan.md §5）

【学习要点】
1. 卡口选在 invoke_tool 而非各工具内部：十个写工具共用同一个执行咽喉，
   在这里挂钩意味着"新写工具零成本纳入回滚"（只需实现既有的
   estimate_affected_paths），忘记接线的可能性 = 0
2. 快照发生在首次尝试之前、重试循环之外：如果在 attempt-1 失败后重拍，
   抓到的已是半损坏状态——回滚语义当场作废（"写坏了还能救"变成空话）
3. commit 只在成功路径执行：写失败不留账（"没发生的事"不该出现在
   file_changes.json 里）；写成功必有快照（prepare 在调用前已完成）
4. 内容寻址（objects/<sha256>）：同一文件的同一版本被多次改写只存一份，
   跨文件相同内容也去重——磁盘占用与"改动次数"脱钩，只与"不同内容数"相关
"""

from iwan_claude.core.shadow.store import (
    ShadowStore,
    get_active_shadow,
    reset_active_shadow,
    set_active_shadow,
)

__all__ = ["ShadowStore", "get_active_shadow", "set_active_shadow", "reset_active_shadow"]
