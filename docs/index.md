# API Documentation

## __init__
*File: `__init__.py`*

---

## __main__
*File: `__main__.py`*

---

## agents\__init__
*File: `agents\__init__.py`*

---

## agents\loader
*File: `agents\loader.py`*

### Class: `class`

### Class: `AgentProfileLoader`

### Function: `load()`

### Function: `_search_paths()`

### Function: `_parse()`

---

## app
*File: `app.py`*

### Class: `CoreApp`

### Function: `_now()`

### Function: `_ping_handler()`

### Function: `_trace_event_handler()`

### Function: `_agent_run_handler()`

### Function: `_session_create_handler()`

### Function: `_session_send_handler()`

### Function: `_session_history_handler()`

### Function: `_permission_respond_handler()`

### Function: `_session_compact_handler()`

### Function: `_session_checkpoint_list_handler()`

### Function: `_session_checkpoint_restore_handler()`

### Function: `_session_close_handler()`

### Function: `_subscribe_handler()`

### Function: `_replay_events()`

### Function: `run()`

### Function: `_set_shutdown()`

### Function: `_win_sigint_handler()`

### Function: `run()`

---

## bus\__init__
*File: `bus\__init__.py`*

---

## bus\commands
*File: `bus\commands.py`*

### Class: `PingCommand`

### Class: `PongResult`

### Class: `AgentRunCommand`

### Class: `AgentRunResult`

### Class: `EventSubscribeCommand`

### Class: `EventSubscribeResult`

### Class: `SessionCreateCommand`

### Class: `SessionCreateResult`

### Class: `SessionSendMessageCommand`

### Class: `SessionSendMessageResult`

### Class: `SessionGetHistoryCommand`

### Class: `SessionGetHistoryResult`

### Class: `SessionCloseCommand`

### Class: `SessionCloseResult`

### Class: `PermissionRespondCommand`

### Class: `PermissionRespondResult`

### Class: `SessionCompactCommand`

### Class: `SessionCompactResult`

### Class: `SessionCheckpointListCommand`

### Class: `CheckpointInfo`

### Class: `SessionCheckpointListResult`

### Class: `SessionCheckpointRestoreCommand`

### Class: `SessionCheckpointRestoreResult`

---

## bus\envelope
*File: `bus\envelope.py`*

### Class: `JsonRpcRequest`

### Class: `EventPushEnvelope`

### Class: `JsonRpcSuccess`

### Class: `JsonRpcErrorObject`

### Class: `JsonRpcError`

### Class: `HandlerError`

### Function: `make_error()`

---

## bus\events
*File: `bus\events.py`*

### Class: `CoreStartedEvent`

### Class: `RunStartedEvent`

### Class: `RunFinishedEvent`

### Class: `StepStartedEvent`

### Class: `StepFinishedEvent`

### Class: `ToolCallStartedEvent`

### Class: `ToolCallFinishedEvent`

### Class: `ToolCallFailedEvent`

### Class: `LlmTokenEvent`

### Class: `LlmUsageEvent`

### Class: `LlmModelSelectedEvent`

### Class: `LogLineEvent`

### Class: `SessionCreatedEvent`

### Class: `SessionMessageReceivedEvent`

### Class: `SessionWaitingForInputEvent`

### Class: `SessionResumedEvent`

### Class: `SessionClosedEvent`

### Class: `ContextCompactedEvent`

### Class: `PermissionRequestedEvent`

### Class: `PermissionGrantedEvent`

### Class: `PermissionDeniedEvent`

### Class: `SubagentStartedEvent`

### Class: `SubagentFinishedEvent`

### Class: `SkillInvokedEvent`

---

## compact\__init__
*File: `compact\__init__.py`*

---

## compact\budget
*File: `compact\budget.py`*

### Function: `truncate_tool_results()`

---

## compact\compactor
*File: `compact\compactor.py`*

### Class: `from`

### Class: `class`

### Class: `Compactor`

### Function: `_ts_compact()`

### Function: `_now()`

### Function: `compact()`

### Function: `compact_messages()`

### Function: `_write_summary()`

### Function: `_messages_to_text()`

---

## config
*File: `config.py`*

### Class: `class`

### Class: `class`

### Class: `class`

### Class: `class`

### Class: `class`

### Class: `class`

### Class: `class`

### Class: `class`

### Class: `class`

### Class: `class`

### Class: `class`

### Function: `get_config()`

### Function: `_apply_toml()`

### Function: `_apply_env()`

---

## context
*File: `context.py`*

### Class: `class`

### Function: `__post_init__()`

### Function: `system_prompt()`

### Function: `add_assistant_message()`

### Function: `add_tool_result()`

### Function: `is_done()`

### Function: `mark_success()`

### Function: `mark_failed()`

---

## events\__init__
*File: `events\__init__.py`*

---

## events\bus
*File: `events\bus.py`*

### Class: `EventBus`

### Function: `subscribe()`

### Function: `publish()`

---

## events\writer
*File: `events\writer.py`*

### Class: `EventWriter`

### Function: `__aenter__()`

### Function: `__aexit__()`

### Function: `handle()`

### Function: `subscribe()`

---

## langgraph_loop
*File: `langgraph_loop.py`*

### Class: `AgentState`

### Class: `LangGraphAgentLoop`

### Function: `_now()`

### Function: `_build_graph()`

### Function: `run()`

### Function: `_chat_node()`

### Function: `_chat_router()`

### Function: `_tools_node()`

### Function: `_tools_router()`

### Function: `_compact_node()`

### Function: `_compact_router()`

### Function: `_end_node()`

### Function: `_assistant_msg_from_response()`

### Function: `_tool_result_msg()`

### Function: `_extract_last_assistant_text()`

---

## llm\__init__
*File: `llm\__init__.py`*

### Function: `create_provider_from_config()`

---

## llm\base
*File: `llm\base.py`*

### Class: `LLMProvider`

### Function: `chat()`

---

## llm\openai_compat
*File: `llm\openai_compat.py`*

### Class: `OpenAICompatibleProvider`

### Function: `_now()`

### Function: `_convert_messages_to_openai()`

### Function: `_convert_tools_to_openai()`

### Function: `chat()`

### Function: `_stream_once()`

---

## llm\provider
*File: `llm\provider.py`*

### Class: `AnthropicProvider`

### Function: `_context_window()`

### Function: `_now()`

### Function: `_effective_context_window()`

### Function: `chat()`

---

## llm\types
*File: `llm\types.py`*

### Class: `class`

### Class: `class`

### Class: `class`

---

## logging_setup
*File: `logging_setup.py`*

### Function: `setup_logging()`

---

## loop
*File: `loop.py`*

### Class: `AgentLoop`

### Function: `_now()`

### Function: `run()`

---

## mcp\__init__
*File: `mcp\__init__.py`*

---

## mcp\client
*File: `mcp\client.py`*

### Class: `McpServerUnavailableError`

### Class: `McpToolError`

### Class: `class`

### Class: `McpClient`

### Function: `connect_stdio()`

### Function: `connect_tcp()`

### Function: `_initialize()`

### Function: `list_tools()`

### Function: `call_tool()`

### Function: `_drain_stderr()`

### Function: `close()`

### Function: `_call()`

### Function: `_notify()`

### Function: `_write_line()`

### Function: `_read_line()`

---

## mcp\server
*File: `mcp\server.py`*

### Class: `McpServerManager`

### Function: `start_all()`

### Function: `register_tools()`

### Function: `get_tools()`

### Function: `stop_all()`

### Function: `_connect()`

---

## mcp\tool
*File: `mcp\tool.py`*

### Class: `McpTool`

### Function: `invoke()`

---

## memory\__init__
*File: `memory\__init__.py`*

---

## memory\loader
*File: `memory\loader.py`*

### Function: `load_context_file()`

---

## permissions\__init__
*File: `permissions\__init__.py`*

---

## permissions\errors
*File: `permissions\errors.py`*

### Class: `PermissionDeniedError`

---

## permissions\manager
*File: `permissions\manager.py`*

### Class: `from`

### Class: `class`

### Class: `PermissionManager`

### Function: `_now()`

### Function: `evaluate()`

### Function: `check_and_wait()`

### Function: `respond()`

### Function: `_apply_response()`

### Function: `cancel_session()`

---

## permissions\policy
*File: `permissions\policy.py`*

### Class: `PermissionDecision`

### Class: `class`

### Function: `matches_outside_cwd()`

### Function: `param_preview()`

### Function: `_check_sandbox_path()`

### Function: `evaluate()`

---

## permissions\storage
*File: `permissions\storage.py`*

### Function: `load_policy_file()`

### Function: `save_policy_file()`

---

## rag\__init__
*File: `rag\__init__.py`*

---

## rag\chunker
*File: `rag\chunker.py`*

### Class: `from`

### Class: `Chunk`

### Class: `class`

### Function: `chunk_file()`

### Function: `_chunk_python()`

### Function: `get_symbol_name()`

### Function: `visit_node()`

### Function: `_chunk_markdown()`

### Function: `_chunk_plaintext()`

### Function: `_chunk_plaintext_lines()`

### Function: `_chunk_json()`

### Function: `_chunk_json_data()`

### Function: `_chunk_yaml()`

### Function: `_chunk_xml()`

### Function: `_chunk_xml_element()`

### Function: `_chunk_csv()`

---

## rag\embedding
*File: `rag\embedding.py`*

### Class: `EmbeddingProvider`

### Function: `embed()`

### Function: `_embed_batch()`

### Function: `get_embedding_provider()`

---

## rag\index
*File: `rag\index.py`*

### Class: `from`

### Class: `class`

### Class: `class`

### Class: `KnowledgeIndexManager`

### Function: `_load_meta()`

### Function: `_save_meta()`

### Function: `index_directory()`

### Function: `is_excluded()`

### Function: `index_file()`

### Function: `remove_file()`

### Function: `status()`

### Function: `search()`

### Function: `hybrid_search()`

### Function: `_rewrite_query()`

### Function: `_keyword_search()`

### Function: `rebuild_index()`

### Function: `cleanup_index()`

### Function: `backup_index()`

### Function: `save()`

### Function: `load()`

---

## rag\tools
*File: `rag\tools.py`*

### Class: `SearchKnowledgeParams`

### Class: `SearchKnowledgeTool`

### Class: `IndexKnowledgeParams`

### Class: `IndexKnowledgeTool`

### Class: `ForgetKnowledgeParams`

### Class: `ForgetKnowledgeTool`

### Function: `invoke()`

### Function: `invoke()`

### Function: `invoke()`

---

## rag\vectorstore
*File: `rag\vectorstore.py`*

### Class: `VectorStore`

### Class: `MemoryVectorStore`

### Function: `add()`

### Function: `delete()`

### Function: `delete_by_source()`

### Function: `search()`

### Function: `save()`

### Function: `load()`

### Function: `add()`

### Function: `delete()`

### Function: `delete_by_source()`

### Function: `search()`

### Function: `_cosine_similarity()`

### Function: `save()`

### Function: `load()`

### Function: `get_vector_store()`

---

## runner
*File: `runner.py`*

### Class: `from`

### Class: `class`

### Class: `AgentRunner`

### Function: `_now()`

### Function: `_init_checkpointer()`

### Function: `close()`

### Function: `list_checkpoints()`

### Function: `restore_checkpoint()`

### Function: `_build_registry()`

### Function: `_ok()`

### Function: `run()`

### Function: `run_and_capture()`

---

## runs
*File: `runs.py`*

### Function: `run_dir()`

### Function: `events_file()`

### Function: `new_run_id()`

### Function: `ensure_run_dir()`

---

## sandbox
*File: `sandbox.py`*

### Class: `SandboxManager`

### Function: `enabled()`

### Function: `root()`

### Function: `max_file_size()`

### Function: `max_total_size()`

### Function: `ask_on_access_denied()`

### Function: `is_path_allowed()`

### Function: `validate_path()`

### Function: `check_file_size()`

### Function: `get_total_used()`

### Function: `check_total_quota()`

### Function: `ensure_sandbox_exists()`

### Function: `init_sandbox()`

### Function: `_ensure_default_sandbox()`

### Function: `get_sandbox()`

### Function: `is_path_allowed()`

### Function: `validate_path()`

### Function: `check_file_size()`

### Function: `check_total_quota()`

### Function: `get_search_root()`

---

## session\__init__
*File: `session\__init__.py`*

---

## session\manager
*File: `session\manager.py`*

### Class: `SessionManager`

### Function: `_now()`

### Function: `create()`

### Function: `send_message()`

### Function: `close()`

### Function: `compact()`

### Function: `get_history()`

### Function: `list_checkpoints()`

### Function: `restore_checkpoint()`

### Function: `_get_session()`

---

## session\model
*File: `session\model.py`*

### Class: `class`

### Function: `to_dict()`

### Function: `from_dict()`

---

## session\store
*File: `session\store.py`*

### Class: `SessionStore`

### Function: `_now()`

### Function: `session_dir()`

### Function: `runs_dir()`

### Function: `write_meta()`

### Function: `read_meta()`

### Function: `append_message()`

### Function: `append_messages()`

### Function: `read_messages()`

### Function: `_trim_orphan_tool_use()`

### Function: `write_compacted()`

### Function: `write_messages()`

### Function: `read_notes()`

### Function: `append_note()`

---

## skills\__init__
*File: `skills\__init__.py`*

---

## skills\loader
*File: `skills\loader.py`*

### Class: `class`

### Class: `SkillLoader`

### Function: `_parse_skill_file()`

### Function: `resolve()`

### Function: `_search_paths()`

### Function: `list_all()`

### Function: `list_all_skills()`

### Function: `render_prompt()`

### Function: `install_from_url()`

### Function: `_normalize_url()`

### Function: `_download_content()`

### Function: `_install_from_zip()`

### Function: `_install_from_skill_md()`

---

## subagent\__init__
*File: `subagent\__init__.py`*

---

## subagent\registry
*File: `subagent\registry.py`*

### Class: `from`

### Class: `class`

### Class: `BackgroundTaskRegistry`

### Function: `_utcnow()`

### Function: `register()`

### Function: `get()`

### Function: `all()`

### Function: `cancel()`

### Function: `cancel_batch()`

### Function: `cancel_all()`

### Function: `register_batch()`

### Function: `batch_status()`

### Function: `all_batch_ids()`

### Function: `prune()`

### Function: `mark_finished()`

---

## subagent\tool
*File: `subagent\tool.py`*

### Class: `from`

### Class: `SpawnAgentParams`

### Class: `SpawnAgentTool`

### Class: `AgentResultParams`

### Class: `AgentResultTool`

### Class: `SpawnAgentTask`

### Class: `SpawnAgentsParams`

### Class: `SpawnAgentsTool`

### Class: `BatchResultParams`

### Class: `BatchResultTool`

### Class: `CancelAgentParams`

### Class: `CancelAgentTool`

### Function: `_now()`

### Function: `_new_batch_id()`

### Function: `invoke()`

### Function: `_bridge()`

### Function: `_run_background_wrapped()`

### Function: `_run_background()`

### Function: `_build_child_registry()`

### Function: `_allowed()`

### Function: `invoke()`

### Function: `invoke()`

### Function: `_run_one()`

### Function: `_empty_status()`

### Function: `format_batch_status()`

### Function: `invoke()`

### Function: `_is_terminal()`

### Function: `invoke()`

---

## system_prompt
*File: `system_prompt.py`*

### Function: `build_base_system_prompt()`

---

## task\__init__
*File: `task\__init__.py`*

---

## task\manager
*File: `task\manager.py`*

### Class: `TaskManager`

### Function: `_now()`

### Function: `_max_id()`

### Function: `_load()`

### Function: `_save()`

### Function: `create()`

### Function: `get()`

### Function: `update()`

### Function: `list_all()`

### Function: `_clear_dependency()`

### Function: `format_list()`

---

## task\model
*File: `task\model.py`*

### Class: `from`

### Class: `class`

### Function: `to_dict()`

### Function: `from_dict()`

---

## tools\__init__
*File: `tools\__init__.py`*

---

## tools\base
*File: `tools\base.py`*

### Class: `from`

### Class: `class`

### Class: `BaseTool`

### Function: `invoke()`

---

## tools\builtin\__init__
*File: `tools\builtin\__init__.py`*

---

## tools\builtin\bash
*File: `tools\builtin\bash.py`*

### Class: `BashParams`

### Class: `BashTool`

### Function: `invoke()`

### Function: `_quote_powershell()`

---

## tools\builtin\cache
*File: `tools\builtin\cache.py`*

### Class: `CacheManager`

### Class: `CacheGetParams`

### Class: `CacheGetTool`

### Class: `CacheSetParams`

### Class: `CacheSetTool`

### Class: `CacheDeleteParams`

### Class: `CacheDeleteTool`

### Class: `CacheInvalidateTool`

### Class: `CacheStatsTool`

### Function: `get()`

### Function: `set()`

### Function: `delete()`

### Function: `clear()`

### Function: `keys()`

### Function: `stats()`

### Function: `invoke()`

### Function: `invoke()`

### Function: `invoke()`

### Function: `invoke()`

### Function: `invoke()`

---

## tools\builtin\code_quality
*File: `tools\builtin\code_quality.py`*

### Class: `ReviewCodeParams`

### Class: `ReviewCodeTool`

### Class: `LintCodeParams`

### Class: `LintCodeTool`

### Class: `SecurityScanParams`

### Class: `SecurityScanTool`

### Function: `invoke()`

### Function: `_check_security()`

### Function: `_check_performance()`

### Function: `_check_readability()`

### Function: `_check_maintainability()`

### Function: `invoke()`

### Function: `invoke()`

### Function: `_scan_line()`

---

## tools\builtin\collaboration
*File: `tools\builtin\collaboration.py`*

### Class: `from`

### Class: `class`

### Class: `AssignRoleParams`

### Class: `AssignRoleTool`

### Class: `ListRolesTool`

### Class: `ShareKnowledgeParams`

### Class: `ShareKnowledgeTool`

### Function: `invoke()`

### Function: `invoke()`

### Function: `invoke()`

---

## tools\builtin\context
*File: `tools\builtin\context.py`*

### Class: `AddContextParams`

### Class: `AddContextTool`

### Function: `invoke()`

---

## tools\builtin\dependency
*File: `tools\builtin\dependency.py`*

### Class: `PipManageParams`

### Class: `PipManageTool`

### Class: `DependencyCheckParams`

### Class: `DependencyCheckTool`

### Function: `invoke()`

### Function: `invoke()`

---

## tools\builtin\documentation
*File: `tools\builtin\documentation.py`*

### Class: `GenerateDocsParams`

### Class: `GenerateDocsTool`

### Class: `UpdateReadmeParams`

### Class: `UpdateReadmeTool`

### Class: `ChangelogParams`

### Class: `ChangelogTool`

### Function: `invoke()`

### Function: `_generate_docs()`

### Function: `invoke()`

### Function: `invoke()`

---

## tools\builtin\editor
*File: `tools\builtin\editor.py`*

### Class: `from`

### Class: `class`

### Class: `ViewFileParams`

### Class: `ViewFileTool`

### Class: `EditByLinesParams`

### Class: `EditByLinesTool`

### Class: `EditBySearchParams`

### Class: `EditBySearchTool`

### Class: `InsertAtLineParams`

### Class: `InsertAtLineTool`

### Class: `DeleteLinesParams`

### Class: `DeleteLinesTool`

### Function: `_validate_rel_path()`

### Function: `_backup_timestamp()`

### Function: `_backup_destination()`

### Function: `make_backup()`

### Function: `split_preserve_endings()`

### Function: `join_preserve_endings()`

### Function: `_strip_eol()`

### Function: `_has_eol()`

### Function: `_detect_eol()`

### Function: `_normalized_lines()`

### Function: `_read_text_safe()`

### Function: `estimate_affected_paths()`

### Function: `invoke()`

### Function: `estimate_affected_paths()`

### Function: `invoke()`

### Function: `estimate_affected_paths()`

### Function: `invoke()`

### Function: `estimate_affected_paths()`

### Function: `invoke()`

### Function: `estimate_affected_paths()`

### Function: `invoke()`

---

## tools\builtin\fs_ops
*File: `tools\builtin\fs_ops.py`*

### Class: `from`

### Class: `class`

### Class: `DeleteFileParams`

### Class: `DeleteFileTool`

### Class: `RenameFileParams`

### Class: `RenameFileTool`

### Class: `CopyFileParams`

### Class: `CopyFileTool`

### Class: `MkdirParams`

### Class: `MkdirTool`

### Class: `FileStatParams`

### Class: `FileStatTool`

### Class: `FileExistsParams`

### Class: `FileExistsTool`

### Function: `_validate_rel_path()`

### Function: `_now_iso()`

### Function: `estimate_affected_paths()`

### Function: `estimate_affected_paths()`

### Function: `invoke()`

### Function: `estimate_affected_paths()`

### Function: `invoke()`

### Function: `estimate_affected_paths()`

### Function: `invoke()`

### Function: `estimate_affected_paths()`

### Function: `invoke()`

### Function: `estimate_affected_paths()`

### Function: `invoke()`

### Function: `invoke()`

### Function: `_human_size()`

---

## tools\builtin\git
*File: `tools\builtin\git.py`*

### Class: `GitStatusParams`

### Class: `GitStatusTool`

### Class: `GitLogParams`

### Class: `GitLogTool`

### Class: `GitDiffParams`

### Class: `GitDiffTool`

### Class: `GitCommitParams`

### Class: `GitCommitTool`

### Class: `GitCheckoutParams`

### Class: `GitCheckoutTool`

### Function: `invoke()`

### Function: `invoke()`

### Function: `invoke()`

### Function: `invoke()`

### Function: `invoke()`

---

## tools\builtin\http
*File: `tools\builtin\http.py`*

### Class: `HttpRequestParams`

### Class: `HttpRequestTool`

### Function: `invoke()`

---

## tools\builtin\list_dir
*File: `tools\builtin\list_dir.py`*

### Class: `ListDirParams`

### Class: `ListDirTool`

### Function: `invoke()`

### Function: `_walk()`

---

## tools\builtin\note_save
*File: `tools\builtin\note_save.py`*

### Class: `NoteSaveParams`

### Class: `NoteSaveTool`

### Function: `invoke()`

---

## tools\builtin\performance
*File: `tools\builtin\performance.py`*

### Class: `ProfileCodeParams`

### Class: `ProfileCodeTool`

### Function: `invoke()`

---

## tools\builtin\read_file
*File: `tools\builtin\read_file.py`*

### Class: `ReadFileParams`

### Class: `ReadFileTool`

### Function: `invoke()`

---

## tools\builtin\run_python
*File: `tools\builtin\run_python.py`*

### Class: `RunPythonParams`

### Class: `RunPythonTool`

### Class: `_InstallResult`

### Function: `invoke()`

### Function: `_resolve_python()`

### Function: `_child_env()`

### Function: `_pip_install()`

---

## tools\builtin\search
*File: `tools\builtin\search.py`*

### Class: `from`

### Class: `FindFilesParams`

### Class: `FindFilesTool`

### Class: `class`

### Class: `GrepSearchParams`

### Class: `GrepSearchTool`

### Class: `_Matcher`

### Class: `_ReMatcher`

### Class: `_FixedMatcher`

### Function: `_validate_rel_root()`

### Function: `_simplify_pattern_basename()`

### Function: `_matches_any_glob()`

### Function: `_matches_any_path_glob()`

### Function: `_should_ignore()`

### Function: `invoke()`

### Function: `relpath()`

### Function: `invoke()`

### Function: `rel()`

### Function: `search()`

### Function: `search()`

### Function: `search()`

### Function: `_search_content()`

### Function: `_any_glob_include()`

### Function: `_human()`

---

## tools\builtin\skill
*File: `tools\builtin\skill.py`*

### Class: `SkillListParams`

### Class: `SkillListTool`

### Class: `SkillInfoParams`

### Class: `SkillInfoTool`

### Class: `SkillCreateParams`

### Class: `SkillCreateTool`

### Class: `SkillDeleteParams`

### Class: `SkillDeleteTool`

### Class: `SkillInstallParams`

### Class: `SkillInstallTool`

### Function: `invoke()`

### Function: `invoke()`

### Function: `invoke()`

### Function: `invoke()`

### Function: `invoke()`

---

## tools\builtin\system
*File: `tools\builtin\system.py`*

### Class: `ProcessListParams`

### Class: `ProcessListTool`

### Function: `invoke()`

### Function: `_quote_powershell()`

---

## tools\builtin\task_create
*File: `tools\builtin\task_create.py`*

### Class: `TaskCreateTool`

### Function: `invoke()`

---

## tools\builtin\task_get
*File: `tools\builtin\task_get.py`*

### Class: `TaskGetTool`

### Function: `invoke()`

---

## tools\builtin\task_list
*File: `tools\builtin\task_list.py`*

### Class: `TaskListTool`

### Function: `invoke()`

---

## tools\builtin\task_update
*File: `tools\builtin\task_update.py`*

### Class: `TaskUpdateTool`

### Function: `invoke()`

---

## tools\builtin\testing
*File: `tools\builtin\testing.py`*

### Class: `GenerateTestsParams`

### Class: `GenerateTestsTool`

### Class: `instantiation`

### Class: `RunTestsParams`

### Class: `RunTestsTool`

### Class: `TestCoverageParams`

### Class: `TestCoverageTool`

### Function: `invoke()`

### Function: `_extract_functions_and_classes()`

### Function: `_generate_test_code()`

### Function: `invoke()`

### Function: `invoke()`

---

## tools\builtin\write_file
*File: `tools\builtin\write_file.py`*

### Class: `from`

### Class: `class`

### Class: `WriteFileParams`

### Class: `WriteFileTool`

### Function: `_validate_rel_path()`

### Function: `estimate_affected_paths()`

### Function: `estimate_affected_paths()`

### Function: `invoke()`

### Function: `_backup_destination()`

---

## tools\errors
*File: `tools\errors.py`*

### Class: `RateLimitedError`

---

## tools\invocation
*File: `tools\invocation.py`*

### Class: `is`

### Class: `in`

### Function: `_now()`

### Function: `_fail()`

### Function: `invoke_tool()`

### Function: `elapsed()`

### Function: `_emit_permission()`

---

## tools\registry
*File: `tools\registry.py`*

### Class: `ToolRegistry`

### Function: `register()`

### Function: `get()`

### Function: `tool_schemas()`

### Function: `to_langchain_tools()`

### Function: `_bridge()`

---

## trace\__init__
*File: `trace\__init__.py`*

---

## trace\provider
*File: `trace\provider.py`*

### Class: `TracingProvider`

### Function: `_now()`

### Function: `chat()`

---

## trace\record
*File: `trace\record.py`*

### Class: `TraceRecord`

---

## trace\writer
*File: `trace\writer.py`*

### Class: `TraceWriter`

### Function: `start()`

### Function: `stop()`

### Function: `emit()`

### Function: `_drain()`

---

## transport\__init__
*File: `transport\__init__.py`*

---

## transport\ipc_broadcaster
*File: `transport\ipc_broadcaster.py`*

### Class: `from`

### Class: `class`

### Class: `IpcEventBroadcaster`

### Function: `_now()`

### Function: `subscribe()`

### Function: `unsubscribe()`

### Function: `handle()`

### Function: `_matches_topic()`

### Function: `_matches_scope()`

---

## transport\socket_client
*File: `transport\socket_client.py`*

### Class: `IpcError`

### Class: `SocketClient`

### Function: `connect()`

### Function: `close()`

### Function: `on_event()`

### Function: `send_command()`

### Function: `run_event_loop()`

### Function: `_dispatch()`

---

## transport\socket_server
*File: `transport\socket_server.py`*

### Class: `SocketServer`

### Function: `_now()`

### Function: `get_connection_writer()`

### Function: `register()`

### Function: `start()`

### Function: `stop()`

### Function: `_handle_connection()`

### Function: `_read_loop()`

### Function: `_handle_line()`

### Function: `_send()`

---
