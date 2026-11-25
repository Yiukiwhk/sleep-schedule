# 桌面版（Windows exe）改造指导

> 目标读者：接手这个改造的开发者/会话。本文是**可执行的任务清单**，每阶段带验收标准。
> 规格由 2026-11-25 的讨论确定：pywebview 壳、本地免注册账号、双向云同步、服务端合规校验、
> OCR 与文件两个导入接口、应用内更新提示、保留英文、旧版本兼容。

## 0. 一句话方案

同一个代码库，加一个 `SSP_MODE=desktop` 运行模式；桌面端用 **pywebview 套本地 uvicorn**，
数据落在本机，云端只承担同步与备份；视觉/文件识别全部在本机跑，只上传结构化结果。

## 1. 目标与非目标

**目标**

1. 双击即可用：不需要注册、不需要联网、不需要服务器。
2. 单机数据完整：记录、三餐打卡、建议引擎、日历、时间圆筒、i18n、主题全部可用。
3. 可选云同步：绑定账号后**双向**同步；未绑定时完全离线。
4. 两种导入：睡眠报告截图（OCR）、设备导出文件（直接解析）。
5. 应用内能看到新版本提示与更新说明。
6. 与现有服务端保持接口兼容，旧版本客户端不会被新服务端弄坏。

**非目标（明确不做）**

- 不做多用户切换界面（一台电脑 = 一个档案，见 §4）。若将来要家庭多档案，再开 `profiles` 表。
- 不做实时协作、不做云端计算建议（建议永远在本地/服务端各自算）。
- 不做自动静默更新（只提示 + 用户确认）。
- 不做代码签名（内测阶段），文档里写清 SmartScreen 提示。
- 不做摄像头实时识别（v1 只做截图/文件）。

## 2. 代码边界

现状：`backend/app/` 是一个整体 FastAPI 应用，`main.py` 里同时装配 API、静态资源、管理员入口。

改造后分三层，**不复制业务代码**：

| 层 | 位置 | 职责 | 依赖 |
|---|---|---|---|
| 核心 | `backend/app/`（不动） | 模型、schema、建议引擎、记录服务 | 纯 Python + SQLAlchemy |
| 服务端 | `backend/app/api/` | 路由、鉴权、同步、管理员 | FastAPI |
| 桌面壳 | `desktop/`（新增） | pywebview、进程管理、本地令牌、更新检查、导入器 | pywebview + 导入依赖 |

桌面壳**不重写业务**，只做三件事：拉起 uvicorn（子进程或线程）、开窗口、提供导入与更新。

新增文件：

```
desktop/
  __init__.py
  app.py              # 入口：解析参数、起服务、开窗口
  runtime.py          # 端口分配、健康检查、退出清理
  security.py         # 本地令牌、Origin 校验、DPAPI 存取
  updater.py          # 版本清单拉取与提示
  importers/
    __init__.py
    base.py           # ImportResult / ImportRecord 数据结构
    ocr.py            # 截图 → 结构化记录
    files.py          # JSON / CSV / PDF 解析
  requirements-desktop.txt
  build/
    app.spec          # PyInstaller onedir
    installer.iss     # Inno Setup
```

## 3. 功能矩阵

| 功能 | 服务端 | 桌面版 | 说明 |
|---|---|---|---|
| 登录 / 注册 | 有 | **无** | 本地用户自动创建，见 §4 |
| 管理员后台 | 有 | **无** | 路由与静态资源都不打包 |
| 记录 CRUD / 日历 / 圆筒 | 有 | 有 | 同一套前端 |
| 建议引擎 | 有 | 有 | 本地算，离线可用 |
| 三餐锚点 | 有 | 有 | 设置随同步走 |
| 写入冷却 | 10 秒 | **0** | 单人本地无并发 |
| 6 个月保留 | 有 | **不删**（`RETENTION_MONTHS=1200`） | 本地磁盘归用户 |
| 注销账号 | 有 | 换成「清空本地数据」 | 无账号可注销 |
| 云同步 | — | 有（可选） | §6 / §7 |
| 截图 OCR | — | 有 | §8 |
| 文件导入 | — | 有 | §8 |
| 更新提示 | — | 有 | §9 |

## 4. 本地身份与数据布局

**身份**：首次启动时在本地库创建一个用户（`id=1`，`username="本机"`，`is_admin=0`，
`password_hash` 写入一个随机不可用值，永远不走密码登录）。桌面模式启动时自动为该用户建会话，
前端 `/api/auth/me` 直接返回它，因此不出现登录页。

**数据目录**：默认 `%PROGRAMDATA%\YiukiSleep\`（机器级，符合「同一台电脑的不同 Windows 用户是一个档案」）：

```
%PROGRAMDATA%\YiukiSleep\
  data\app.db          # SQLite
  logs\app.log
  models\              # OCR/分期模型，按 manifest 下载
  config.json          # 端口、是否开机自启、更新通道
  device.json          # 绑定信息（不含 token 明文，token 走 DPAPI）
```

- 目录用继承 ACL 创建（`os.makedirs` + 默认安全描述符即可）；安装器不改成 Everyone 可写。
- 提供 `--per-user-data` 开关切到 `%LOCALAPPDATA%\YiukiSleep\`（多 Windows 用户各自独立）。
- 卸载默认**保留**数据；卸载页提供「同时删除我的数据」勾选。

**迁移**：本地库沿用服务端 `init_db()` 的迁移函数，版本号记在 `schema_meta` 表，
同步时上报 `schema_version`（见 §7 合规校验）。

## 5. 进程、窗口与本地安全

启动顺序：

1. 读 `config.json`，选端口（默认 0 = 系统分配，绑定 `127.0.0.1`）。
2. 生成一次性令牌 `LOCAL_TOKEN`（32 字节随机），作为环境变量传给 uvicorn。
3. 起 uvicorn（`--host 127.0.0.1 --port <port> --no-server-header`），等 `/healthz` 200。
4. `pywebview.create_window(url=f"http://127.0.0.1:{port}/?t={LOCAL_TOKEN}")`，窗口关闭即退出进程树。

安全三条（缺一不可）：

1. **只绑回环**，端口随机；令牌只出现在窗口 URL 的 query 里，前端读取后存 `sessionStorage` 并清理地址栏（`history.replaceState`）。
2. **中间件校验**：`/api/*` 请求必须带 `X-Local-Token`（与启动令牌一致），否则 403；HTML/静态资源不校验，避免登录前 403。
3. **Origin 校验**：`Origin` 必须是 `http://127.0.0.1:<port>` 或缺失（同源 GET），出现其它 Origin 直接 403——防本机其它网页 `fetch` 你的 API。

服务进程与窗口同生命周期：窗口 `on_closed` → 停 uvicorn → 退出；异常退出时用 `atexit` + 父进程看门狗兜底。

## 6. 云绑定

设置页新增「云同步」区块，三种状态：

| 状态 | 界面 | 行为 |
|---|---|---|
| 未绑定 | 「绑定云端账号」按钮 + 说明 | 纯本地 |
| 已绑定 | 显示账号、最后同步时间、「立即同步」「解除绑定」 | 双向同步 |
| 同步异常 | 顶部黄条 + 原因 + 「查看详情」 | 停止同步，不静默丢数据 |

**绑定流程（两种入口，第一次同步时弹选择框）**

```
绑定云端
 ├─ 选项 A：使用已有账号（服务器地址 + 用户名 + 密码）
 │    └─ 登录成功 → 提示「这台电脑已有数据，要上传合并到云端账号吗？」
 │          ├─ 上传合并（默认）：先推本地，再拉云端，按 §7 合并
 │          ├─ 只拉云端：本地数据先导出 JSON 备份，再以云端为准覆盖
 │          └─ 取消：保持未绑定
 └─ 选项 B：不绑定，新建云端账号（用户名 + 密码 + 同意条款勾选）
      └─ 服务端创建新账号并绑定本机 → 直接进入同步
```

- 设备令牌：登录后 `POST /api/sync/devices`（`{"name": "Windows 桌面", "fingerprint": "..."}`）返回一次性 `device_token`，
  仅存 DPAPI 加密后的密文；服务端只存哈希与最后使用时间，可吊销、可列「已绑定设备」。
- 解除绑定：删本地 token + 服务端标记 revoked；本地数据保留。
- 服务器地址在设置页可改（默认 `https://yiuki.me`），改地址视为重新绑定。

## 7. 同步协议

**同步范围（v1）**：`sleep_records`、`meal_checks`、`user_settings`（目标作息、三餐开关、锚点、语言、主题）。
**不同步**：`advice_plans`（各自重算）、日志、会话。

**行标识**：每行有 `client_row_id`（UUID v4，本机生成，永不变）+ `updated_at`（UTC ISO8601，毫秒）。
记录额外保留 `record_date` 作为业务唯一键；服务端 `(user_id, record_date)` 唯一。

**墓碑**：删除写 `deleted_at`，不物理删。拉取时墓碑照常返回，客户端据此删除本地行；
墓碑保留 180 天后由服务端清理（届时两端一致，无需再传）。

**游标**：服务端每用户维护 `sync_rev` 单调递增（`sync_log` 表：`rev, table, row_id, op, payload, changed_at`）。
`pull?cursor=` 返回 `rev > cursor` 的记录与墓碑，`has_more` 用于分页（每页 500）。

**冲突**：最后写入胜（LWW），比较 `updated_at`；相等时比较 `client_id` 字符串，大者胜（确定性）。
墓碑优先于更早的更新；墓碑与更新同一毫秒时，墓碑胜。

**接口**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/sync/devices` | 绑定设备，返回 `device_token`（仅一次） |
| DELETE | `/api/sync/devices/{id}` | 吊销 |
| GET | `/api/sync/status` | 返回 `{ok, server_version, min_client, schema_version, quota, violations[]}` |
| POST | `/api/sync/push` | `{cursor, rows:[{table, client_row_id, updated_at, deleted_at, payload}]}` → 逐行结果 + 新游标 |
| GET | `/api/sync/pull?cursor=` | 增量拉取 + `has_more` |
| POST | `/api/sync/account` | 选项 B 用：桌面端新建账号（限流 + `X-Client: desktop`） |

**合规性检查（每次同步前，服务端执行）**

技术性校验，不替代任何法律审查，UI 文案写「同步前检查」而不是「合规审查」：

1. 客户端版本 ≥ `min_supported`，否则 `sync.client_too_old`（附更新提示）。
2. `schema_version` 在服务端支持集合内，否则 `sync.schema_mismatch`。
3. 账号状态正常（未锁定、未在注销审核中、配额未超）。
4. 逐行数据校验：日期范围（今天-3650 ~ 今天+1）、`wake_date >= record_date`、
   `wake_date <= 今天`（与产品规则一致）、备注长度 ≤ 200、`meal_checks` 必须关联已存在的 `record_date`、
   单次 push ≤ 500 行、单行 payload ≤ 8 KB、未知表名拒绝。
5. 失败返回 `{ok:false, violations:[{code, table, client_row_id, message}]}`，
   客户端**整批不落地**，在「同步异常」里逐条展示，用户可修正或跳过该行。

**幂等**：同一批重复推送（网络重试）按 `client_row_id` 命中已有行，比较 `updated_at` 决定 applied/stale，不产生重复。

## 8. 导入接口

统一中间结构（`desktop/importers/base.py`）：

```python
@dataclass
class ImportRecord:
    date: date
    sleep_time: str | None      # "23:30"
    wake_time: str | None
    stages: dict | None         # {"deep": 96, "light": 210, "rem": 84, "awake": 12} 分钟
    confidence: float           # 0~1
    source: str                 # "ocr" | "file"

@dataclass
class ImportResult:
    records: list[ImportRecord]
    warnings: list[str]
    source_file: str
    parser_version: str
```

**确认页是硬要求**：任何导入都先进「预览 + 可编辑表格 + 逐条置信度」，用户确认后才写库；
低置信度行默认不勾选。原因是 OCR 必然错字符，直接入库会污染建议引擎的输入。

### 8.1 文件解析（先做这个）

按扩展名/魔数分派：

- `.json`：兼容两种——本应用导出格式（`{"records": [...]}`）与设备 App 的常见结构（有 `date`/`sleepTime`/`wakeTime` 键即尝试映射）。
- `.csv`：首行表头，内置列名映射（date/sleep/wake/deep/light/rem），允许用户在预览页手工改列名映射。
- `.pdf`：先取文本层（`pypdf`），正则抽日期与时间；无文本层则提示「扫描件请用截图识别」。
- 其它：报 `import.unsupported_format`。

### 8.2 截图 OCR（后做）

流水线（全部本地）：

1. 预处理：缩放、灰度、去噪（`opencv-python-headless`）。
2. 文字：`RapidOCR`（onnxruntime，模型 ~15–30 MB，中文够用），抽日期与时间。
3. 分期条：HSV 颜色聚类 → 找出深睡/浅睡/清醒色带 → 按横轴刻度换算成分钟数。
4. 组装 `ImportRecord`，`confidence` 由「文字匹配率 × 色带占比合理性」估算。

不做深度学习版面检测；等积累 200+ 真实截图再考虑训练检测模型替代规则。

## 9. 更新提示

- 清单地址：`https://yiuki.me/desktop/latest.json`（由站点仓库发布）：
  `{"version":"0.4.0","min_supported":"0.3.0","notes":"...","url":"https://github.com/Yiukiwhk/sleep-schedule/releases/tag/desktop-0.4.0","sha256":"...","size":123456789}`
- 启动时检查（可在设置里关闭），发现新版本 → 标题栏小圆点 + 设置页「有新版本 0.4.0」→ 点击看说明 → 「去下载」打开浏览器。
- 服务端 `min_supported` 与清单一致：低于该版本，同步返回 `sync.client_too_old`，客户端提示「先更新再同步」。
- 不做静默替换；升级 = 用户下载安装包覆盖安装，数据目录不动。

## 10. 打包与分发

- PyInstaller `--onedir`（启动快、杀软误报少），产物 `dist/YiukiSleep/`：`YiukiSleep.exe` + `_internal/`。
- Inno Setup 生成 `YiukiSleep-setup-x.y.z.exe`；安装到 `%ProgramFiles%\YiukiSleep`，数据在 `%PROGRAMDATA%\YiukiSleep`。
- 依赖拆分：`requirements-desktop.txt` 只加 `pywebview`、`opencv-python-headless`、`rapidocr-onnxruntime`、`pypdf`；
  **不引入 PyTorch**（体积 1.5 GB 起）。
- 体积目标：无 OCR ≈ 55–70 MB；含 OCR/OpenCV ≈ 150–190 MB。
- 构建脚本 `desktop/build/build.ps1`：清目录 → `pytest` → PyInstaller → Inno → 计算 sha256 → 输出到 `dist/desktop/`。

## 11. 测试策略

| 层 | 内容 |
|---|---|
| 单测 | 模式切换（`SSP_MODE=desktop` 时无 `/admin`、无登录路由）、LWW 合并、墓碑不复活、校验规则逐条 |
| 集成 | 两台“机器”（两个临时数据目录）互为对端，跑 push/pull 收敛；重复推送幂等 |
| 桌面自检 | `YiukiSleep.exe --smoke`：起服务 → `/healthz` 200 → `/api/auth/me` 返回本机用户 → 建一条记录 → 退出码 0 |
| 手工清单 | 首次启动、关机重启数据在、绑定/解除绑定、导入 JSON/CSV/截图、断网同步失败提示、更新提示、卸载保留数据 |

现有 8 个静态检查与 `accept_local.ps1` 继续跑服务端模式；桌面模式新增 `pytest -m desktop`。

## 12. 里程碑与验收

| 阶段 | 内容 | 验收标准 | 预估 |
|---|---|---|---|
| P0 模式开关 | `SSP_MODE`、本地用户、去 admin、冷却 0、保留关闭 | `SSP_MODE=desktop` 起服务：`/admin` 404、`/api/auth/me` 直接返回本机用户、写入无冷却；服务端模式行为不变（现有 424 项验收全过） | 2 天 |
| P1 壳与打包 | pywebview、端口/令牌、onedir 骨架、`--smoke` | 干净 Windows 虚拟机双击可用，数据写入 `%PROGRAMDATA%`，`--smoke` 退出码 0 | 3 天 |
| P2 同步 | 设备令牌、push/pull、LWW、墓碑、游标、绑定/新建账号 | 两台临时机器 50 条记录双向合并一致；重复推送不重复；删除不复活；断网有明确提示 | 5 天 |
| P3 校验与导入 | `/api/sync/status` 校验、文件解析、OCR、确认页 | 脏数据被逐行拒绝并显示原因；10 张真实截图导入后人工改 ≤2 处即可入库 | 5 天 |
| P4 发布 | 安装包、更新清单、文档、README | 全新机器安装→使用→升级→卸载保留数据，全流程走通 | 2 天 |

## 13. 风险与开放问题

| 风险 | 处理 |
|---|---|
| 机器级数据目录意味着同机其它 Windows 账户可读 | 文档写清；提供 `--per-user-data`；将来可加「启动时要求 Windows Hello」 |
| 服务端要提供「桌面端新建账号」接口，等于绕开注册关闭策略 | 该接口仅接受 `X-Client: desktop`，同一 IP 每小时 ≤ 3 个，创建后 24h 内不可同步；上线前评估是否要邀请码 |
| OCR 识别错误污染建议 | 强制确认页；低置信度默认不勾选；导入记录打 `source="ocr"` 便于回溯 |
| 同步冲突丢数据 | 每次同步前本地自动导出 JSON 快照到 `backup/`，保留最近 10 份 |
| 杀软误报 | onedir + 不做自解压；README 说明；后续可签名 |

**需要你确认的两点**（不影响 P0/P1 开工）：

1. 数据目录默认用机器级 `%PROGRAMDATA%`（同机多 Windows 用户共享一个档案）还是每用户独立？我按你说的「一个电脑的不同用户是一个账号」先做机器级。
2. 文件导入优先支持哪些格式？（默认 JSON / CSV / PDF 文本层；如果设备只导出 PDF 扫描件，就提前排 OCR）

## 14. 开工顺序（建议）

1. 先做 **P0**：它不需要任何新依赖，当天就能验证「桌面模式跑得起来、没有后台入口」。
2. 再做 **P1** 的壳与 `--smoke`，拿到一个能双击的 exe（暂不含 OCR），先把分发链路和 SmartScreen 反馈摸清。
3. 同步（P2）与导入（P3）可以并行，但**同步先于导入**——导入的确认页要复用同步的行级校验结果。

## 15. 实现状态

滚动更新，每完成一个里程碑补一段。

### P0 模式开关 —— 已完成（2026-11-25）

改动（只动后端 + 测试，前端未改；`/api/auth/me` 在桌面模式返回 200 就足以跳过登录页）：

- `backend/app/config.py`：新增 `MODE`、`DESKTOP`（`MODE == "desktop"`）、`DESKTOP_USERNAME`（默认 `本机`）；
  `WRITE_COOLDOWN_SECONDS` 在桌面模式默认 `0`，`RETENTION_MONTHS` 默认 `1200`（100 年＝不删）。
- `backend/app/desktop.py`（新增）：`ensure_local_user()` 幂等建/取本地档案——`username="本机"`、
  `is_admin=False`、随机不可用密码（`hash_password(secrets.token_urlsafe(32))`），并发撞唯一索引时重读；
  `local_user(db)` 供依赖注入使用。
- `backend/app/deps.py`：`optional_user` / `current_user` 在桌面模式直接返回本地档案，不查会话 cookie。
- `backend/app/api/__init__.py`：抽出 `build_api_router(*, include_admin=True)`，桌面模式整块不装配 `admin.router`。
- `backend/app/api/auth.py`：`_reject_in_desktop()` 把 `login` / `admin/login` / `logout` / `admin/logout` /
  `admin/parts` / `password` 六个入口在桌面模式按 404 处理；`/auth/me` 保留。
- `backend/app/main.py`：桌面模式不 `bootstrap_admin()`、不 mount `/admin`、SPA fallback 里 `admin*` 直接 404；
  `/` 直接 `FileResponse(frontend/user/index.html)`（不再 302，避免丢掉 P1 的 `?t=<本地令牌>`）；
  不起 6 小时周期清理任务（`task=None`）。
- `tests/test_desktop_mode.py`（新增）+ `pytest.ini`：因为 `settings` 在 import 时定死，测试用子进程探针跑
  `TestClient(create_app())`，分别断言 desktop / server 两种模式的页面、接口与错误码；`pytest -m desktop` 可单选。

验收（本机实跑）：

| 项 | 结果 |
|---|---|
| `pytest tests\test_advice_engine.py tests\test_desktop_mode.py -q -p no:cacheprovider` | 34 passed |
| `SSP_MODE=desktop` 起真实 uvicorn（`run.py --port 18012`） | `/` 200、`/user/` 200、`/admin/` 404、`/api/admin/stats` 404、`/api/auth/me` 200 且 `username=本机`/`is_admin=false`、`/api/records/meta` 冷却 0 / 保留 1200 月、连续两次写入 201/201（无 429）、`/api/auth/login` 与 `/api/auth/password` 404 |
| `deploy\accept_local.ps1`（服务端模式全量） | `local acceptance: ALL PASS`（e2e 74 项全过） |
| 8 个 `scripts/` 静态检查 | 全部 exit 0 |

### P1 桌面壳与打包 —— 已完成（2026-11-25）

后端：

- `backend/app/version.py`（新增）：`VERSION` / `MIN_SUPPORTED` / `SCHEMA_VERSION`，同步与更新检查的唯一版本来源。
- `backend/app/core/local_guard.py`（新增）：设了 `SSP_LOCAL_TOKEN` 才启用；`/api` 与 `/api/*` 缺 `X-Local-Token` 或值不符返回 403
  `error.forbidden`；带 `Origin` 时只接受 `http://127.0.0.1:<port>` 与 `http://localhost:<port>`。
- `backend/app/api/runtime_api.py`（新增）：`GET /api/runtime` 暴露 `{mode, desktop, version, min_supported, schema_version, retention_months, write_cooldown_seconds}`，供前端判断运行形态。
- `backend/app/api/desktop_api.py`（新增，仅桌面模式装配）：`GET /api/desktop/export` 导出 `{format:"yiuki-sleep-export", version:1, exported_at, settings, records, meal_checks}`；
  `POST /api/desktop/wipe` 清空本机记录/三餐/建议（保留本地档案）。
- `backend/app/api/__init__.py`：`build_api_router(*, include_admin=True, include_desktop=False)`；`backend/app/main.py` 在 `create_app()` 末尾
  `install_local_guard(app)`。
- `backend/app/__init__.py`（新增）：`app` 原本是 namespace package，PyInstaller 无法解析 `app.main`，补一个空 `__init__.py` 后 onedir 产物才带上
  `fastapi` / `uvicorn` / `app`。

前端：

- `frontend/shared/local-token.js`（新增）：把 `?t=<令牌>` 存进 `sessionStorage` 后 `history.replaceState` 清掉地址栏，`request()` 自动带 `X-Local-Token`。
- `frontend/shared/api.js`：新增 `runtimeApi`、`desktopApi`、`syncApi`。
- `frontend/user/js/main.js`：`bootstrap()` 先 `captureLocalToken()`，再并行拉 `/api/runtime`（`loadRuntime()` 不阻塞首屏，保住 boot-cache 的同步贴屏）；
  桌面模式隐藏「退出登录」并给 `<html>` 打 `data-mode="desktop"`。
- `frontend/user/js/pages/settings.js`：桌面模式隐藏修改密码与注销卡，换成「本机数据」（导出 JSON / 清空）与「云同步」卡。
- `frontend/shared/i18n.js`：新增 `error.forbidden` 与桌面版相关 10 个键（zh/en 各一份，保持 CRLF）。

桌面壳 `desktop/`：

- `runtime.py`：数据目录（默认 `%PROGRAMDATA%\YiukiSleep`，`--per-user-data` 切 `%LOCALAPPDATA%`）、`config.json` 读写、端口认领、一次性本地令牌、`/healthz` 等待、
  子进程环境变量拼装（frozen 时 `SSP_FRONTEND_DIR = sys._MEIPASS/frontend`）。
- `security.py`：DPAPI（`CryptProtectData` / `CryptUnprotectData`，固定 entropy）加解密的设备令牌存储。
- `app.py`：`--smoke` / `--serve-only` / `--port` / `--data-dir` / `--per-user-data` / `--version` / `--report`；`LocalServer` 用 uvicorn `Server` + 守护线程；
  窗口模式用 pywebview 打开 `http://127.0.0.1:<port>/?t=<令牌>`，窗口关闭即停服务。
- `requirements-desktop.txt`、`build/app.spec`、`build/build.ps1`：PyInstaller onedir +（可选）Inno Setup + `SHA256SUMS.txt`。

验收（本机实跑）：

| 项 | 结果 |
|---|---|
| `python desktop\app.py --smoke --report …` | 退出码 0；`/api/runtime` 200（desktop=true）、`/api/auth/me` 200（本机）、无令牌 403、建记录 201、`/admin/` 404 |
| `pytest tests\test_desktop_shell.py` | 1 passed |
| `pytest tests\test_advice_engine.py tests\test_desktop_mode.py tests\test_desktop_shell.py` | 35 passed |
| 8 个 `scripts/` 静态检查 + `node --check`（改动的 5 个前端模块） | 全部通过 |

### P2 云同步 —— 已完成（2026-11-25）

服务端：

- `backend/app/models.py`：`sleep_records` / `meal_checks` 增加 `client_row_id`、`sync_updated_at`、`sync_rev`、`deleted_at`；新增 `SyncDevice`、
  `SyncLog`（单调递增 `rev` 游标日志）、`SyncSettings`。
- `backend/app/db.py`：`_migrate_sync_columns()` 幂等补列/补索引，并给历史行回填 `client_row_id`（uuid4）与 `sync_updated_at`。
- `backend/app/services/sync_engine.py`（新增）：行校验、LWW 合并（`updated_at` 新者胜，墓碑优先；`tie` 只在墓碑时为 1）、`SyncLog` 追加、游标分页拉取、
  设备令牌生成/哈希、`status_payload`。
- `backend/app/api/sync_api.py`（新增）：`POST /api/sync/devices`、`DELETE /api/sync/devices/{id}`、`GET /api/sync/status`、`POST /api/sync/push`、
  `GET /api/sync/pull?cursor=`、`POST /api/sync/account`（仅 `X-Client: desktop`；同 IP 每小时 ≤ `SYNC_ACCOUNT_RATE_LIMIT`；新账号 24h 内不能同步——`SYNC_NEW_ACCOUNT_GRACE_HOURS` 可配）。
- 推送先整批校验，失败返回 `{ok:false, violations:[…]}` 且一行都不落库；成功返回逐行 `applied` / `stale` 与新游标。

桌面客户端：

- `desktop/sync.py`（新增）：绑定/解绑（设备令牌只存 DPAPI 密文）、脏行收集、分批 push、按游标 pull + LWW 落到本地库、每次同步前导出 JSON 快照（保留 10 份）。
  关键约束：push 响应里的游标是服务端位置，**不能**拿它推进本机 pull 游标；脏行必须带上本地 `updated_at`，否则会被服务端判成 `stale`。
- `backend/app/api/desktop_api.py`：`GET /api/desktop/sync/state`、`POST /api/desktop/sync/{bind,create,run,unbind}` 桥接（`SyncError` → 409 `sync.failed`）。
- `frontend/user/js/pages/settings.js`：「云同步」卡三态（未绑定 / 已绑定 / 同步异常），默认按「上传合并」绑定；解绑保留本机数据。
- i18n 新增 17 个同步键（zh/en）。

验收（本机实跑）：

| 项 | 结果 |
|---|---|
| `pytest tests\test_sync_protocol.py` | 1 passed（设备令牌、整批校验、幂等、墓碑、新账号宽限、限流） |
| `pytest tests\test_sync_convergence.py` | 5 passed（两台机器 50 条双向收敛、重复推送幂等、墓碑不复活、较新备注双向胜出、断网绑定报错、三餐随记录同步） |
| 全套 pytest（advice / desktop_mode / desktop_shell / sync_protocol / sync_convergence） | 41 passed |

### P3 校验与导入 —— 已完成（2026-11-25）

导入（本机解析，只把结构化结果发给后端）：

- `desktop/importers/base.py`（新增）：`ImportRecord` / `ImportResult` 数据结构，`PARSER_VERSION = "1.0"`，低置信度阈值 `LOW_CONFIDENCE = 0.7`。
- `desktop/importers/files.py`（新增）：`.json`（本应用导出格式、`records/data/items/rows` 包装、设备格式 `date/sleepTime/wakeTime`）、
  `.csv`（表头别名含中文，`deep/light/rem` 分期列）、`.pdf`（pypdf 文本层，抽不到文本时返回 `import.needs_screenshot`）。
  重复日期去重、坏行跳过并附 warning；编码按 `utf-8-sig` → `gb18030` 回退。
- `desktop/importers/ocr.py`（新增，可选依赖）：RapidOCR + opencv，只在装了 `desktop/requirements-ocr.txt` 时可用；
  未安装时接口返回 `import.ocr_unavailable`，界面上给同一句提示。**本轮未安装 OCR 依赖，也未打进安装包**（见 §12 P3 验收口径：截图 OCR 需另装可选依赖）。
- `backend/app/api/desktop_api.py`：`POST /api/desktop/import/parse`（multipart，≤10 MiB）、`POST /api/desktop/import/commit`
  （逐行复用记录校验：日期范围、起床日期、三餐日期、时间重叠；重复日期记 `import.already_exists`，其余错误进 `violations`，整批不因单行失败而回滚）。
- `frontend/user/js/pages/settings.js`：「导入记录」卡——选文件 → 解析预览（逐行日期/入睡/起床/备注可改、显示置信度、低置信度默认不勾）→ 只提交勾选行。
- `frontend/shared/api.js`：`upload()`（FormData + `X-Local-Token`）与 `importApi`。
- i18n 新增 32 个导入键（zh/en），`check_i18n_keys.py`：584 keys、zh 584 = en 584、missing 0。

同步前检查（服务端）：

- `GET /api/sync/status` 返回 `server_version` / `min_client` / `schema_version` / `quota` / `violations`；
  push 前的逐行校验错误码见 `backend/app/services/sync_engine.py`（`sync.bad_date`、`sync.wake_in_future`、`sync.meal_without_record` 等），
  失败时整批不落库，客户端逐条显示。客户端版本/ schema 低于要求时返回 `sync.client_too_old` / `sync.schema_mismatch`。

验收（本机实跑）：

| 项 | 结果 |
|---|---|
| `pytest tests\test_importers.py`（desktop\.venv） | 8 passed（导出格式、设备格式、CSV 分期、中文表头+BOM、坏行、PDF 无文本层、OCR 缺依赖） |
| `pytest tests\test_desktop_mode.py` | 2 passed（含导入 parse/commit、导出、清空、离线绑定 409） |
| 真实 UI 手工点一遍 | 桌面模式下设置页出现「本机数据 / 云同步 / 导入记录」三张卡；服务端模式退回密码卡+危险区 |

### P4 发布与更新 —— 已完成（2026-11-25）

- `desktop/build/installer.iss`（新增，Inno Setup 6）：装到 `{autopf}\YiukiSleep`，`PrivilegesRequired=admin`，
  卸载只删程序目录，**`%PROGRAMDATA%\YiukiSleep` 明确保留**。没有 ISCC 时 `build.ps1` 自动跳过，只产出 onedir + zip 前的目录。
- `desktop/build/build.ps1`：pytest（可 `-SkipTests`）→ PyInstaller（`desktop/build/app.spec`）→ 跑 `dist\desktop\YiukiSleep\YiukiSleep.exe --smoke --report` →
  `SHA256SUMS.txt` → `dist\desktop\latest.json`（`version` / `min_supported` / `notes` / `url` / `sha256` / `size`）。
  pytest 阶段优先用 `backend\.venv`（带 pytest + httpx），没有该 venv 时才退回构建 venv，所以构建 venv 只需运行时依赖。
- `backend/app/version.py` 的 `VERSION` 是版本唯一来源；`desktop/__init__.py` 与上一行保持一致，`build.ps1` 从后者读版本号。
- `desktop/updater.py`（新增）：读 `https://yiuki.me/desktop/latest.json`，`parse_version` / `is_newer` / `check()`；
  网络或格式失败只返回 `error` 不抛异常；非 https 下载地址与非法 sha256 进 `warnings`。
- `GET /api/desktop/update`（仅桌面模式）：返回 `{ok, current, latest, notes, url, sha256, size, min_supported, update_available, error, warnings}`。
- 设置页只在发现新版本时显示一行「发现新版本 x.y.z，可前往下载」的链接，**不自动下载、不静默替换**。
- `docs/DESKTOP.md` 的安装/升级/卸载手工清单见下；`README.md` 增加桌面版章节。

手工清单（未在干净虚拟机上执行，按顺序即可复现）：

1. 干净 Windows：装 `YiukiSleep-setup-0.1.0.exe` → 首次启动不出现登录页 → 设置页显示本机用户「本机」。
2. 建一条记录 → 关窗口 → 重新打开 → 记录仍在（数据在 `%PROGRAMDATA%\YiukiSleep\data\app.db`）。
3. 设置页绑定云端账号 → 立即同步 → 另一台机器绑定同一账号后能看到记录；删除一条后同步，另一台不复活。
4. 导入 JSON / CSV / PDF（无文本层的 PDF 会给「请改用截图」提示）。
5. 断网后点同步：提示 `网络不可用…`，本机数据不受影响。
6. 把 `latest.json` 的 version 改大后重启：设置页出现更新链接，点了只开浏览器。
7. 卸载：程序目录删除，`%PROGRAMDATA%\YiukiSleep` 保留；重装后数据还在。

验收（本机实跑）：

| 项 | 结果 |
|---|---|
| `pytest tests\test_updater.py`（desktop\.venv） | 5 passed（版本比较、清单解析、非 https/sha 告警、网络失败、缺 version） |
| `pytest tests\test_desktop_frozen.py`（desktop\.venv，需先 `build.ps1`） | 1 passed（**打包产物**：无令牌 403、建记录 201、关进程重启同数据目录后记录仍在、同步接口 200、multipart 导入 parse/commit 200） |
| `desktop\build\build.ps1 -SkipTests` | exit 0：bundle `--smoke` ok、`SHA256SUMS.txt` 与 `latest.json` 生成 |
| `deploy\accept_local.ps1`（服务端模式全量） | `local acceptance: ALL PASS` |

自动化小口子：`desktop/app.py` 正常每次启动随机生成本机令牌；测试或脚本可以用环境变量 `SSP_LOCAL_TOKEN` 固定它
（打包产物测试就是靠这个带令牌访问 `/api/*`），不设置时行为不变。

已知未做：代码签名、自动更新（按 §9 明确不做）、干净虚拟机上的安装/卸载手工验证、OCR 依赖未进安装包。
