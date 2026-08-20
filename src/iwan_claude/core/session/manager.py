"""
会话管理器模块 - 管理会话的创建、消息处理和状态转换

【学习要点】
1. 会话管理：管理会话的生命周期（创建、发送消息、关闭）
2. 并发控制：使用 asyncio.Lock 确保同一会话的并发安全
3. 事件发布：通过 EventBus 发布会话相关事件
4. Skill 解析：检测并应用 Skill（自动触发和手动触发）
5. 文件引用：支持 @filename 语法自动读取文件内容
6. Checkpoint 管理：支持会话状态的保存和恢复

【会话状态转换】
```
创建(active) -> 发送消息 -> 处理中(active) -> 等待输入(waiting_for_input)
                                              -> 关闭(closed) [one_shot 模式]
                      -> 关闭(closed) [手动关闭]
```

【错误码定义】
- SESSION_NOT_FOUND (-32010): 会话不存在
- SESSION_CLOSED (-32011): 会话已关闭
- SESSION_BUSY (-32012): 会话正在处理中

【设计特点】
- 使用内存字典存储会话对象，文件系统持久化
- 使用 asyncio.Lock 实现会话级别的并发控制
- 通过 EventBus 发布事件，实现解耦
- 支持 Skill 的自动匹配和手动触发
- 支持 @filename 语法自动读取文件内容
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from iwan_claude.core.bus.envelope import HandlerError
from iwan_claude.core.bus.events import (
    SessionClosedEvent,
    SessionCreatedEvent,
    SessionMessageReceivedEvent,
    SessionResumedEvent,
    SessionWaitingForInputEvent,
    SkillInvokedEvent,
)
from iwan_claude.core.events.bus import EventBus
from iwan_claude.core.runs import new_run_id
from iwan_claude.core.session.model import Session, SessionMode
from iwan_claude.core.snapshot import format_recovery_context, read_snapshot
from iwan_claude.core.session.store import SessionStore
from iwan_claude.core.skills.loader import SkillLoader

# 类型检查时导入（避免循环导入）
if TYPE_CHECKING:
    from iwan_claude.core.llm.base import LLMProvider
    from iwan_claude.core.memory.manager import MemoryManager
    from iwan_claude.core.runner import AgentRunner

# 会话错误码定义
SESSION_NOT_FOUND = -32010
SESSION_CLOSED = -32011
SESSION_BUSY = -32012

log = logging.getLogger(__name__)


def _now() -> str:
    """
    返回当前 UTC 时间的 ISO 8601 字符串

    【返回值】
    - str: 当前 UTC 时间的 ISO 8601 格式字符串

    【设计目的】
    统一时间格式，便于存储和比较
    """
    return datetime.now(UTC).isoformat()


@dataclass
class SendMessageResult:
    """
    send_message 的返回值 - 支持技能确认流程

    【两种场景】
    1. 正常执行：run_id 非空，skill_match 为 None
    2. 技能待确认：run_id 为空，skill_match 包含匹配信息
       TUI 弹出确认控件，用户确认后重新调用 send_message(skill_name=...)
    """
    run_id: str = ""
    skill_match: dict[str, Any] | None = None


class SessionManager:
    """
    会话管理器 - 管理会话的生命周期

    【学习要点】
    1. 会话管理：创建、发送消息、关闭会话
    2. 并发控制：使用 asyncio.Lock 确保同一会话的并发安全
    3. 事件发布：通过 EventBus 发布会话相关事件
    4. Skill 解析：检测并应用 Skill（自动触发和手动触发）
    5. 文件引用：支持 @filename 语法自动读取文件内容
    6. Checkpoint 管理：支持会话状态的保存和恢复

    【核心组件】
    - _store: SessionStore - 会话存储管理器
    - _runner_factory: Callable - AgentRunner 工厂函数
    - _bus: EventBus - 事件总线
    - _provider: LLMProvider - LLM 提供商（用于消息压缩）
    - _sessions: dict - 内存中的会话字典
    - _locks: dict - 会话级别的锁字典
    - _skill_loader: SkillLoader - Skill 加载器

    【使用示例】
    ```python
    from iwan_claude.core.session.manager import SessionManager
    from iwan_claude.core.session.store import SessionStore
    
    store = SessionStore(Path("~/.iwan_claude/sessions"))
    manager = SessionManager(
        store=store,
        runner_factory=lambda: AgentRunner(),
        bus=EventBus(),
    )
    
    # 创建会话
    session = await manager.create("chat")
    
    # 发送消息
    run_id = await manager.send_message(session.id, "Hello")
    
    # 关闭会话
    await manager.close(session.id)
    ```
    """

    def __init__(
        self,
        store: SessionStore,
        runner_factory: Callable[[], AgentRunner],
        bus: EventBus,
        provider: LLMProvider | None = None,
        memory_manager: MemoryManager | None = None,
    ) -> None:
        """
        初始化会话管理器

        【参数说明】
        - store: SessionStore - 会话存储管理器
        - runner_factory: Callable[[], AgentRunner] - AgentRunner 工厂函数
        - bus: EventBus - 事件总线
        - provider: LLMProvider | None - LLM 提供商（用于消息压缩）

        【执行流程】
        1. 保存传入的依赖组件
        2. 初始化内存会话字典（空）
        3. 初始化会话锁字典（空）
        4. 创建 SkillLoader 实例

        【设计说明】
        使用依赖注入模式，便于测试和扩展
        - store: 负责会话数据的持久化存储
        - runner_factory: 用于创建新的 AgentRunner 实例
        - bus: 用于发布会话相关事件
        - provider: 用于消息压缩功能（可选）
        """
        # 会话存储管理器
        self._store = store
        # AgentRunner 工厂函数
        self._runner_factory = runner_factory
        # 事件总线
        self._bus = bus
        # LLM 提供商（用于消息压缩）
        self._provider = provider
        # 跨会话记忆管理器（可选）：会话结束后存储对话，供后续检索
        self._memory_manager = memory_manager
        # 内存中的会话字典（键为会话 ID，值为 Session 对象）
        self._sessions: dict[str, Session] = {}
        # 会话级别的锁字典（键为会话 ID，值为 asyncio.Lock）
        self._locks: dict[str, asyncio.Lock] = {}
        # Skill 加载器
        self._skill_loader = SkillLoader()

    async def create(self, mode: SessionMode, title: str = "", cwd: str = "") -> Session:
        """
        创建新会话

        【参数说明】
        - mode: SessionMode - 会话模式（one_shot / chat）
        - title: str - 会话标题（可选）
        - cwd: str - 会话绑定的工作目录（沙箱根），实现多项目隔离
          空字符串时使用当前 Core 的 CWD

        【返回值】
        - Session: 创建的会话对象

        【执行流程】
        1. 生成会话 ID（格式：sess-<12位随机字符串>）
        2. 创建 Session 对象（状态为 active，绑定 cwd）
        3. 切换沙箱根到会话 cwd（如果指定了 cwd）
        4. 将会话添加到内存字典
        5. 为会话创建锁
        6. 将会话元数据写入文件
        7. 发布 SessionCreatedEvent 事件
        8. 返回会话对象

        【cwd 的作用】
        每个会话绑定独立的项目目录（类似 VS Code 的 workspace），
        Agent 的文件操作被限制在此目录内。
        - 在 D:/project-a 启动 TUI → 会话 A 的 cwd = D:/project-a
        - 在 E:/project-b 启动 TUI → 会话 B 的 cwd = E:/project-b
        """
        # 生成会话 ID（格式：sess-<12位随机字符串>）
        sid = f"sess-{uuid.uuid4().hex[:12]}"
        # 获取当前时间戳
        ts = _now()
        # 创建 Session 对象
        session = Session(
            id=sid,
            mode=mode,
            status="active",
            title=title,
            created_at=ts,
            updated_at=ts,
            run_ids=[],
            cwd=cwd,
        )
        # 如果指定了 cwd，切换沙箱根目录
        if cwd:
            from iwan_claude.core.sandbox import set_sandbox_root
            set_sandbox_root(cwd)
        # 将会话添加到内存字典
        self._sessions[sid] = session
        # 为会话创建锁（确保并发安全）
        self._locks[sid] = asyncio.Lock()
        # 将会话元数据写入文件
        self._store.write_meta(session)
        # 发布会话创建事件
        await self._bus.publish(SessionCreatedEvent(session_id=sid, mode=mode, ts=ts))
        return session

    async def send_message(
        self,
        sid: str,
        content: str,
        *,
        run_id: str | None = None,
        skill_name: str = "",
        skip_auto_skill: bool = False,
    ) -> SendMessageResult:
        """
        处理用户消息，追加到消息历史并启动一次 Agent 运行

        【参数说明】
        - sid: str - 会话 ID
        - content: str - 用户消息内容
        - run_id: str | None - 运行 ID（可选，自动生成）
        - skill_name: str - 手动指定技能（TUI 确认后回传）
        - skip_auto_skill: bool - 跳过自动匹配（用户拒绝后回传）

        【返回值】
        - SendMessageResult: run_id 非空=正常执行；skill_match 非空=待用户确认

        【执行流程】
        1. 获取会话对象（不存在则抛出错误）
        2. 检查会话是否正在处理中（加锁）
        3. 检查会话是否已关闭
        4. 如果会话处于等待输入状态，发布恢复事件
        5. 将用户消息追加到消息历史
        6. 发布消息接收事件
        7. 如果会话没有标题，使用消息前 40 个字符作为标题
        8. 生成运行 ID 并更新会话元数据
        9. 解析 @filename 语法，自动读取文件内容
        10. 解析 Skill（手动触发或自动匹配）
        11. 创建 AgentRunner 并执行运行
        12. 更新会话状态
        13. 发布相应事件
        14. 返回运行 ID

        【并发控制】
        使用 asyncio.Lock 确保同一会话的并发安全
        如果会话正在处理中，抛出 SESSION_BUSY 错误

        【@filename 语法】
        用户消息中可以包含 @filename 引用，系统会自动读取文件内容
        并追加到消息内容中，格式如下：
        ```
        [File References]
        --- filename.py ---
        file content
        --- end filename.py ---
        ```

        【Skill 触发】
        - 手动触发：消息以 "/" 开头，后面跟 Skill 名称和参数
        - 自动触发：系统根据消息内容自动匹配最合适的 Skill
        """
        # 1. 获取会话对象（不存在则抛出错误）
        session = self._get_session(sid)
        # 2. 获取会话锁
        lock = self._locks[sid]
        # 3. 检查会话是否正在处理中
        if lock.locked():
            raise HandlerError(SESSION_BUSY, "session busy")

        # 4. 如果会话绑定了 cwd，切换沙箱根目录
        # 这确保 Agent 的文件操作（read_file, write_file, bash 等）
        # 被限制在会话指定的项目目录内
        if session.cwd:
            from iwan_claude.core.sandbox import set_sandbox_root
            set_sandbox_root(session.cwd)

        # 5. 加锁处理消息
        async with lock:
            # 5. 检查会话是否已关闭
            if session.status == "closed":
                raise HandlerError(SESSION_CLOSED, "session already closed")

            # 6. 如果会话处于等待输入状态，发布恢复事件
            if session.status == "waiting_for_input":
                await self._bus.publish(SessionResumedEvent(session_id=sid, ts=_now()))

            # ==================== 技能自动匹配预检查 ====================
            # 在修改任何状态（追加消息、生成 run_id）之前检查
            # 若命中自动匹配，返回 match 信息给 TUI 确认，不启动 run
            # 用户确认后回传 skill_name，拒绝后回传 skip_auto_skill=True
            if not skill_name and not skip_auto_skill and not content.startswith("/"):
                matched_skill, score = self._skill_loader.match_skill(content)
                if matched_skill is not None:
                    return SendMessageResult(skill_match={
                        "name": matched_skill.name,
                        "score": score,
                        "description": matched_skill.description,
                    })

            # 7. 将用户消息追加到消息历史
            self._store.append_message(sid, "user", content)
            # 8. 发布消息接收事件
            await self._bus.publish(
                SessionMessageReceivedEvent(session_id=sid, content=content, ts=_now())
            )

            # 9. 如果会话没有标题，根据第一条消息自动起名
            # 【设计思路】取第一条用户消息的前 30 个字符，在词边界截断
            # 不调用 LLM（避免额外延迟），保持轻量；后续可通过 /rename 手动修改
            if not session.title:
                # 取消息的第一行（避免多行消息标题过长）
                first_line = content.strip().split("\n")[0].strip()
                if len(first_line) > 30:
                    # 在词边界截断：找 30 字符内最后一个空格
                    cut = first_line[:30].rfind(" ")
                    if cut > 10:  # 空格位置合理才截断
                        session.title = first_line[:cut] + "…"
                    else:
                        session.title = first_line[:30] + "…"
                else:
                    session.title = first_line or "(untitled)"
                # 发布重命名事件，通知 TUI 刷新标签栏
                from iwan_claude.core.bus.events import SessionRenamedEvent
                await self._bus.publish(
                    SessionRenamedEvent(
                        session_id=sid,
                        title=session.title,
                        ts=_now(),
                    )
                )

            # 10. 生成运行 ID（如果没有提供）
            run_id = run_id or new_run_id()
            # 11. 更新会话运行列表和时间戳
            session.run_ids.append(run_id)
            session.updated_at = _now()
            # 12. 写入会话元数据
            self._store.write_meta(session)

            # ==================== @filename 语法处理 ====================
            # 解析 @filename 引用，自动读取文件内容注入上下文
            expanded_content = content
            file_refs = re.findall(r"@([\w./\\-]+)", content)
            if file_refs:
                file_contents: list[str] = []
                for ref in file_refs:
                    file_path = Path(ref)
                    if file_path.exists():
                        try:
                            file_content = file_path.read_text(encoding="utf-8")
                            file_contents.append(f"--- {ref} ---\n{file_content}\n--- end {ref} ---")
                        except Exception:
                            pass
                if file_contents:
                    expanded_content = content + "\n\n[File References]\n" + "\n\n".join(file_contents)
            
            # ==================== Skill 解析 ====================
            # 三种触发方式：
            # 1. skill_name 参数（TUI 确认后回传）→ 使用指定技能
            # 2. "/" 前缀 → 手动触发
            # 3. 无 skill → 正常处理（auto-match 已在预检查阶段完成）
            goal = expanded_content
            system_prompt_override: str | None = None
            tool_whitelist: list[str] | None = None

            # 方式 1：TUI 确认后回传的 skill_name
            if skill_name:
                resolved = self._skill_loader.resolve(skill_name)
                if resolved is not None:
                    goal = self._skill_loader.render_prompt(resolved, content)
                    system_prompt_override = resolved.system_prompt_template
                    tool_whitelist = resolved.allowed_tools or None
                    await self._bus.publish(
                        SkillInvokedEvent(
                            skill_name=skill_name,
                            arguments=content,
                            run_id=run_id,
                            ts=_now(),
                            auto_triggered=True,
                        )
                    )

            # 方式 2：手动触发（消息以 "/" 开头）
            elif expanded_content.startswith("/"):
                parts = expanded_content[1:].split(None, 1)
                manual_name = parts[0]
                arguments = parts[1] if len(parts) > 1 else ""
                skill = self._skill_loader.resolve(manual_name)
                if skill is not None:
                    goal = self._skill_loader.render_prompt(skill, arguments)
                    system_prompt_override = skill.system_prompt_template
                    tool_whitelist = skill.allowed_tools or None
                    await self._bus.publish(
                        SkillInvokedEvent(
                            skill_name=manual_name,
                            arguments=arguments,
                            run_id=run_id,
                            ts=_now(),
                        )
                    )

            # ==================== 执行 Agent 运行 ====================
            # 崩溃恢复：如果会话上次中断，读取快照并生成恢复上下文
            recovery_context = ""
            if session.status == "interrupted":
                recovery_context = self._build_recovery_context(session.id)
                log.info("recovering interrupted session=%s recovery_len=%d", sid, len(recovery_context))

            # 标记会话为运行中（崩溃后此状态保留，重启时用于检测中断）
            session.status = "running"
            self._store.write_meta(session)

            # 创建 AgentRunner 实例
            runner = self._runner_factory()
            # 执行运行并捕获结果
            outcome = await runner.run_and_capture(
                goal,
                run_id=run_id,
                session=session,
                store=self._store,
                system_prompt_override=system_prompt_override,
                tool_whitelist=tool_whitelist,
                recovery_context=recovery_context,
            )

            # 存储对话到跨会话记忆（供后续会话语义检索）
            # 用原始用户消息 content（而非 skill 展开后的 goal）作为查询锚点
            if self._memory_manager is not None and outcome.result:
                try:
                    await self._memory_manager.remember_conversation(
                        user_msg=content,
                        assistant_msg=outcome.result,
                        session_id=sid,
                    )
                    logging.getLogger(__name__).info(
                        "memory: remembered conversation session=%s result_len=%d",
                        sid, len(outcome.result),
                    )
                except Exception:
                    logging.getLogger(__name__).exception(
                        "memory: remember_conversation failed session=%s", sid
                    )

            # ==================== 更新会话状态 ====================
            session.updated_at = _now()
            if session.mode == "one_shot":
                # one_shot 模式：运行结束后关闭会话
                session.status = "closed"
                await self._bus.publish(SessionClosedEvent(session_id=sid, ts=session.updated_at))
            else:
                # chat 模式：运行结束后等待用户输入
                session.status = "waiting_for_input"
                await self._bus.publish(
                    SessionWaitingForInputEvent(
                        session_id=sid,
                        last_run_id=run_id,
                        ts=session.updated_at,
                    )
                )
            # 写入会话元数据
            self._store.write_meta(session)
            # 返回运行 ID
            return SendMessageResult(run_id=run_id)

    # ==================================================================
    # 崩溃恢复支持
    # ==================================================================

    def _build_recovery_context(self, sid: str) -> str:
        """
        读取会话最后一次运行的快照，格式化为恢复上下文

        【参数说明】
        - sid: str - 会话 ID

        【返回值】
        - str: 格式化的恢复上下文文本（注入 system prompt 的 ## Recovery Context 部分）
               如果没有快照则返回空字符串

        【执行流程】
        1. 找到会话的 runs 目录
        2. 按 run_id 倒序查找最新的 snapshot.json
        3. 读取快照内容
        4. 调用 format_recovery_context 格式化为自然语言提示
        """
        runs_dir = self._store.runs_dir(sid)
        if not runs_dir.exists():
            return ""

        # 按 run_id 倒序查找最新的有快照的运行目录
        for run_entry in sorted(runs_dir.iterdir(), reverse=True):
            if not run_entry.is_dir():
                continue
            snapshot_path = run_entry / "snapshot.json"
            snapshot = read_snapshot(snapshot_path)
            if snapshot is not None:
                return format_recovery_context(snapshot)

        # 没有找到快照
        return ""

    async def recover_interrupted_sessions(self) -> int:
        """
        恢复中断的会话 - 将所有 status="running" 的会话标记为 "interrupted"

        【设计目的】
        Core 启动时调用。如果上次崩溃时有会话正在运行，
        其 status 会保留为 "running"，需要标记为 "interrupted"，
        以便 TUI 检测并提示用户恢复。

        【返回值】
        - int: 被标记为 interrupted 的会话数量

        【执行流程】
        1. 从存储层读取所有会话
        2. 查找 status="running" 的会话
        3. 将其 status 改为 "interrupted"
        4. 写入 meta.json 持久化
        5. 加载到内存字典
        """
        count = 0
        stored_sessions = self._store.list_sessions()
        for session in stored_sessions:
            if session.status == "running":
                session.status = "interrupted"
                session.updated_at = _now()
                self._store.write_meta(session)
                # 加载到内存，避免后续重复读取
                self._sessions[session.id] = session
                if session.id not in self._locks:
                    self._locks[session.id] = asyncio.Lock()
                count += 1
                log.info("marked interrupted session=%s title=%s", session.id, session.title)
        if count > 0:
            log.info("recovered %d interrupted session(s)", count)
        return count

    def list_interrupted_sessions(self) -> list[Session]:
        """
        列出所有中断的会话（status="interrupted"）

        【返回值】
        - list[Session]: 中断的会话列表，按更新时间倒序

        【设计目的】
        TUI 连接时调用，检测是否有需要恢复的会话
        """
        all_sessions = self.list_sessions()
        return [s for s in all_sessions if s.status == "interrupted"]

    async def close(self, sid: str) -> None:
        """
        关闭指定会话

        【参数说明】
        - sid: str - 会话 ID

        【执行流程】
        1. 获取会话对象（不存在则抛出错误）
        2. 检查会话是否正在处理中（加锁）
        3. 更新会话状态为 closed
        4. 更新会话时间戳
        5. 写入会话元数据
        6. 发布 SessionClosedEvent 事件

        【注意事项】
        - 如果会话正在处理中，抛出 SESSION_BUSY 错误
        - 关闭后的会话不能再发送消息
        """
        # 1. 获取会话对象（不存在则抛出错误）
        session = self._get_session(sid)
        # 2. 获取会话锁
        lock = self._locks[sid]
        # 3. 检查会话是否正在处理中
        if lock.locked():
            raise HandlerError(SESSION_BUSY, "session busy")
        # 4. 加锁处理
        async with lock:
            # 5. 更新会话状态为 closed
            session.status = "closed"
            # 6. 更新会话时间戳
            session.updated_at = _now()
            # 7. 写入会话元数据
            self._store.write_meta(session)
            # 8. 发布关闭事件
            await self._bus.publish(SessionClosedEvent(session_id=sid, ts=session.updated_at))

    async def compact(self, sid: str, focus: str = "") -> Any:
        """
        手动压缩指定会话的消息历史

        【参数说明】
        - sid: str - 会话 ID
        - focus: str - 压缩焦点（可选）

        【返回值】
        - SessionCompactResult: 压缩结果，包含摘要令牌数和节省的令牌数

        【执行流程】
        1. 获取会话对象（不存在则抛出错误）
        2. 检查会话是否正在处理中（加锁）
        3. 检查 LLM provider 是否可用
        4. 创建 Compactor 实例
        5. 执行消息压缩
        6. 如果压缩失败或没有收益，抛出错误
        7. 将压缩后的消息写入 thread.jsonl
        8. 返回压缩结果

        【压缩机制】
        使用 LLM 将多条消息压缩为一条摘要消息，减少上下文长度
        压缩后保留：
        - 用户摘要消息
        - Assistant 确认消息

        【注意事项】
        - 需要 LLM provider 才能执行压缩
        - 如果压缩没有节省令牌，会抛出错误
        """
        # 1. 获取会话对象（不存在则抛出错误）
        self._get_session(sid)
        # 2. 获取会话锁
        lock = self._locks[sid]
        # 3. 检查会话是否正在处理中
        if lock.locked():
            raise HandlerError(SESSION_BUSY, "session busy")
        # 4. 检查 LLM provider 是否可用
        if self._provider is None:
            raise HandlerError(-32020, "provider not available for compaction")
        # 5. 加锁处理
        async with lock:
            # 导入放在函数内部避免循环导入
            from iwan_claude.core.bus.commands import SessionCompactResult
            from iwan_claude.core.compact.compactor import Compactor
            # 6. 读取消息历史
            messages = self._store.read_messages(sid)
            # 7. 获取会话目录
            session_dir = self._store.session_dir(sid)
            # 8. 创建 Compactor 实例
            compactor = Compactor(self._bus, session_dir, sid)
            # 9. 执行消息压缩
            result = await compactor.compact_messages(messages, self._provider, focus=focus)
            # 10. 检查压缩结果
            if result is None:
                raise HandlerError(-32021, "compaction failed or not beneficial")
            # 11. 将压缩后的消息写入 thread.jsonl
            self._store.write_compacted(sid, [
                {"role": "user", "content": result.summary_text},
                {"role": "assistant", "content": "Understood, I'll continue from this summary."},
            ])
            # 12. 返回压缩结果
            return SessionCompactResult(
                summary_tokens=result.summary_tokens,
                saved_tokens=max(0, result.original_token_estimate - result.summary_tokens),
            )

    async def get_history(self, sid: str) -> list[dict[str, Any]]:
        """
        读取指定会话的完整消息历史

        【参数说明】
        - sid: str - 会话 ID

        【返回值】
        - list[dict[str, Any]]: 消息列表，可直接传给 Anthropic API

        【执行流程】
        1. 获取会话对象（不存在则抛出错误）
        2. 调用 SessionStore.read_messages() 读取消息历史

        【设计目的】
        获取会话的完整消息历史，用于展示或分析
        """
        # 1. 获取会话对象（不存在则抛出错误）
        self._get_session(sid)
        # 2. 读取消息历史
        return self._store.read_messages(sid)

    async def list_checkpoints(self, sid: str) -> list[dict[str, Any]]:
        """
        列出指定会话的所有 checkpoint

        【参数说明】
        - sid: str - 会话 ID

        【返回值】
        - list[dict[str, Any]]: Checkpoint 列表

        【执行流程】
        1. 获取会话对象（不存在则抛出错误）
        2. 创建 AgentRunner 实例
        3. 调用 list_checkpoints() 获取 checkpoint 列表
        4. 关闭 AgentRunner

        【设计目的】
        列出所有可用的 checkpoint，供用户选择恢复
        """
        # 1. 获取会话对象（不存在则抛出错误）
        self._get_session(sid)
        # 2. 创建 AgentRunner 实例
        runner = self._runner_factory()
        try:
            # 3. 获取 checkpoint 列表
            return await runner.list_checkpoints(sid)
        finally:
            # 4. 确保关闭 runner
            await runner.close()

    async def restore_checkpoint(self, sid: str, checkpoint_id: str) -> dict[str, Any] | None:
        """
        恢复指定会话到指定 checkpoint

        【参数说明】
        - sid: str - 会话 ID
        - checkpoint_id: str - Checkpoint ID

        【返回值】
        - dict[str, Any] | None: 恢复结果（包含 checkpoint_id、step、messages 数量），恢复失败返回 None

        【执行流程】
        1. 获取会话对象（不存在则抛出错误）
        2. 检查会话是否正在处理中（加锁）
        3. 创建 AgentRunner 实例
        4. 调用 restore_checkpoint() 获取状态
        5. 如果状态为 None，返回 None
        6. 从状态中提取消息和步骤
        7. 将消息写入 thread.jsonl
        8. 更新会话运行列表（截断到恢复的步骤）
        9. 更新会话时间戳
        10. 写入会话元数据
        11. 返回恢复结果

        【Checkpoint 恢复机制】
        从 checkpoint 恢复会话状态，包括：
        - 消息历史
        - 当前步骤
        - 运行列表

        【注意事项】
        - 如果会话正在处理中，抛出 SESSION_BUSY 错误
        - 恢复后会覆盖当前的消息历史
        """
        # 1. 获取会话对象（不存在则抛出错误）
        session = self._get_session(sid)
        # 2. 获取会话锁
        lock = self._locks[sid]
        # 3. 检查会话是否正在处理中
        if lock.locked():
            raise HandlerError(SESSION_BUSY, "session busy")

        # 4. 加锁处理
        async with lock:
            # 5. 创建 AgentRunner 实例
            runner = self._runner_factory()
            # 6. 恢复 checkpoint 状态
            state = await runner.restore_checkpoint(sid, checkpoint_id)
            # 7. 关闭 runner
            await runner.close()

            # 8. 检查恢复结果
            if state is None:
                return None

            # 9. 从状态中提取消息和步骤
            messages = state.get("messages", [])
            step = state.get("step", 0)

            # 10. 将消息写入 thread.jsonl（覆盖原文件）
            self._store.write_messages(sid, messages)

            # 11. 更新会话运行列表（截断到恢复的步骤）
            session.run_ids = session.run_ids[:step] if session.run_ids else []
            # 12. 更新会话时间戳
            session.updated_at = _now()
            # 13. 写入会话元数据
            self._store.write_meta(session)

            # 14. 返回恢复结果
            return {"checkpoint_id": checkpoint_id, "step": step, "messages": len(messages)}

    def _get_session(self, sid: str) -> Session:
        """
        从内存字典获取会话对象

        【参数说明】
        - sid: str - 会话 ID

        【返回值】
        - Session: 会话对象

        【错误处理】
        - 如果会话不存在，抛出 HandlerError(SESSION_NOT_FOUND)

        【设计目的】
        统一的会话查找方法，确保错误处理一致
        """
        session = self._sessions.get(sid)
        if session is None:
            raise HandlerError(SESSION_NOT_FOUND, "session not found")
        return session

    def list_sessions(self) -> list[Session]:
        """
        列出所有会话，按更新时间倒序排列

        【返回值】
        - list[Session]: 会话列表，按 updated_at 从新到旧排序

        【执行流程】
        1. 从存储层读取所有会话的元数据
        2. 合并内存中的会话状态（确保状态最新）
        3. 按 updated_at 降序排序
        4. 返回排序后的列表

        【设计目的】
        支持 TUI 标签页显示所有会话，
        以及启动时恢复最近的会话。

        【状态同步说明】
        内存中的会话状态可能比磁盘上的更新（如正在运行中），
        所以需要用内存状态覆盖磁盘读取的状态。
        """
        # 从存储层读取所有会话
        stored = self._store.list_sessions()
        # 构建内存会话字典（用于快速查找）
        by_id = {s.id: s for s in stored}
        # 用内存中的会话覆盖（确保状态和 updated_at 最新）
        for sid, session in self._sessions.items():
            by_id[sid] = session
        # 转换为列表并按 updated_at 降序排序
        result = list(by_id.values())
        result.sort(key=lambda s: s.updated_at, reverse=True)
        return result

    async def rename_session(self, sid: str, title: str) -> Session:
        """
        重命名会话标题

        【参数说明】
        - sid: str - 会话 ID
        - title: str - 新的会话标题

        【返回值】
        - Session: 更新后的会话对象

        【执行流程】
        1. 获取会话对象（不存在则抛出错误）
        2. 更新会话标题
        3. 更新会话时间戳
        4. 写入会话元数据到磁盘
        5. 发布重命名事件
        6. 返回更新后的会话

        【设计目的】
        允许用户自定义会话标题，便于在多标签中识别。
        """
        # 1. 获取会话对象（不存在则抛出错误）
        session = self._get_session(sid)
        # 2. 更新会话标题
        session.title = title
        # 3. 更新会话时间戳
        session.updated_at = _now()
        # 4. 写入会话元数据到磁盘
        self._store.write_meta(session)
        # 5. 发布重命名事件
        from iwan_claude.core.bus.events import SessionRenamedEvent
        await self._bus.publish(
            SessionRenamedEvent(
                session_id=sid,
                title=title,
                ts=session.updated_at,
            )
        )
        # 6. 返回更新后的会话
        return session
