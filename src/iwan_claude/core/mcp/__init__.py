"""
MCP（Model Context Protocol）模块 - 实现与外部工具服务器的通信

【学习要点】
1. MCP 协议：Anthropic 定义的工具服务器协议，基于 JSON-RPC 2.0
2. 双传输模式：支持 stdio（子进程）和 TCP（网络）两种通信方式
3. 适配器模式：将 MCP 工具适配为 BaseTool，实现透明集成
4. 生命周期管理：管理所有 MCP Server 的启动、发现、注册和关闭

【核心组件】
- McpClient: MCP 客户端，负责与单个 MCP Server 通信
- McpServerManager: MCP Server 管理器，管理所有 Server 的生命周期
- McpTool: MCP 工具包装器，将 MCP 工具适配为 BaseTool
- McpToolDef: 工具定义数据类
- McpServerUnavailableError: 服务器不可用异常

【模块结构】
- client.py: MCP 客户端实现
- server.py: MCP Server 管理器
- tool.py: MCP 工具包装器
- __init__.py: 统一导出接口

【使用流程】
1. 配置 MCP Server（config.yaml）
2. McpServerManager.start_all() 启动所有 Server
3. McpServerManager.register_tools() 注册工具到 ToolRegistry
4. Agent 透明调用 MCP 工具
5. McpServerManager.stop_all() 关闭所有 Server

【设计目的】
将工具实现从 core daemon 中分离出去，实现插件化扩展。
通过 MCP 协议，agent 可以调用外部工具服务器提供的工具，
无需修改 core daemon 的代码。
"""
from iwan_claude.core.mcp.client import McpClient, McpServerUnavailableError, McpToolDef
from iwan_claude.core.mcp.server import McpServerManager
from iwan_claude.core.mcp.tool import McpTool

# 统一导出接口
__all__ = ["McpClient", "McpServerManager", "McpServerUnavailableError", "McpTool", "McpToolDef"]
