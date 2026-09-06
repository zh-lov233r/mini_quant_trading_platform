# 信号中心

[English](signal-center.md) · [文档索引](README.zh-CN.md)

## 使用流程

打开 `/signals`，选择一个市场、股票池和多个已保存策略实例。存在有效完成标记时可省略日期，否则明确选择历史交易日。扫描冻结股票池 instrument 身份、策略版本、标准化参数和算法 revision。页面确认手动扫描已提交后，由 worker 在后台继续处理；页面不显示扫描历史、进度、结果或手动生成报告入口。手动扫描不会自动生成报告或发送邮件。

每日任务保存名称、市场、股票池、固定策略版本、报告语言、收件人及保留交易日数。新任务默认停用；启用表示授权自动扫描、发布、邮件和配置范围内的保留清理，同时仍需开启下方运维开关。暂停阻止新增扫描，已入队或运行中的任务继续使用冻结配置完成。修改只影响后续运行。停机恢复只选择最新有效完成日；任务/日期唯一键防止重启或行情修订重复发报。不提供复杂 cron 和逐日历史补报。

报告分别显示成功零信号、覆盖不完整和失败。强度仅在同一个策略实例内排名。报告页支持策略/股票/过滤条件、URL 选择、上一条/下一条，以及不可变图表证据。点击观察结果的任意单元格即可展开图表，再点击同一行收起。窗口为 60/120/250 根，默认 120 根；向前加载受快照范围限制。同股策略、语言及图层切换保留图表视图。缺失成交量保留缺失。过期链接返回 HTTP 410，不显示最新行情。

信号中心采用扁平分区和轻量分隔线，沿用平台的可搜索股票池选择器与状态徽标。策略选择复用策略库的卡片外壳，包括大类渐变、顶部色条、边框、字体层级和大类颜色，并采用紧凑多选布局。选中后增强对应大类颜色，不可扫描实例保持禁用。页面其他区域避免嵌套卡片。手动扫描与定时任务表单显示当前模式，空列表提供下一步提示；控件支持键盘焦点和移动端触控尺寸。

`GET /api/signal-scans/{identity}/instruments/{instrument_id}/chart` 必须传入 `strategy_run_id` 和 `signal_id`，可传 `limit` 和 `before`，并校验其扫描/股票归属。返回结构与报告图表相同，包含 `scan_id`，`report_id` 为 null；报告图表包含两个 ID。未结束扫描返回 409，已清理扫描内容返回 410。读取期间持有扫描共享锁以防保留清理，不回退到当前行情。本次无需数据库 schema 变更。

## 检测与输入边界

九类内置策略使用 `quant_kernel.observe_market`，与日评估和回测共享原生判断及状态机；拒绝 `custom`。不向检测层传入假持仓、券商状态或假想成交。交叉及带明确确认锚点的观察属于离散事件，其余满足判断条件的观察属于当日持续条件。原执行引擎仍保持 T 日收盘信号、下一有效 session 开盘成交。

扫描在行情读锁内分批读取 PostgreSQL；每个 instrument 的 PreparedDataset v5 数组在策略实例之间复用。形态预热要求来自原生配置；SR 从全部可用历史冷重放到所选日期，每个实例独立维护状态。启用 SR 市场过滤时要求真实基准 close/SMA 数据。必要字段、OHLC 几何及截止日期检查将缺失/无效数据与成功零信号分开。OHLC 逐字段优先前复权，沿用未复权回退并明确记录。

扫描 manifest 保存冻结成员及各 instrument 的实际输入边界。确认时间采用可信市场日历的收盘时间；缺失时精度为交易日，不把供应商日线时间戳误当成信号确认时刻。只有存在观察的股票生成图表资产：最近最多 **1,000 个实际可用 session**、指标和原生结构证据。这是可复现的报告/图表切片，并非所有扫描输入的完整历史归档；不推断历史股票池成员。与上一报告比较要求 instrument 成员、策略身份、版本、参数、revision 相同且覆盖完整，否则报告说明不可比原因。

## 数据就绪与日历

`signal_data_ready` 保存已提交的市场/交易日完成记录、数据版本和覆盖计数。美股每日完整维护在行情、公司行动、复权、特征及质量检查后发布；A 股完整导入在复权、特征和校验后发布。局部导入、跳过特征/质量检查及失败流程不能发布全市场标记。维护修改前令旧标记失效。调度器还检查维护门禁，每次扫描再次校验所选范围；等待不能表示为成功空扫描。

`signal_market_sessions` 持久保存日期、开收盘时间、来源与获取时间。A 股导入使用 [Tushare trade_cal](https://tushare.pro/document/2?doc_id=26)；美股维护保存 [Massive upcoming holidays](https://massive.com/docs/rest/stocks/market-operations/market-holidays) 的未来覆盖和提前收市信息，不使用前瞻接口推断缺失历史日期。日历错误会延后清理，直到存在可信覆盖。Dashboard 和图表读取不调用供应商接口。

## 发布、邮件与保留

固定 ReportDocument 发布后不可变，HTML、JSON、完整 CSV、PDF 均从该文档渲染。CSV 包含过滤掉的观察及结构证据。重新生成创建新报告身份；导出重试只从原文档补齐缺失格式，不重新扫描。正文先独立提交，邮件可立即入队；HTML、JSON、CSV 和 PDF 使用 `signal_report_render_jobs` 独立渲染队列。报告详情通过 `render_status`、`render_error` 和 `render_errors` 展示导出进度与失败；最多自动尝试三次，之后可手动重试缺失格式。渲染失败不改变正文的成功状态或重新扫描。

报告资产存放在独立的 `SIGNAL_REPORT_STORAGE_DIR`，API 与 worker 共享。JSON/gzip 快照和导出按内容寻址、原子写入，读取时校验。备份时应同时保存该目录和信号数据库表。

SMTP 使用证书校验的 STARTTLS，凭证仅来自环境变量。邮件包含摘要、报告链接和保留提示，不附加行情历史。报告与投递状态独立。确定的暂时拒绝最多尝试三次；DATA 开始后的超时/断开标记为 `unknown`，重启恢复也保持该状态，不自动重发。只有确定 `failed` 的投递可手动重试。自动化测试模拟 SMTP。

定时报告默认保留 **三个完整市场交易日**：从发布时间之后尚未开盘的下一个 session 开始计数，在第三个 session 收盘时到期。周一收盘后发布，通常周四收盘后到期。周末、节假日、提前收市、时区及延迟发布均使用持久日历时间。缺失日期显示 `calendar_incomplete` 并延后清理；手动报告默认不过期。

清理先发布过期墓碑，再以可重试步骤删除导出、独占观察与快照。手动报告或其他有效报告引用的共享数据继续保留。运行中任务、正在投递及持锁读取的资产受到保护；任务身份、日期、结果计数、过期状态与投递摘要保留用于审计和幂等。已读入内存的下载可完成传输。

维护入口必须明确指定 US 或 CN，只失效所属市场的就绪标记；局部导入也不会清除另一市场的标记。维护门禁仍在全局排空期间阻止新增扫描。

## Schema 与部署

数据库表缺失时，信号接口返回带 `signal_schema_missing` 的 503，`/readyz` 返回 503 和缺失表名。Dashboard 保留其他模块，并明确显示信号中心尚未部署；不会显示为成功空报告。维护在修改门禁之前检查必要表。

项目没有 Alembic 工作流。本功能增加九张 `signal_*` 表，不重置或迁移用户行情。**应用到现有数据库前必须核对准确目标并取得授权。** 上线前备份现有信号表及资产目录；部署 schema 时停止 API、worker 和导入，保留行情备份。回退应停止新 worker，恢复匹配的代码/数据库/资产备份，不应直接删除用户表。

```bash
# 只读输出目标和表计数，DATABASE_URL 来自运维环境。
make signal-schema
# 不连接数据库，审阅增量 DDL。
make signal-schema SIGNAL_SCHEMA_ARGS="--sql"
# 仅在目标核对并授权之后执行：
make signal-schema SIGNAL_SCHEMA_ARGS="--apply"
.venv/bin/pip install --no-build-isolation --no-deps -e backend/native
# 独立 worker，也由 make dev/dev-agent-safe/dev-agent-all 监管。
make signal-worker
# 默认 dry-run，只有显式 --apply 才删除。
make signal-cleanup
```

待审阅 SQL：[`create_signal_center.sql`](../backend/utils/create_signal_center.sql)。`make signal-cleanup SIGNAL_CLEANUP_ARGS="--apply"` 执行配置范围内的过期删除，必须符合操作者意图；开发期间关闭自动清理。

| 环境变量 | 默认值 / 用途 |
| --- | --- |
| `SIGNAL_SCAN_SCHEDULER_ENABLED` | `false`；允许就绪日任务调度 |
| `SIGNAL_REPORT_DELIVERY_ENABLED` | `false`；允许 SMTP，启用任务时也要求开启 |
| `SIGNAL_REPORT_RETENTION_ENABLED` | `false`；允许 worker 清理 |
| `SIGNAL_REPORT_STORAGE_DIR` | 仓库 `data/signal-reports`；持久共享目录 |
| `SIGNAL_REPORT_PUBLIC_URL` | `http://localhost:3000`；可访问的报告地址前缀 |
| `SIGNAL_SMTP_HOST`、`SIGNAL_SMTP_FROM` | 发邮件时必填 |
| `SIGNAL_SMTP_PORT` | `587`，STARTTLS |
| `SIGNAL_SMTP_USER`、`SIGNAL_SMTP_PASSWORD` | 可选服务认证，只来自环境 |

Docker 中 `signal-worker` 与 backend 共享持久目录 `/app/data/signal-reports`。本地 Make 监管会在进程失败后重启。PostgreSQL 提供 SKIP LOCKED 领取、120 秒租约、20 秒心跳及最多三次尝试；每轮对扫描、正文发布、邮件、导出渲染各领取最多一个任务，避免扫描持续到达时后续阶段无法执行。四类任务分别恢复。安全开发启动关闭 Paper 调度/下单及三个 Signal 自动开关，手动扫描/报告仍可执行。不引入 Redis 或 Celery。

## API 与验证

以 [OpenAPI](../apps/openapi.yaml) 为准。接口包括 `/api/signal-scan-plans`（创建/列表/修改/启停）、`/api/signal-scans`（入队/状态/观察/手动报告）和 `/api/signal-reports`（列表/详情/观察/图表/导出/渲染重试/投递/重试）。入队支持 `Idempotency-Key`。观察分页支持 `limit`、`offset`、`strategy_run_id`、`instrument_id`、`q`、`passes=true|false|all`、`group=strategy|instrument`。图表要求报告、instrument、策略子任务和信号归属一致。

`GET /api/dashboard/overview` 新增 `signal_reports`：最近五份已发布未过期摘要、投递计数及有界聚合状态。等待数据、失败/不完整扫描、报告失败和邮件异常同时进入现有提醒。Dashboard 读取不触发扫描、渲染、邮件、清理或全量图表加载。

```bash
PYTHONPATH=backend:. .venv/bin/python -m unittest backend.tests.test_signal_center
# 仅使用独立初始化的可丢弃 PostgreSQL 测试库：
SIGNAL_TEST_DATABASE_URL=postgresql+psycopg://127.0.0.1:55441/quant_signal_test \
  PYTHONPATH=backend:. .venv/bin/python -m unittest backend.tests.test_signal_center_postgres
.venv/bin/python -m compileall -q backend/src backend/utils backend/tests
cd frontend
npm test
npm run lint
NEXT_DIST_DIR=.next-signal-center-build npm run build
```

PostgreSQL 测试要求当前基础 schema 及行情/特征表，会写入合成数据，不可指向现有用户数据库。浏览器验收应在邮件、交易、清理关闭时验证手动提交反馈且不请求扫描历史、策略卡片大类与选中状态、报告深链接、快速切换、缩放/图层/语言保持、移动端/键盘和过期链接。
