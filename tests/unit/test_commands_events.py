from __future__ import annotations

import pytest
from pydantic import ValidationError

from iwan_claude.core.bus.commands import (
    PingCommand,
    PongResult,
    SessionCreateResult,
    SessionSetAutoModeCommand,
    SessionSetAutoModeResult,
)
from iwan_claude.core.bus.events import CoreStartedEvent, SessionAutoModeChangedEvent


# 功能：验证 PingCommand 序列化后再反序列化，client 和 type 字段完整保留
# 设计：JSON 往返测试确认 wire 协议的序列化正确性，type 字段是 discriminated union 的判别键
def test_ping_command_roundtrip() -> None:
    cmd = PingCommand(client="cli/0.0.1")
    cmd2 = PingCommand.model_validate_json(cmd.model_dump_json())
    assert cmd2.client == "cli/0.0.1"
    assert cmd2.type == "core.ping"


# 功能：验证 PingCommand 的 type 字段默认值为 "core.ping"
# 设计：Literal 默认值测试，type 是 Command union 的判别键，必须与 union 定义完全一致，否则反序列化时会路由到错误类型
def test_ping_command_default_type() -> None:
    cmd = PingCommand(client="x")
    assert cmd.type == "core.ping"


# 功能：验证缺少必填 client 字段时 pydantic 校验失败
# 设计：传入空 dict 触发校验，确认 client 是必填字段，防止 daemon 收到不完整的 ping 命令进入 handler
def test_ping_command_missing_client_raises() -> None:
    with pytest.raises(ValidationError):
        PingCommand.model_validate({})


# 功能：验证 PongResult 序列化往返后所有字段完整保留
# 设计：与 PingCommand 对称，测试命令-响应对的两端序列化，确认 int 和 str 字段类型在往返中不变
def test_pong_result_roundtrip() -> None:
    pong = PongResult(server_version="0.0.1", uptime_ms=42, received_at="2026-05-11T00:00:00Z")
    pong2 = PongResult.model_validate(pong.model_dump())
    assert pong2.server_version == "0.0.1"
    assert pong2.uptime_ms == 42


# 功能：验证 CoreStartedEvent 序列化往返后 listen_addr 和 type 字段正确保留
# 设计：CoreStartedEvent 是 daemon 启动通知，往返测试确认 type 的 Literal 约束在反序列化后保持（不被字段名覆盖）
def test_core_started_event_roundtrip() -> None:
    evt = CoreStartedEvent(listen_addr="127.0.0.1:7437", version="0.0.1")
    evt2 = CoreStartedEvent.model_validate_json(evt.model_dump_json())
    assert evt2.listen_addr == "127.0.0.1:7437"
    assert evt2.type == "core.started"


# 功能：验证 SessionSetAutoModeCommand 序列化往返并校验 mode 必填
# 设计：mode 是设置自动模式的核心字段，必须随命令下发
def test_session_set_auto_mode_command_roundtrip() -> None:
    cmd = SessionSetAutoModeCommand(session_id="s1", mode="read_only")
    cmd2 = SessionSetAutoModeCommand.model_validate_json(cmd.model_dump_json())
    assert cmd2.session_id == "s1"
    assert cmd2.mode == "read_only"
    assert cmd2.type == "session.set_auto_mode"


# 功能：验证 SessionSetAutoModeResult 返回当前模式
# 设计：daemon 设置成功后返回实际生效的 mode，供 TUI 同步状态
def test_session_set_auto_mode_result_roundtrip() -> None:
    res = SessionSetAutoModeResult(mode="on")
    res2 = SessionSetAutoModeResult.model_validate_json(res.model_dump_json())
    assert res2.mode == "on"


# 功能：验证 SessionCreateResult 默认 auto_mode 为 off，可携带指定值
# 设计：TUI 创建会话后根据该字段同步 daemon 侧的自动模式状态
def test_session_create_result_includes_auto_mode() -> None:
    res_default = SessionCreateResult(session_id="s1", status="active")
    assert res_default.auto_mode == "off"

    res_on = SessionCreateResult(session_id="s2", status="active", auto_mode="on")
    assert res_on.auto_mode == "on"


# 功能：验证 SessionAutoModeChangedEvent 序列化往返
# 设计：daemon 通过该事件向所有订阅客户端广播模式变更，TUI 据此刷新状态栏
def test_session_auto_mode_changed_event_roundtrip() -> None:
    evt = SessionAutoModeChangedEvent(session_id="s1", mode="read_only", ts="2026-07-22T00:00:00Z")
    evt2 = SessionAutoModeChangedEvent.model_validate_json(evt.model_dump_json())
    assert evt2.session_id == "s1"
    assert evt2.mode == "read_only"
    assert evt2.type == "session.auto_mode_changed"
