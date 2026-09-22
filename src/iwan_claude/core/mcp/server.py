"""
MCP Server 管理模块 - 管理所有 MCP Server 的生命周期

【学习要点】
1. 生命周期管理：启动、工具发现、注册、关闭
2. 插件化扩展：将工具实现从 core daemon 中分离出去
3. 容错设计：单个 MCP Server 启动失败不影响其他 Server
4. 透明集成：将 MCP 工具注册到 ToolRegistry，使 agent 可以透明调用

【核心类】
- McpServerManager: MCP Server 管理器

【典型场景】
- 通过 stdio 连接本地脚本（如 Python 脚本）
- 通过 TCP 连接远程工具服务器（如数据库查询服务、API 代理）

【配置来源】
MCP Server 配置来自 config.yaml 的 mcp.servers 字段

【启动流程】
1. 读取配置列表
2. 并行连接所有 MCP Server（单个失败只影响自己）
3. 发现工具（调用 tools/list）
4. 将工具包装为 McpTool
5. 注册到 ToolRegistry

【关闭流程】
1. 关闭所有 MCP Client 连接
2. 终止 stdio 子进程
3. 清理缓存
"""
from __future__ import annotations

import asyncio
import logging

from iwan_claude.core.config import McpServerConfig
from iwan_claude.core.mcp.client import McpClient
from iwan_claude.core.mcp.tool import McpTool
from iwan_claude.core.tools.registry import ToolRegistry

log = logging.getLogger(__name__)


class McpServerManager:
    """
    MCP Server 管理器 - 管理所有 MCP Server 的生命周期

    【学习要点】
    1. 生命周期管理：启动、工具发现、注册、关闭
    2. 插件化扩展：将工具实现从 core daemon 中分离出去
    3. 容错设计：单个 MCP Server 启动失败不影响其他 Server
    4. 透明集成：将 MCP 工具注册到 ToolRegistry

    【核心属性】
    - _clients: dict[str, McpClient] - 存储所有 MCP Client（key=server_name）
    - _tools: list[McpTool] - 存储所有已发现的 MCP 工具

    【核心方法】
    - start_all(): 启动所有 MCP Server
    - register_tools(): 将工具注册到 ToolRegistry
    - get_tools(): 获取工具列表
    - stop_all(): 关闭所有 MCP Server

    【设计目的】
    将工具实现从 core daemon 中分离出去，实现插件化扩展。
    Agent 可以通过 MCP 协议调用外部工具服务器提供的工具。

    【容错设计】
    - 单个 MCP Server 启动失败不影响其他 Server
    - 记录错误日志但不抛出异常
    """
    def __init__(self) -> None:
        """
        初始化 MCP Server 管理器

        【字段说明】
        - _clients: dict[str, McpClient] - 存储所有 MCP Client（key=server_name）
        - _tools: list[McpTool] - 存储所有已发现的 MCP 工具

        【设计要点】
        - 使用字典存储 Client，便于按名称查找和管理
        - 使用列表存储工具，便于批量注册和获取
        """
        # 存储所有 MCP Client（key=server_name）
        self._clients: dict[str, McpClient] = {}
        # 存储所有已发现的 MCP 工具
        self._tools: list[McpTool] = []

    async def start_all(self, servers: list[McpServerConfig]) -> None:
        """
        启动所有 MCP Server

        【参数说明】
        - servers: list[McpServerConfig] - MCP Server 配置列表（来自 config.yaml）

        【执行流程】
        1. 对配置列表并行发起启动（_start_one）
        2. 每个 Server：建连 → tools/list → 包装 McpTool → 缓存
        3. 失败者就地回收已建立的连接后跳过

        【容错设计】
        - 单个 MCP Server 启动失败不影响其他 Server
        - 使用 try-except 捕获异常，记录日志并跳过

        【注意事项】
        - 配置列表来自 config.yaml 的 mcp.servers 字段
        - 每个 Server 的配置包含 transport、name、command/args（stdio）或 host/port（TCP）

        【示例】
        ```yaml
        mcp:
          servers:
            - name: database
              transport: stdio
              command: python
              args: ["-m", "my_mcp_server"]
            - name: api
              transport: tcp
              host: localhost
              port: 8080
        ```
        """
        # 并行启动所有 Server：单个死配置最坏拖满自己的超时，
        # 串行会让 daemon 启动时间 = Σ超时，一个坏 server 卡全体
        results = await asyncio.gather(
            *(self._start_one(cfg) for cfg in servers),
            return_exceptions=True,
        )
        # gather 已兜住异常，这里只处理 _start_one 自身意外炸掉的情况（留痕不抛出）
        for cfg, res in zip(servers, results, strict=True):
            if isinstance(res, BaseException):  # pragma: no cover - 防御分支
                log.exception(
                    "mcp: server '%s' startup raised unexpectedly",
                    cfg.name, exc_info=res,
                )

    # 启动单个 Server：任何一步失败都要回收已建立的连接（子进程/TCP socket 泄漏点）
    async def _start_one(self, cfg: McpServerConfig) -> None:
        client: McpClient | None = None
        try:
            # 建立连接（stdio 或 TCP）
            client = await self._connect(cfg)
            # 发现工具（调用 tools/list）
            tool_defs = await client.list_tools()
        except Exception:
            # 单个 MCP Server 启动失败不影响其他 Server
            log.exception("mcp: server '%s' failed to start, skipping", cfg.name)
            if client is not None:
                # connect 成功但 list_tools 失败时 client 尚未进 _clients，
                # 不在这里 close 的话 stdio 子进程永远无人回收
                try:
                    await client.close()
                except Exception:
                    log.warning("mcp: error closing half-started server '%s'", cfg.name)
            return
        if cfg.name in self._clients:
            # 重名防御（配置层已拦截，走到这里说明有旁路）：保留先连的，丢弃后者
            log.error("mcp: duplicate server name '%s', dropping the later one", cfg.name)
            try:
                await client.close()
            except Exception:
                pass
            return
        # 将每个工具包装为 McpTool（使 ToolRegistry 可透明调用）
        for tool_def in tool_defs:
            self._tools.append(McpTool(client, cfg.name, tool_def))
        # 缓存 Client（用于后续工具调用）
        self._clients[cfg.name] = client
        # 记录日志
        log.info(
            "mcp: server '%s' connected, %d tool(s) discovered",
            cfg.name, len(tool_defs),
        )

    def register_tools(self, registry: ToolRegistry) -> None:
        """
        将所有已发现的 MCP 工具注册到 ToolRegistry

        【参数说明】
        - registry: ToolRegistry - 工具注册表

        【执行流程】
        1. 遍历所有已发现的 MCP 工具
        2. 将每个工具注册到 ToolRegistry

        【设计目的】
        使 Agent 可以像调用内置工具一样调用 MCP 工具，实现透明集成。

        【注意事项】
        - 必须在 start_all() 之后调用
        - 每个工具的名称格式为 mcp__{server_name}__{tool_name}
        """
        # 遍历所有已发现的 MCP 工具
        for tool in self._tools:
            # 注册到 ToolRegistry
            registry.register(tool)

    def get_tools(self) -> list[McpTool]:
        """
        获取已发现的 MCP 工具列表

        【返回值】
        - list[McpTool]: MCP 工具列表

        【设计目的】
        用于 Runner 每次 run 时注入新的 ToolRegistry。

        【注意事项】
        - 返回列表的副本，防止外部修改
        - 必须在 start_all() 之后调用
        """
        # 返回工具列表的副本
        return list(self._tools)

    async def stop_all(self) -> None:
        """
        关闭所有 MCP Server 连接

        【执行流程】
        1. 遍历所有已连接的 Client
        2. 关闭每个 Client 的连接
        3. 记录日志
        4. 清空缓存

        【资源清理】
        - 关闭 TCP 连接
        - 终止 stdio 子进程
        - 释放文件句柄

        【容错设计】
        - 单个 Client 关闭失败不影响其他 Client
        - 使用 try-except 捕获异常，记录警告日志

        【注意事项】
        - 必须在程序退出前调用
        - 使用 list(self._clients.items()) 防止迭代过程中字典被修改
        """
        # 遍历所有已连接的 Client
        for name, client in list(self._clients.items()):
            try:
                # 关闭 Client 连接
                await client.close()
                # 记录日志
                log.info("mcp: server '%s' closed", name)
            except Exception:
                # 单个 Client 关闭失败不影响其他 Client（带堆栈，便于排查僵尸子进程）
                log.warning("mcp: error closing server '%s'", name, exc_info=True)
        # 清空缓存：_tools 必须一并清掉——只清 client 的话，
        # stop 之后 get_tools() 仍会吐出一批指向已关闭连接的死工具
        self._clients.clear()
        self._tools.clear()

    async def _connect(self, cfg: McpServerConfig) -> McpClient:
        """
        根据配置建立 MCP Server 连接

        【参数说明】
        - cfg: McpServerConfig - MCP Server 配置

        【返回值】
        - McpClient: 已连接的 MCP 客户端

        【支持的传输类型】
        - stdio: 通过子进程的标准输入输出通信
        - tcp: 通过 TCP 网络连接通信

        【执行流程】
        1. 创建 McpClient
        2. 根据 transport 类型选择连接方式
        3. 验证必要的配置参数
        4. 建立连接并完成握手

        【配置要求】
        - stdio: 需要 command 和 args 参数
        - tcp: 需要 host 和 port 参数

        【异常处理】
        - ValueError: 配置不完整或传输类型未知
        """
        # 创建 MCP 客户端（读超时来自该 server 的配置项 timeout_sec）
        client = McpClient(read_timeout_sec=cfg.timeout_sec)
        
        # 根据传输类型选择连接方式
        if cfg.transport == "stdio":
            # stdio 模式：启动子进程，通过管道通信
            # 验证 command 参数是否存在
            if not cfg.command:
                raise ValueError(f"mcp server '{cfg.name}': stdio transport requires 'command'")
            # 建立 stdio 连接
            await client.connect_stdio(cfg.command, cfg.args, cfg.env or None)
        elif cfg.transport == "tcp":
            # TCP 模式：连接远程服务器
            # 建立 TCP 连接
            await client.connect_tcp(cfg.host, cfg.port)
        else:
            # 未知传输类型
            raise ValueError(f"mcp server '{cfg.name}': unknown transport '{cfg.transport}'")
        
        # 返回已连接的客户端
        return client