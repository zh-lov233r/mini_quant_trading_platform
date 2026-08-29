# Quant Agent 全流程联调交付报告

## 1. 执行元数据

| 字段 | 值 |
| --- | --- |
| 执行日期 | 2026-08-19 至 2026-08-20 |
| 操作者 | Codex |
| 状态 | PASS |
| Quant 分支 | `codex/quant-agent-research-integration` |
| Quant 起始提交 | `4c7b6e29e90bed6f2f8fa1aa420dea6fd1e558b7` |
| Quant 最终实现提交 | `17bd739`（本文档由独立 `docs(e2e)` 提交交付） |
| Coding Agent 分支 | `codex/quant-agent-research-integration` |
| Coding Agent 起始提交 | `c0e6e10eab2be22be881d2c1e309f4b24feba865` |
| Coding Agent 最终提交 | `76d3ccf` |

最终结论为 `PASS`；真实 Draft PR 保持 open、draft、未合并和未部署。

## 2. 安全边界与脱敏环境

| 项目 | 脱敏结果 |
| --- | --- |
| Quant PostgreSQL | `postgresql://[REDACTED]@localhost:5432/hzy`，schema `public` |
| AgentOps PostgreSQL | `postgresql://[REDACTED]@localhost:15432/agentops`，schema `public` |
| Quant Backend / Frontend | `localhost:8000` / `localhost:3000` |
| AgentOps API / Web Console | `localhost:8100` / `localhost:3100`（Console 可选） |
| Service token | `[REDACTED]`；不得进入 workflow input、Artifact、日志或响应 |
| Paper scheduler | 必须为 `false` |
| Paper order submission | 必须为 `false` |
| Broker/order 工具 | 必须为 0 个注册项、0 次调用 |

执行前安全发现：Quant 数据库配置源码包含预存凭证字面量。本报告未记录其值；提交 `e574286` 已将源码默认值改为无凭证的本地连接，并要求部署凭证只能来自环境变量。

## 3. UTC 执行日志

| UTC 时间 | 步骤 | 命令/操作（脱敏） | 预期 | 实际 | 状态 | 耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-08-19T21:22:52Z | 仓库审计 | `git status --short --branch`、`git log -1` | 两仓库位于集成分支且干净 | 两仓库均位于目标分支，起始提交见元数据 | PASS | <1s |
| 2026-08-19T21:22:52Z | 工具链审计 | 检查 Docker、PostgreSQL 客户端、Codex、GitHub CLI、Node.js | 联调依赖可用 | Docker CLI、Codex、GitHub CLI、Node.js 可用；`psql`/`pg_dump` 主机客户端不可用 | PARTIAL | <2s |
| 2026-08-19T21:22:52Z | Docker 审计 | `docker info` | Docker daemon 可用 | Docker daemon 未运行，`localhost:15432` 无监听 | FAIL（保留，待恢复） | <2s |
| 2026-08-19T21:22:52Z | 数据库目标审计 | 通过应用配置解析 URL，仅输出 host/port/database | 明确两个本地数据库目标且不泄露凭证 | Quant=`localhost:5432/hzy`；AgentOps=`localhost:15432/agentops` | PASS | <1s |
| 2026-08-19T21:22:52Z | 认证审计 | 检查凭证是否存在，不输出内容 | Codex/GitHub 可认证，LLM/服务令牌状态明确 | Codex 已登录；GitHub 已登录；AgentOps LLM/service token 尚未配置 | PARTIAL | <1s |
| 2026-08-19T21:24:58Z | Docker 恢复 | 启动 Docker Desktop 后重新执行 `docker info` | Docker daemon 可用 | Docker Server `29.4.2` 已启动 | PASS | ~5s |
| 2026-08-19T21:24:58Z | Quant schema 备份 | 脱敏环境调用 `pg_dump --schema-only` | 升级前备份成功 | `/tmp/codex-quant-agent-e2e/quant-schema-20260819T212458Z.sql`，32,004 bytes，SHA-256 `23c1c9df7755235e2bb0b086d8b368414a484953a57681beab9cc1de1fd0a123` | PASS | <1s |
| 2026-08-19T21:24:58Z | AgentOps 数据库启动 | `make db-up` | `localhost:15432/agentops` ready | `agentops-postgres` running | PASS | <2s |
| 2026-08-19T21:24:58Z | AgentOps migration | `make db-migrate` | 升级到 `0007` | `0006 -> 0007` 成功 | PASS | <2s |
| 2026-08-19T21:25:41Z | Quant additive schema | 脱敏调用 `psql -v ON_ERROR_STOP=1 -f backend/utils/create_zzzzz_research_experiments.sql` | 扩列并创建研究表/索引 | SQL 全部成功；`idempotency_key` 已为 128；两张研究表、约束和索引存在 | PASS | <1s |
| 2026-08-19T21:25:41Z | AgentOps schema 核验 | 只读查询 Alembic 版本、列和约束 | revision=`0007` 且结构完整 | revision=`0007`；18 列、主键、外键和两个唯一约束存在 | PASS | <1s |
| 2026-08-19T21:25:41Z | AgentOps 全量测试 | `.venv/bin/python -m pytest -q apps/control-plane/tests` | 包含 PostgreSQL 测试的全套测试通过 | `78 passed in 7.14s` | PASS | 7.14s |
| 2026-08-19T21:30:00Z | Structured provider 修复 | 新增 `codex_cli` provider；只读 sandbox、临时 schema/output、最多两次修复 | 无 API key 时仍可使用已认证 Codex，且不授予写权限 | 9 项相关单测通过；compileall 和 diff check 通过 | PASS | <2s |
| 2026-08-19T21:30:20Z | 真实 structured smoke | 通过 `quant.strategy_planner` 调用已认证 Codex CLI | 返回符合 output schema 的 proposal | 返回 `mean_reversion`、AAPL/MSFT、2 个 override；无格式修复 | PASS | ~5s |
| 2026-08-19T21:32:00Z | 首次联合启动 | 从 Quant cwd 启动两个后端并轮询健康状态 | `:8000`、`:8100` ready | AgentOps 相对 venv/app 路径解析错误；Quant 已安全启动且 scheduler disabled，但探针误用 `/health`；会话退出后 Quant 已停止 | FAIL（保留，待恢复） | ~30s |
| 2026-08-19T21:34:26Z | 安全拓扑恢复 | 使用绝对路径启动两个后端；显式设置两个 paper 开关为 `false` 和 `FRONTEND_ORIGIN` | `:8000`、`:8100` ready，scheduler disabled | 两个健康检查均为 200；Quant 日志确认 scheduler disabled | PASS | ~1s |
| 2026-08-19T21:34:45Z | 首次前端加载 | 浏览器打开 `/research` | 策略目录、实验列表和 AgentOps 工作流可访问 | `127.0.0.1` 与默认 CORS origin 不一致，Quant OPTIONS=400、页面 `Failed to fetch` | FAIL（保留，已恢复） | <2s |
| 2026-08-19T21:35:10Z | CORS 运行配置恢复 | Quant 显式使用 `FRONTEND_ORIGIN=http://127.0.0.1:3000` 后重启 | 前端读取两个 API | 策略目录和空实验列表成功加载 | PASS | ~2s |
| 2026-08-19T21:35:30Z | 首次 WorkflowRun 创建 | 从 Quant 前端提交参数策略目标 | 创建运行并进入 planner | AgentOps 拒绝 bootstrap 项目的扩张 scope：`repos/quant/**` 超过仓库绑定 | FAIL（保留，已修复） | <1s |
| 2026-08-19T21:36:20Z | Bootstrap scope 修复 | 将项目默认 scope 收窄到六个允许路径，并使 bootstrap 修复既有项目 | 既有项目可安全重跑 bootstrap | 5 项相关测试通过；三套工作流重新确认 ready | PASS | <1s |
| 2026-08-19T21:36:45Z | LLM runtime 第一次重试 | WorkflowRun `3103ad71-0e78-4d13-a67f-c48baf942021` | 使用服务级 `codex_cli` | 项目 runtime snapshot 仍为 `openai`，因无 provider credential 明确失败 | FAIL（保留，已修复） | ~6s |
| 2026-08-19T21:37:20Z | 项目 runtime 修复 | 扩展 runtime provider 类型并将项目 planner 设置为 `codex_cli` | 无 API key，使用已认证 Codex | 配置写入成功，credential 仍为未配置且不再是必需项 | PASS | <1s |
| 2026-08-19T21:37:40Z | LLM runtime 第二次重试 | WorkflowRun `b5c1c266-e1b1-4cda-a17c-9c0f7131d5e8` | Codex CLI 执行 | 服务进程 PATH 无 Codex 可执行文件，明确失败 | FAIL（保留，已修复） | ~3s |
| 2026-08-19T21:38:20Z | Codex 路径修复 | `CODEX_BIN` 使用已审计的绝对可执行路径重启 AgentOps | planner 可执行 | AgentOps 健康，后续真实 planner 成功 | PASS | ~1s |
| 2026-08-19T21:38:45Z | 无效 proposal 证据 | WorkflowRun `3c38b5e3-63a3-4800-a557-c23a15cc8069` | 校验阻止非目录参数 | planner 生成不存在的 `signal.entry_zscore`，Quant 返回 422，未进入审批/创建 | PASS（负向） | ~20s |
| 2026-08-19T21:39:38Z | 有效 proposal 与审批 | WorkflowRun `8090cebf-bae6-425c-b0f1-0b2ec7572651` | 校验通过，批准后创建 draft | proposal/validate 通过；批准后运行被错误标记 completed，但 approval/create 仍未完成 | FAIL（保留，已修复） | ~22s |
| 2026-08-19T21:41:20Z | 审批恢复算法修复 | 恢复时穿越已完成节点，直到到达待执行 frontier | 多节点链在审批后继续 | 19 项相关测试通过，新增 approval/delivery 节点状态断言 | PASS | ~3s |
| 2026-08-19T21:42:18Z | 参数策略完整闭环 | 前端创建并批准 WorkflowRun `e2334154-0cd0-4362-a110-d52735e770cd` | draft strategy 创建且所有节点完成 | validate、approval、create 全部 completed；Strategy `ae2f6e36-c5d1-4e1f-bd1f-111bbb14cfe6` 为 draft | PASS | ~19s |
| 2026-08-19T21:43:30Z | 参数策略幂等/冲突 | 重放相同请求与幂等键，再以同键修改名称 | 同资源；冲突为 409 | 重放 HTTP 201 但返回同一 Strategy ID；变更 payload 返回 409；数据库仅 1 行 | PASS | <1s |
| 2026-08-19T21:45:32Z | 最小研究实验创建 | 从 Quant Frontend 审批 2 参数 × 2 窗口 × 2 成本场景 | 创建 8 个稳定排序 trial | WorkflowRun `89faedf5-fe6f-4d88-be91-336708f17ec8` 创建 Experiment `000c786a-346b-4822-aa5c-89f84c10bf3a` 和 8 个唯一 trial | PASS | ~25s |
| 2026-08-19T21:48:51Z | 研究确定性报告 | 聚合 8 个 trial | 生成样本内外、成本衰减、参数稳定性和 lineage | 8/8 completed；报告含 8 条 Trial→Backtest lineage、数据指纹和 research-only disclaimer | PASS | ~3m |
| 2026-08-19T21:49:20Z | AgentOps 重启恢复 | 在 `waiting_external` 时重启 Control Plane | 复用 ToolRun、幂等键和 Experiment | await ToolRun `b45e6a26-87e5-408b-ad02-e1a5014cff73` attempt 5→14，Experiment/8 个 Backtest 均未重复 | PASS | ~20s |
| 2026-08-19T21:50:30Z | 并发 finalize 缺陷 | 两个 worker 同时完成最后 trial | Experiment 自动进入 completed | 8 个 trial 已完成但 Experiment 暂留 running | FAIL（保留，已修复） | <1s |
| 2026-08-19T21:51:10Z | Experiment finalize 修复 | trial 终态先提交，再重载并 finalize；启动恢复扫描可终结实验 | 并发及重启后状态一致 | focused tests 9 项通过；重启后首个 Experiment 自动变为 completed | PASS | ~3s |
| 2026-08-19T21:53:00Z | Quant 短时不可用演练 | AgentOps await 期间停止并恢复 Quant | 短暂连接失败可恢复 | 旧实现首次 ConnectError 即使 workflow 失败 | FAIL（保留，已修复） | ~15s |
| 2026-08-19T21:54:00Z | External tool 重试修复 | 对 ConnectError/HTTP 503 增加有界重试，默认最多 15 次 | Quant 重启窗口内不丢失 workflow | focused tests 覆盖重试成功与上限失败；第三次 8-trial 实验穿越 Quant 重启并完成 await/verifier | PASS | ~3s |
| 2026-08-19T22:02:00Z | LLM analyst 可用性 | 第三次实验进入 `quant.result_analyst` | 有界聚合输入生成结构化分析 | 输入已由 `_research_analysis_input` 限定为聚合字段，但 Codex CLI provider 超时；运行明确 failed，未伪造分析 | FAIL（保留，待成功重试） | ~120s |
| 2026-08-19T22:07:00Z | Quant worker 强制重启恢复 | 精确停止运行 trial 的 Quant PID 并重启 | 复用 Trial/Backtest，attempt 增加且不重复 | Experiment `cc6794f2-26f2-4024-9242-f57dcb79fc38`：ordinal 0/1 attempt=2，原 Backtest ID 复用；最终 4/4 completed、4 个唯一 StrategyRun | PASS | ~2m |
| 2026-08-19T22:11:00Z | 部分失败演练 | 仅修改 E2E Experiment 的一个 queued trial 参数为无效值 | 其余 trial 保留，Experiment=partially_failed | Experiment `1e1a4c8a-1007-4b42-8cca-71219133e08d`：3 completed/1 failed，errorCode=`trial_failed` | PASS | ~1m |
| 2026-08-19T22:14:00Z | 取消演练 | 4-trial 实验运行中调用 cancel API | 不再领取 queued trial，最终 cancelled | Experiment `ff7c7a42-695d-4c7d-af79-6f0282cbb55a`：取消前 1 running/3 queued；最终 1 completed/3 cancelled | PASS | ~1m |
| 2026-08-19T22:17:00Z | 数据漂移演练 | 精确插入唯一 `vendor_event_id=codex-e2e-data-drift-…` 的公司行动 | 下一 trial 校验触发 data_changed，不混合结果 | Experiment `ec34c99e-184d-4e66-9acf-bd86656ca38e` 进入 data_changed；一个 trial failed、三个 cancelled | PASS | ~1m |
| 2026-08-19T22:18:00Z | 数据漂移清理 | 删除前核对唯一 ID/行，按主键删除并复查 | 只删除注入行 | 删除 `corporate_actions.id=148281`；目标匹配 1→0，其他 `codex-e2e` 行为 0 | PASS | <1s |
| 2026-08-20T23:48:20Z | 代码策略计划审批 | Quant Frontend 批准真实 Codex Planner 结果 | 进入 coder | WorkflowRun `3aabd8c5-b5ff-481c-9bfe-4bd76276fb03` 的计划包含共享引擎、T/T+1、回归测试、OpenAPI/前端/双语同步和禁止项 | PASS | ~20s |
| 2026-08-20T23:48:20Z | 审批后运行可观测性 | UI 应立即显示 coder 节点运行中 | API 状态和长任务节点实时更新 | coder 已实际启动，但 API 仍显示 `waiting_approval`/coder pending，暴露长节点开始前未提交状态的缺陷 | FAIL（保留，待修复） | ongoing |
| 2026-08-20T23:58:20Z | 最终研究规格审批 | Quant Frontend 展示 normalized spec、trial 数量和成本估算后批准 | 4 trials、并发 2、1 次分析调用 | UI 显示 trialCount=4、backtestRuns=4、maxConcurrentTrials=2；审批 ID `d1492ca4-9f47-42b5-9f60-f05f8188d29c` | PASS | ~20s |
| 2026-08-20T23:59:19Z | 完整研究 WorkflowRun | Planner→validate→approval→create→await→verify→analyze | 所有节点和结构化分析完成 | WorkflowRun `164b2f63-33fd-4fae-8208-e0897734ef4a` completed；Experiment `21f3fa80-f7db-459f-9216-982afd015837` 4/4 completed | PASS | ~59s |
| 2026-08-21T00:01:00Z | 前端研究详情与深链 | UI 展示 trial/report，并可进入现有 Backtest 页面 | 4 行 trial、4 个深链、Backtest 内容可读 | `/research/21f3…` 显示 4/4 和 base/stress、in/out-of-sample；点击 `4c177104` 进入 `/backtests/4c177104-…`，指标/交易/曲线均加载 | PASS | <2s |
| 2026-08-21T00:03:00Z | 首次代码策略 mock 验证 | Planner→approval→coder→verifier→bounded repair→mock delivery | 隔离工作区且完整验证 | WorkflowRun `3aabd8c5-b5ff-481c-9bfe-4bd76276fb03` 暴露未提交报告进入工作区、依赖缺失和 AgentOps `DATABASE_URL` 污染 Quant 三项问题；在修复上限内明确失败 | FAIL（保留，已修复） | ~15m |
| 2026-08-21T00:09:00Z | Coder 工作区与验证环境修复 | 使用本地 clean clone；验证期间只链接依赖；过滤数据库/凭证变量并强制 paper 开关为 false | Coder 只能看到已提交基线，验证不读取 AgentOps 数据库或密钥 | 工作区初始 clean，`.env` 和未提交报告不存在；`.venv`/`node_modules` 仅在验证时临时链接并于 finally 移除 | PASS | ~5m |
| 2026-08-21T00:10:00Z | 长节点可观测性修复 | 节点进入 running 后先提交状态再执行长任务 | UI/API 可即时显示 coder/verifier 运行中 | 新运行中两个审批节点与代码摘要均可在 Quant Frontend 查看 | PASS | <1m |
| 2026-08-21T00:17:00Z | 代码策略 mock 闭环 | WorkflowRun `fd062507-f48b-4203-b406-1cd4d50c6744` | 两次审批、Coder、Quant Verifier 和 mock Draft PR 全部成功 | 所有节点 completed；mock delivery=`https://github.com/local/quant/pull/mock-fd062507` | PASS | ~8m |
| 2026-08-21T00:22:00Z | Momentum Breakout 变更复核 | 审查 Coder 的五文件 diff，并在 Quant 集成分支应用加固 | 共享行为、T/T+1、DST、成本和契约均有显式覆盖 | 提交 `17bd739`；新增确定性 close timestamp、1d/close 限制、周末 T+1、forward-adjusted SQL、费用/滑点精确值回归 | PASS | ~4m |
| 2026-08-21T00:25:00Z | 最终本地全量验证 | 两仓库测试、compileall、lint/build、migration/workflow 校验 | 所有本地验收通过 | Quant 46 tests、compileall、lint/build；Coding Agent 86 tests、compileall；Web Console typecheck/build；Alembic `0007 (head)`；三套 workflow JSON valid | PASS | ~2m |
| 2026-08-21T00:28:39Z | Git/远端只读审计 | 检查 status、commit、remote、upstream | 明确外部写入目标且不推送 | Quant 仅报告未提交，Coding Agent clean；两个同名分支均无 upstream；远端分别为 `zh-lov233r/mini_quant_trading_platform` 和 `zh-lov233r/coding_agent` | PASS | <1s |
| 2026-08-21T02:47:00Z | Quant 精确分支推送 | 经用户授权后推送两个明确 commit/ref | 发布集成 base 和代码策略 head，不带入未提交文档 | `3f816ae` → `codex/quant-agent-research-integration`；`17bd739` → `agentops/run-fd062507` | PASS | ~4s |
| 2026-08-21T02:48:00Z | GitHub App 创建 Draft PR | 使用 GitHub connector 创建相同 base/head 的 draft | Draft PR 创建成功 | GitHub API 返回 403 `Resource not accessible by integration`；未产生 PR 或其他写入 | FAIL（保留，已使用已认证 CLI 恢复） | <1s |
| 2026-08-21T02:49:25Z | 真实 Draft PR 创建与复核 | 已认证 `gh` 后备创建；GitHub App 只读复核 | open、draft、未合并，diff 仅为已验证代码策略提交 | PR #4；base=`codex/quant-agent-research-integration@3f816ae`；head=`agentops/run-fd062507@17bd739`；1 commit、5 files、mergeable=true、merged=false | PASS | ~3s |
| 2026-08-21T02:51:00Z | Draft PR checks 复核 | `gh pr checks 4`、PR metadata/file list | 保持 draft 且明确 CI 状态 | PR 为 OPEN/DRAFT、merge state CLEAN；GitHub 未配置或未触发 checks，依赖本报告记录的本地全量验证 | PASS（有上线风险说明） | <2s |

失败记录不得从本表删除；恢复后追加新记录。

## 4. 数据库升级与 Schema 证据

执行结果：

- Quant schema-only 备份：`/tmp/codex-quant-agent-e2e/quant-schema-20260819T212458Z.sql`，32,004 bytes，SHA-256 `23c1c9df7755235e2bb0b086d8b368414a484953a57681beab9cc1de1fd0a123`。
- Quant 当前目标：database `hzy`、schema `public`；升级前只有 `strategies.idempotency_key VARCHAR(64)`，研究表不存在。
- Quant additive SQL 成功：`strategies.idempotency_key VARCHAR(128)`；`research_experiments`、`experiment_trials`、状态检查、外键、唯一约束和领取/追踪索引均存在。
- AgentOps `0006 -> 0007` 成功；`external_tool_runs` 的 input hash、幂等键、外部引用、请求/响应、输出摘要、错误、attempt 和时间字段均存在。

## 5. 资源追踪

| 资源 | ID / URL | 状态 | 关联资源 |
| --- | --- | --- | --- |
| AgentOps Project | `2f0f92a6-fc47-4ffe-a23a-f565a23c2fc1` | ready | 三套已发布 Quant workflow |
| 参数策略 WorkflowRun | `e2334154-0cd0-4362-a110-d52735e770cd` | completed | Quant Parameter Strategy v1 |
| 参数策略 ToolRun | validate=`dd600c47-a515-4e87-88df-4ecbd36da6e9`; create=`53d1dce5-a9f4-4f80-9947-36a8e624a119` | completed | 同一 WorkflowRun |
| Draft Strategy | `ae2f6e36-c5d1-4e1f-bd1f-111bbb14cfe6` | draft | create ToolRun；idempotency key `e2334154-0cd0-4362-a110-d52735e770cd:create` |
| 研究 WorkflowRun | `89faedf5-fe6f-4d88-be91-336708f17ec8` | downstream failed（重启连接窗口）；Experiment completed | ResearchExperiment `000c786a-346b-4822-aa5c-89f84c10bf3a` |
| 最小 ResearchExperiment | `000c786a-346b-4822-aa5c-89f84c10bf3a` | completed：8/8 | fingerprint `8e6645aa…3c2a94`；Strategy `ae2f…cfe6` |
| ExperimentTrial / Backtest | 8 个 trial / 8 个唯一 Backtest | completed | 示例 Trial `49e79fdb-8572-42bc-b7fa-fa5e6b3e79a3` → Backtest `a7a2b6fa-9ae1-4c08-b3dd-a7bbfcd6d232` |
| Worker 恢复 Experiment | `cc6794f2-26f2-4024-9242-f57dcb79fc38` | completed：4/4 | 原 running Trial `e3233ed4-…` / Backtest `2c5e6fc0-…` 被复用，attempt=2 |
| 部分失败 Experiment | `1e1a4c8a-1007-4b42-8cca-71219133e08d` | partially_failed：3/1 | 失败 Trial `3caf7d71-6660-4014-aa74-b102aa97dde1` |
| 取消 Experiment | `ff7c7a42-695d-4c7d-af79-6f0282cbb55a` | cancelled：1 completed/3 cancelled | 未领取新 trial |
| 数据漂移 Experiment | `ec34c99e-184d-4e66-9acf-bd86656ca38e` | data_changed | 唯一注入行已清理 |
| 最终研究 WorkflowRun | `164b2f63-33fd-4fae-8208-e0897734ef4a` | completed | Experiment `21f3fa80-f7db-459f-9216-982afd015837` |
| 最终研究 ToolRuns | validate=`4cbd13b6-95bd-4b63-bfef-a38da879d4b0`; create=`30ad43d5-be01-4900-9d75-5339d8bf83a7`; await=`de4210b3-6f51-4af2-8ed4-e539cc63a1ed`; verify=`cd3bb841-90e7-4d1b-9e14-9d24310734f9` | completed | 同一 WorkflowRun / Experiment |
| 最终 ResearchExperiment | `21f3fa80-f7db-459f-9216-982afd015837` | completed：4/4 | fingerprint `8e6645aa…3c2a94` |
| 最终 Trial / Backtest 示例 | Trial `02783601-3101-4b07-b8d9-b238d8be8856` → Backtest `4c177104-e10f-43b5-9d88-7a2b62e878ad` | completed | UI 深链已验证 |
| 代码策略失败 WorkflowRun | `3aabd8c5-b5ff-481c-9bfe-4bd76276fb03` | failed（保留故障证据） | 工作区/依赖/环境隔离问题 |
| 代码策略成功 WorkflowRun | `fd062507-f48b-4203-b406-1cd4d50c6744` | completed | plan approval=`c0d7b783-f902-4ea6-8b7c-d9b5a4a2eef3`；risk approval=`7501508b-a55d-4a02-a3da-570a18c44c69` |
| Mock Draft PR | `https://github.com/local/quant/pull/mock-fd062507` | draft mock / completed | 同一成功 WorkflowRun；无外部写操作 |
| 真实 Draft PR | `https://github.com/zh-lov233r/mini_quant_trading_platform/pull/4` | open / draft / unmerged | base `codex/quant-agent-research-integration@3f816ae`；head `agentops/run-fd062507@17bd739`；WorkflowRun `fd062507-…` |

## 6. 场景验收

### 6.1 参数策略

从 Quant Frontend 提交受约束的 `mean_reversion` 目标，结构化 proposal 仅包含 `risk.position_size_pct=0.05`、`risk.max_positions=2` 和 AAPL/MSFT。Quant 校验返回 valid 后，页面展示完整 review payload 并停在人工审批；批准后 validate、approval、create 四个节点均为 completed。

- Strategy 固定为 `draft`、`engine_ready=true`，数据库中同一 idempotency key 仅 1 行。
- 同请求同键重放返回 HTTP 201 和同一 Strategy ID；同键改变名称返回 409。
- `strategy_allocations` 对该 Strategy 的行数为 0；数据库无 order/trade 表，工具白名单也无 broker/order 操作。
- 负向验证：不存在的 `signal.entry_zscore` 被 Quant 以 422 阻止，未创建策略。

### 6.2 最小研究实验

从 Quant Frontend 创建的最小研究实验展开为 8 个稳定排序 trial：2 个 position-size 参数、in/out-of-sample 两个窗口、base/stress 两个成本场景。8 个 trial 均完成并关联 8 个唯一 Backtest。

- RunManifest 固化 AAPL/MSFT universe、1,050 行数据、最大 as-of 和 SHA-256 `8e6645aae74d0c1abef1ba48678ad92b69ef2f4181d1b12feca424bf843c2a94`；每条 lineage 保存同一指纹。
- 实际 runtime params 保存在 StrategyRun config snapshot，参数 hash 分别为 `af08526c…e372c` 和 `7e364182…d6a5`，没有修改基础 Strategy。
- report 同时包含 in/out-of-sample、base/stress、成本衰减、benchmark excess return、集中度、symbol contribution 和完整 Trial→Backtest lineage。
- Backtest 的执行模型标记为 next-session open；UI 深链使用 `/backtests/{backtestRunId}`。示例：Trial `49e79fdb-8572-42bc-b7fa-fa5e6b3e79a3` → Backtest `a7a2b6fa-9ae1-4c08-b3dd-a7bbfcd6d232`。
- 确定性报告含免责声明：只构成研究证据，不保证盈利或实盘安全。

第二个从前端创建的 4-trial 验收运行完整通过了最终 LLM 分析节点。结构化输出明确分为 `facts`、`inferences`、`risks` 和 `nextExperiments`，并保留 disclaimer；结论指出策略虽在模型成本后为小幅正收益，但显著落后 SPY，且两只股票、8 笔交易、单参数和单次切分不足以证明泛化、盈利能力或实盘安全。

前端 `/research/21f3fa80-f7db-459f-9216-982afd015837` 展示 4/4、四行 trial、两个样本窗口和两种成本场景；四个 Backtest 均为可点击深链。实际点击 `4c177104` 后，现有回测页面成功展示总收益 0.34%、最大回撤 0.22%、30 signals、8 transactions、SPY 对比与交易生命周期。

### 6.3 恢复与故障

- AgentOps 在 `waiting_external` 期间重启后复用同一个 await ToolRun `b45e6a26-87e5-408b-ad02-e1a5014cff73`；attempt 从 5 增至 14，没有重复 Experiment、Trial 或 Backtest。
- Quant worker 强制重启时，精确目标 trial 已为 running/attempt=1。恢复后同 Trial 和 Backtest ID 被复用，相关 attempt=2；最终 4 个 trial 对应 4 个唯一 StrategyRun。
- 单一无效 queued 参数只使目标 Experiment 成为 `partially_failed`（3 completed/1 failed），其他结果和报告可读取。
- 运行中取消后停止领取三个 queued trial，最终 `cancelled`（1 completed/3 cancelled）。
- 唯一公司行动注入使实验进入 `data_changed`，未将变化后的数据与已创建的比较结果混合。测试行随后按精确主键删除并验证无残留。

### 6.4 代码策略

真实 Codex Planner 已生成 `momentum_breakout` 实施计划，Quant Frontend 完成 plan approval 和高风险 delivery approval。成功运行 `fd062507-f48b-4203-b406-1cd4d50c6744` 在本地 clean clone 中执行 Coder，隔离工作区不包含主仓库未提交文件、`.env` 或数据库凭证；Coder 只修改五个允许文件。

- 后端 registry/engine 新增 engine-ready 的 `momentum_breakout`，backtest 与 paper 复用同一策略行为。
- 信号被限制为日线收盘、成交仍由既有引擎在下一可用 session 开盘完成；周末 T+1、DST 时区和 symbol/date 稳定排序均有回归测试。
- forward-adjusted 特征查询、手续费和滑点显式预期值、OpenAPI execution contract 与前端类型已同步。
- Quant Verifier 无 scope violation；隔离工作区内后端 46 tests、compileall、前端 lint/build 全部通过。
- Mock Draft PR 已生成且未产生远端写入；随后只有在用户通过单独的外部写操作审批门后才执行真实推送和 Draft PR 创建。
- 用户于 2026-08-21 明确授权外部写入后，真实 [Draft PR #4](https://github.com/zh-lov233r/mini_quant_trading_platform/pull/4) 创建成功。PR 仅含 `17bd739` 的 5 个文件，base 为远端 Quant 集成基线；复核时 `draft=true`、`merged=false`、`mergeable=true`。

## 7. 自动化验证

| 仓库 | 检查 | 结果 |
| --- | --- | --- |
| Quant | 后端全量测试 | PASS：`46 tests in 0.793s` |
| Quant | compileall | PASS |
| Quant | 前端 lint/build | PASS / PASS |
| Coding Agent | 全量 Python 测试（含 PostgreSQL 测试） | PASS：最终 `86 passed in 7.94s`；早期基线 `78 passed in 7.14s` 保留于执行日志 |
| Coding Agent | compileall | PASS |
| Coding Agent | Alembic head / workflow JSON | PASS：`0007 (head)`；三套 Quant workflow JSON 均 valid |
| Coding Agent | Web Console typecheck/build | PASS / PASS |
| 跨仓库 | 浏览器 E2E | PASS（本地）：参数策略、两级审批、研究详情/状态/部分结果、Backtest 深链和代码策略 mock 均已验证 |

## 8. 问题、修复与提交

| 问题 | 影响 | 修复 | 仓库 / 提交 |
| --- | --- | --- | --- |
| Docker daemon 未运行 | AgentOps PostgreSQL 和数据库集成测试无法启动 | 已启动 Docker Desktop；AgentOps 全量测试通过 | 环境问题，已恢复 |
| Quant 数据库配置源码含预存凭证字面量 | 凭证轮换和源码泄露风险 | 不记录原值；默认连接改为无凭证本地配置，部署凭证仅从环境变量读取 | Quant / `e574286` |
| 本地无 OpenAI/Anthropic/Gemini API key | 三个结构化 Quant Agent 无法真实运行 | 增加显式 `codex_cli` provider，复用已认证 Codex；只读、ephemeral、schema constrained | Coding Agent / `76d3ccf` |
| 联合启动使用跨仓库相对路径且 Quant 探针路径错误 | AgentOps 未启动，启动脚本误判 Quant | 改用两个仓库绝对路径；Quant 探针改为 `/healthz` | 运行命令，待复测 |
| Quant `127.0.0.1` 前端被默认 CORS 拒绝 | 页面无法读取 Quant API | 联调启动显式设置匹配的 `FRONTEND_ORIGIN` | 运行配置，已恢复 |
| Bootstrap 默认 scope 为 `repos/quant/**` | WorkflowRun 创建返回 500 | scope 收窄到仓库允许的六个路径；bootstrap 可修复既有项目 | Coding Agent / `76d3ccf` |
| Project runtime 不接受/未选择 `codex_cli` | 服务设置被项目 snapshot 的 `openai` 覆盖 | runtime 类型支持 `codex_cli`，项目配置明确选择该 provider | Coding Agent / `76d3ccf` |
| 服务进程 PATH 找不到 Codex | planner 明确失败 | `CODEX_BIN` 使用已审计绝对路径 | 运行配置，已恢复 |
| 审批恢复无法穿越多个已完成节点 | approval/create 未运行却将 WorkflowRun 标记 completed | 增加已完成节点遍历集合并继续到 pending frontier；扩充回归测试 | Coding Agent / `76d3ccf` |
| 并发 worker 完成最后 trial 时 finalize 看到旧事务快照 | 8 个 trial 已 completed，但 Experiment 停在 running | trial 终态先 commit，再重载 Experiment；启动恢复同时 finalize 所有 ready Experiment | Quant / `e574286` |
| Quant 重启时 external await 对 ConnectError/503 立即失败 | Experiment 可继续但 WorkflowRun 丢失后续报告节点 | 对 transient 错误增加默认 15 次有界重试，并覆盖成功/耗尽单测 | Coding Agent / `76d3ccf` |
| `data_changed` 终态统计在同一事务内未 flush | API progress 暂时显示旧 running/failed 计数 | refresh 前显式 flush；重启后复核为 1 failed/0 running/3 cancelled | Quant / `e574286` |
| Codex builtin Planner 不支持 `codex_cli` 且 schema 非 strict | 代码策略无法进入 coder | 增加 Codex CLI Planner、共享 strict schema 转换并去除 transport wrapper | Coding Agent / `76d3ccf` |
| 结构化 analyst provider 超时 | 第三次研究 workflow 在最终分析节点失败 | 输入限定为聚合字段且失败时不伪造输出；240 秒上限下最终 WorkflowRun `164b2f63-…` 成功 | Coding Agent / `76d3ccf`；复核 PASS |
| 长节点开始前状态未持久化 | coder 已运行但 UI/API 仍显示 waiting approval | 节点 running 状态先提交，再执行长操作；增加审批→coder 可观测性回归测试 | Coding Agent / `76d3ccf` |
| Coder workspace 复制了 dirty/untracked 文件 | 未提交报告触发 scope violation，隔离边界不可信 | 改为 `git clone --local --no-hardlinks` 的已提交 clean snapshot | Coding Agent / `76d3ccf` |
| Coder workspace 缺少 `.venv`/`node_modules` | Verifier 无法执行完整测试和构建 | 只在 verifier 生命周期临时链接已审计依赖，finally 移除 | Coding Agent / `76d3ccf` |
| AgentOps `DATABASE_URL` 传入 Quant 验证 | Quant 测试错误连接 asyncpg/AgentOps 数据库 | 构造最小安全验证环境，过滤数据库与凭证变量，并强制关闭两个 paper 开关 | Coding Agent / `76d3ccf` |
| Momentum fallback timestamp 使用固定 UTC | 夏令时下不等于纽约收盘，可能改变信号日期语义 | 以 `America/New_York` 16:00 本地化后转 UTC，并加入 DST 回归 | Quant / `17bd739` |

## 9. 测试数据清理

仅允许清理由本次 E2E 创建、且带唯一 E2E 标识的资源。每次删除前记录精确查询结果和预计行数；不得删除已有策略、实验或市场数据。

- 数据漂移注入行：删除前精确匹配 `vendor_event_id=codex-e2e-data-drift-6977db0a-0d87-4d21-a009-828d83b1b78e` 为 1 行，主键 `148281`；删除该主键后匹配为 0，其他 `codex-e2e` 公司行动为 0。
- E2E 创建的 Strategy、Experiment、Trial 和 Backtest 暂时保留为本报告及 UI 深链证据；未修改或删除任何执行前已有资源。

## 10. 最终结论

`PASS`

参数策略、研究实验、故障恢复、数据漂移、代码策略 mock 和真实 Draft PR 均已完成验收；所有本地测试、compileall、lint、production build、migration 和 workflow schema 校验通过。Broker/order 调用为 0，paper scheduler 与订单提交在整个联合运行期间保持关闭。

真实 [Draft PR #4](https://github.com/zh-lov233r/mini_quant_trading_platform/pull/4) 保持 open、draft、未合并、未部署。该 head 当前没有 GitHub checks；上线前仍需人工 review，建议在合并前补跑或配置远端 CI，并再次确认生产数据库 rollout、服务 token 管理和 paper/live 权限隔离。本报告中的回测和稳健性结果不构成盈利或实盘安全保证。

## 11. 2026-08-25 双入口重构补充验证

本节记录“已有引擎大类研究 / 新算法研究”重构的真实执行过程；不改写前述旧流程验收历史。

### 11.1 环境与 rollout

- Quant 与 Coding Agent 均位于 `codex/quant-agent-research-integration`。
- rollout preflight 在变更前读到 10 个历史实验、0 个非终态旧实验，因此未阻止 additive schema rollout。
- Quant schema 已先备份到本机临时文件，再新增 `experiment_rounds`、`experiment_candidates` 和 nullable `experiment_trials.candidate_id`。
- AgentOps Alembic 已升级到 `0009`，Approval resolution payload 已持久化。
- 联合启动全程显示 paper scheduler 与 paper order submission 均为 disabled；AgentOps 注册工具不含 broker/order 操作。

### 11.2 失败过程与修复

| UTC 顺序 | WorkflowRun | 实际结果 | 修复 |
| --- | --- | --- | --- |
| 1 | `6a57555d-c62d-4c49-84ee-581a6bf22787` | `category_plan` 因 `invalid_json_schema` 失败 | 将任意键参数对象改为 strict `overrideItems` 数组，并在受控工具边界转换为 Quant 参数映射 |
| 2 | `b4bece8c-25ab-492e-b17c-4b91cba14aea` | Quant 拒绝不存在的 `signal.fast_window` | 增加初始提案最多两次有界修复，并把 catalog 默认参数传给 Planner |
| 3 | `0683784a-1bb4-44a9-9575-dc9836307dcd` | 前端字段名错误导致 `strategyDefaults={}` | 改为 catalog 的真实 `defaults` 字段 |
| 4 | `c4e4c6fb-cb66-4b65-abbc-42a97b2c2fe1` | 两次修复后仍包含数据库不支持的 EMA30 | 同步 `/api/strategies/feature-support` 到 Planner，并明确实际 trial 展开预算公式 |
| 5 | `1a4469aa-fecb-4cfc-9df6-3d9f2f59d9e5` | 完整闭环完成 | 无需进一步修复 |

### 11.3 成功闭环证据

| 资源 | ID | 结果 |
| --- | --- | --- |
| WorkflowRun | `1a4469aa-fecb-4cfc-9df6-3d9f2f59d9e5` | `completed`；两次审批均从 Quant Frontend 完成 |
| 自动基础 draft | `d90ef5cd-a708-4174-b9cf-deaa07906876` | `draft`、`engine_ready=true`；无 allocation/order |
| ResearchExperiment | `8aec87c3-9f5c-41da-8f9b-094c31974c87` | `completed`；2 candidates × 2 samples × 2 costs = 8/8 trials |
| 数据指纹 | `da0f952c20ed1b128199650d87b644510f20758657d706002cb073e881831813` | AAPL/MSFT 固化数据快照 |
| 最终推广 | 同一 WorkflowRun 的 `quant.promote_candidates` ToolRun | 空 `candidateIds` 正常完成，未重复创建 draft |

该最小场景的所有 trial 都没有产生交易，因此 `oos_sharpe` 为缺失值。候选按设计保留指标和 Backtest 证据，但不进入 Pareto frontier，并以 `no_valid_candidates` 停止；分析节点仍完成，未伪造 Sharpe 或盈利结论。

### 11.4 自动化验证与结论

- Quant：60 个 backend tests、compileall、frontend lint、frontend production build 全部通过。
- Coding Agent：91 个 control-plane tests、compileall、Web Console typecheck/build、Alembic `0009` 和 workflow JSON 校验全部通过。
- OpenAPI 已删除旧有限网格 mutation 契约；旧实验、trial、report 与 Backtest 仍可只读。
- 浏览器已验证 `/research` 只有两个入口、动态大类 catalog、实验审批、进度、最终空选择和历史深链。

本次补充验证结论：`PARTIAL`。实现和单轮完整闭环已通过；尚未完成一个真实的三轮浏览器 E2E，也未在本次补充运行中重新执行 provider 故障、重启、取消和数据漂移演练（这些故障语义已有前述历史 E2E 与自动化测试证据）。回测结果不构成盈利或实盘安全证明。
