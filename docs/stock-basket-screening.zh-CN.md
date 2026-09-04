# 股票池筛选

[English](stock-basket-screening.md) | [文档索引](README.zh-CN.md)

## 编辑方式

打开 `/stock-baskets`，点击创建或卡片左下角编辑。不再提供整池 ticker 长文本框。可搜索本地代码 / 公司名，也可直接添加单个 ticker；未收录或未验证的代码会提示，不触发行情下载。代码转大写并去重。已选股票支持代码搜索、每页 20 只、逐项移除和勾选本页后移除。

筛选覆盖本地 A 股和美股主数据中的当前正常上市普通股票。行业或市值筛选前必须选定市场，Tushare 行业和美股 SIC 各自独立。市值输入单位为 A 股的**亿元人民币**、美股的**亿美元**，不换汇。上下限包含边界，留空表示不筛选市值。只有设置市值边界时才排除缺失市值；缺失数量按市场、名称、行业过滤后、市值过滤前统计。展示数据日期和采集时间，未知日期不伪装成最新日期。

可逐项添加，或获取全部匹配代码、确认本次实际数量后添加，只追加不覆盖。保存通过现有组合 API 提交整个草稿；关闭则丢弃。`All Common Stock` 保持只读。成员是静态名单，不是定时规则。当前行业 / 市值快照**不是**历史时点股票池，用于历史研究可能引入选股或幸存者偏差。不改变回测时序、执行规则、策略副本或券商状态。

## API 与存储

创建策略页（`/strategies/new`，含克隆）只配置交易逻辑，不加载或选择股票范围。每次新建手工回测必须选择已保存股票池；后端会拒绝既没有静态股票池、也没有明确历史动态股票池规则的提交。运行会记录解析后的成员，因此后续编辑股票池不会改写历史结果。旧策略中的 universe 值仅作为历史配置保留，不再成为新手工回测的默认范围。

- `GET /api/stock-screening/stocks`：`query`、`market=US|CN`、`industry`、`min_cap`、`max_cap`、`limit`（默认 20、最大 100）、`offset`。API 市值上下限使用元 / 美元，不是亿元。返回 `items`、`total`、`missing_market_cap`，按代码及证券 ID 排序。
- `GET /api/stock-screening/industries?market=CN`：排序后的本地行业标签。
- `POST /api/stock-screening/symbols`：使用同样条件只读解析全部代码，不分页、不保存组合、不调用供应商。

示例：`{"market":"CN","industry":"银行","min_cap":10000000000}` 筛选总市值至少 100 亿元人民币的银行。无市场的行业 / 市值过滤以及无效区间返回 422。未建表时返回带运维说明的 503，仍可手动编辑代码。搜索防抖 300 毫秒，过期请求取消或忽略。DOM 只挂载当前页，不隐藏挂载数千个输入框或标签。

`instrument_market_caps` 按稳定 `instrument_id` 保存一份最新快照：正值 `NUMERIC(24,4)` 金额、币种、来源、可空数据日期、采集时间及原始响应。股票池 `symbols` 存储不变。美股使用 Massive `market_cap` 金额和币种；可复用现有 `vendor_payload.ticker_overview`，沿用原 `sic_asof` 采集时间，数据日期保持未知。Tushare `daily_basic.total_mv` 单位万元，乘 10,000 转为人民币元。写入前检查 ticker / FIGI 身份、币种、正且有限的金额及请求日期。旧日期或缓存重跑不会覆盖较新快照，相同响应重跑不刷新时间戳。

来源：[Massive ticker overview](https://massive.com/docs/rest/stocks/tickers/ticker-overview)、[Tushare daily_basic](https://tushare.pro/document/2?doc_id=32)。Tushare 需要 daily_basic 权限（当前文档要求至少 2,000 积分），不假设现有账户已具备权限。缺失数据、权限不足或请求失败会保留现有快照并报告，不用估算值代替。

## 手动部署与刷新

没有 Alembic，也不在启动时自动建表。建表或补齐前必须确认目标主机 / 数据库、候选数量及备份路径。以下命令在仓库根目录运行，日期替换为所需的已完成市场日期。`plan` 使用只读事务，不访问供应商，未建表也可执行。

```bash
PYTHONPATH=backend .venv/bin/python backend/utils/refresh_stock_market_caps.py plan --market US --cached
PYTHONPATH=backend .venv/bin/python backend/utils/refresh_stock_market_caps.py plan --market CN --date 2026-09-02
```

对已确定的本地库（例 `hzy`）获得明确授权后，先备份，再应用增量 SQL：

```bash
pg_dump --host=localhost --dbname=hzy --schema-only --format=custom --file=stock-screening-schema-before.dump
psql --host=localhost --dbname=hzy --set=ON_ERROR_STOP=1 --file=backend/utils/create_instrument_market_caps.sql
PYTHONPATH=backend .venv/bin/python backend/utils/refresh_stock_market_caps.py apply --market US --cached
PYTHONPATH=backend .venv/bin/python backend/utils/refresh_stock_market_caps.py apply --market CN --date 2026-09-02
PYTHONPATH=backend .venv/bin/python backend/utils/refresh_stock_market_caps.py apply --market US --date 2026-09-02
```

命令读取 `.env`，不打印凭证。在线美股更新需要 `MASSIVE_API_KEY`，A 股需要 `TUSHARE_TOKEN`；复用美股缓存无需供应商凭证。刷新只写 `instrument_market_caps`，使用数据库 advisory lock 串行化市值刷新，并在行锁内复核主数据身份。不改证券主数据、原始行情、组合成员、缓存或调度，不安装自动刷新任务。失败后重跑同一已批准命令，或用 `--symbols AAPL MSFT` / `--symbols 600000.SH` 只重试指定代码；部分失败保留成功记录。非交易日或未更新日期的 A 股接口返回零行时失败退出，不修改快照。

后续刷新前也应备份快照表。恢复范围仅限此新增表：在审核后的事务中从备份恢复其旧内容；若首次建表，可保留空表 / 新表不用并回退界面及 API 部署。不重置或恢复无关行情表，破坏性恢复需另行授权。如需重启后端，启动前明确设置 `PAPER_TRADING_SCHEDULER_ENABLED=false`、`PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false`、`RESEARCH_WORKER_ENABLED=false`。

在线美股请求默认间隔 12 秒（每分钟 5 次），仅在确认账户额度后通过 `--request-interval` 调整；复用缓存不发请求。预览会输出通过校验的可复用缓存数量，刷新每 100 个候选报告进度。

## 验证

运行 `PYTHONPATH=backend .venv/bin/python -m unittest backend.tests.test_stock_screening backend.tests.test_stock_baskets_api`，以及前端 `npm test`、`npm run lint` 和独立 `NEXT_DIST_DIR` 的生产构建。`make check-data CHECK_DATA_ARGS="--strict --json"` 保持只读。浏览器验收覆盖 5,555 只股票、说明聚焦 / 输入、搜索、分页、批量确认、取消和保存重开、键盘 / 手机布局及控制台错误。

独立浏览器验收可用仓库 Python 配合 `PYTHONPATH=backend` 运行 `backend/tests/serve_stock_basket_fixture.py`。在 `frontend` 运行 `NEXT_PUBLIC_API_BASE_URL=http://localhost:18080 NEXT_DIST_DIR=.next-codex-basket-qa npm run build`，再运行 `NEXT_DIST_DIR=.next-codex-basket-qa npx next start -p 3103`。测试服务仅使用一次性内存 SQLite 数据，不连接真实数据库，不含供应商调用、worker 或券商路由。验收后停止两个进程。
