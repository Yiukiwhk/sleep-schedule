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

**数据目录**：默认位置取决于运行方式——**安装版**（0.2.0 起程序在 `%ProgramFiles%\SleepSchedule`）用机器级
`%PROGRAMDATA%\SleepSchedule\`（符合「同一台电脑的不同 Windows 用户是一个档案」）；
**便携版**（green/onedir 目录放在任意可写位置）用 `%LOCALAPPDATA%\SleepSchedule\`。
0.2.2 及更早用 `YiukiSleep` 作目录名，0.2.3 起改成 `SleepSchedule`：首次启动时
`runtime.migrate_legacy_root()` 会把旧目录**复制**过来（旧目录保留不删，失败下次重试）。

```
<root>\
  data\app.db          # SQLite
  logs\app.log
  models\              # OCR/分期模型，按 manifest 下载
  config.json          # 端口、是否开机自启、更新通道
  device.json          # 绑定信息（不含 token 明文，token 走 DPAPI）
```

- 为什么便携版不能用机器级：`%PROGRAMDATA%` 下由普通用户新建的目录会继承 `C:\ProgramData` 的 ACL，
  `BUILTIN\Users` 只有 `ReadAndExecute`。第一次启动能靠「目录上的 create-file 权限」写出 `app.log`，
  之后每次重开都按文件自身 DACL 检查，直接 `[Errno 13] Permission denied`；`app.db` 同理。
  便携版没有安装器去授权，所以默认退到用户目录。
- **安装版**必须由安装器授权：`desktop/build/installer.iss` 的 `[Dirs]` 给
  `{commonappdata}\SleepSchedule`、`data`、`logs`、`backup`、`models` 都加 `Permissions: users-modify`，
  否则同机标准用户装完也写不进去（这是 0.1.0 的实测缺陷）。
- 开关：`--per-user-data` 强制用户级、`--machine-data` 强制机器级、`--data-dir PATH` 显式指定；
  三者互斥校验在 `runtime.app_root()`，`per_user and machine` 直接抛 `ValueError`。
- 启动时用 `runtime.probe_writable()`（创建目录 + 写删 `.write-probe`）先探一次可写性；
  失败弹窗说明三种出路（装安装包 / `--per-user-data` / `--data-dir`），退出码 2，而不是让 uvicorn 半路崩。
- 卸载默认**保留**数据；卸载只删 `{app}`，不碰 `<root>`。

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

**同步范围（v1）**：`sleep_records`、`meal_checks`、`user_settings`（目标作息、三餐开关、锚点、语言、主题）、
`advice_plans`（**只同步当前活跃方案**，见下）。
**不同步**：历史方案快照（各自留本机）、日志、会话。

**方案同步（0.1.2 起）**：`advice_plans` 与记录一样有 `client_row_id`/`sync_updated_at`/`sync_rev`。
每台机器只保留一份活跃方案；推送/拉取时把同用户其它活跃行自动置为非活跃。
绑定完成后桌面端拿 `GET /api/sync/plan`（方案）与 `GET /api/sync/settings`（目标作息）与本机比对：
任一不同，就在同步完成后弹**一个**窗口，问「使用云端方案 / 使用本机方案」——
选云端会忽略 LWW、把云端方案与云端目标作息（`user_settings` 行）一起强制落地
（`apply_pull_row(..., force=True)` 两次），因此不会出现"方案变了但目标作息没变"；
选本机则刷新本机方案与用户资料的时间戳，由下一次 push 把本机方案与目标作息一起覆盖云端。
只在两边都有方案且确有差异时提示；用户做完选择后 `plan_state.needs_choice` 归 false，不会反复弹。
不认识的表名在客户端直接跳过（`apply_pull_row` 返回 `ignored`），因此旧客户端拉到方案行不会报错。

**行标识**：每行有 `client_row_id`（UUID v4，本机生成，永不变）+ `updated_at`（UTC ISO8601，毫秒）。
记录额外保留 `record_date` 作为业务唯一键；服务端 `(user_id, record_date)` 唯一。

**墓碑**：删除写 `deleted_at`，不物理删。拉取时墓碑照常返回，客户端据此删除本地行；
墓碑保留 180 天后由服务端清理（`retention.purge_old_tombstones()`，覆盖 `sleep_records`、
`meal_checks`、`advice_plans`、`sync_settings`；届时两端一致，无需再传）。

**游标**：服务端每用户维护 `sync_rev` 单调递增（`sync_log` 表：`rev, table, row_id, op, payload, changed_at`）。
`pull?cursor=` 返回 `rev > cursor` 的记录与墓碑，`has_more` 用于分页（每页 500）。
`sync_log` 只保留最近 90 天（`SSP_SYNC_LOG_RETENTION_DAYS`，`retention.trim_sync_log()`）；
为了清理后 rev 不回退重号，`sync_log.rev` 用 `AUTOINCREMENT` 主键（老库由
`db._migrate_sync_log_autoincrement()` 原地重建表，保留全部行）。

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
| GET | `/api/sync/plan` | 云端当前活跃方案（绑定后让用户选本机/云端） |
| GET | `/api/sync/settings` | 云端设置行（目标作息、三餐开关），选云端方案时一并采用 |
| POST | `/api/sync/account` | 选项 B 用：桌面端新建账号（限流 + `X-Client: desktop`） |

**合规性检查（每次同步前，服务端执行）**

技术性校验，不替代任何法律审查，UI 文案写「同步前检查」而不是「合规审查」：

1. 客户端版本 ≥ `min_supported`，否则 `sync.client_too_old`（附更新提示）。
2. `schema_version` 在服务端支持集合内，否则 `sync.schema_mismatch`。
3. 账号状态正常（未锁定、未在注销审核中、配额未超）。
4. 逐行数据校验：日期范围（今天-3650 ~ 今天+1）、`wake_date >= record_date`、
   `wake_date <= 今天`（与产品规则一致）、备注长度 ≤ 200、`meal_checks` 必须关联已存在的 `record_date`、
   单次 push ≤ 500 行、单行 payload ≤ 64 KB（方案快照较大，0.1.2 起从 8 KB 放宽）、未知表名拒绝。
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

- PyInstaller `--onedir`（启动快、杀软误报少），产物 `dist/SleepSchedule/`：`SleepSchedule.exe` + `_internal/`。
- Inno Setup 生成 `SleepSchedule-setup-x.y.z.exe`；安装到 `%ProgramFiles%\SleepSchedule`，数据仍在 `%PROGRAMDATA%\YiukiSleep`（数据目录名不随改名变化）。
- 依赖拆分：`requirements-desktop.txt` = 运行时依赖 + `pywebview` + `pypdf`，并 `-r requirements-ocr.txt`
  （`opencv-python-headless`、`numpy`、`onnxruntime`、`rapidocr-onnxruntime` + 其 yaml/shapely/pyclipper/six 依赖）；
  **不引入 PyTorch**（体积 1.5 GB 起）。删掉那一行 `-r` 即可构建无 OCR 的小包。
- 实测体积（0.1.0）：无 OCR onedir 43.1 MB / 安装包 19.6 MB；含 OCR onedir 207.0 MB / 安装包 68.4 MB
  （`cv2.pyd` 67.5 MB、`onnxruntime` 33 MB、模型 15.5 MB；已剔掉用不到的 `opencv_videoio_ffmpeg` 27 MB）。
- 构建脚本 `desktop/build/build.ps1`：清目录 → `pytest` → PyInstaller → Inno → 计算 sha256 → 输出到 `dist/desktop/`。
  `app.spec` 用 `importlib.util.find_spec("rapidocr_onnxruntime")` 决定是否把 OCR 打进包，所以两种构建共用一份 spec。

## 11. 测试策略

| 层 | 内容 |
|---|---|
| 单测 | 模式切换（`SSP_MODE=desktop` 时无 `/admin`、无登录路由）、LWW 合并、墓碑不复活、校验规则逐条 |
| 集成 | 两台“机器”（两个临时数据目录）互为对端，跑 push/pull 收敛；重复推送幂等 |
| 桌面自检 | `SleepSchedule.exe --smoke`：起服务 → `/healthz` 200 → `/api/auth/me` 返回本机用户 → 建一条记录 → 退出码 0 |
| 手工清单 | 首次启动、关机重启数据在、绑定/解除绑定、导入 JSON/CSV/截图、断网同步失败提示、更新提示、卸载保留数据 |
| 干净虚拟机 | 见下方「虚拟机验收流程」：快照 → 装 0.1.1 → 升级到 0.1.2 → 卸载保留数据 → 回滚快照 |

现有 8 个静态检查与 `accept_local.ps1` 继续跑服务端模式；桌面模式新增 `pytest -m desktop`。

### 虚拟机验收流程（30 分钟）

本机没有管理员权限、也不能真装一次安装包，所以「全新机器」这条只能在虚拟机里走。宿主机任选
VirtualBox（免费）/ VMware Workstation Player（个人免费）/ Hyper-V（Windows 专业版自带）。

1. **准备**：装 Windows 10/11 虚拟机 → 装 VMware/VirtualBox 增强工具（否则剪贴板/共享文件夹不可用）→
   打一个干净快照 `clean`。把 `SleepSchedule-setup-<版本>.exe` 用共享文件夹或拖拽放进虚拟机桌面。
2. **安装**（验证 P4 安装路径与 ACL）：双击安装包 → 默认装到 `%ProgramFiles%\SleepSchedule` →
   勾选/取消桌面图标各试一次 → 安装完成后自动启动。首次启动应直接进主界面（无登录页），
   创建一条昨天的记录，确认写入不报错。
3. **数据位置**：`Win+R` 输入 `%PROGRAMDATA%\SleepSchedule` → 应看到 `data\app.db`、`logs\app.log`；
   用 `icacls` 或属性→安全 确认 `Users` 有「修改」权限（安装器 `[Dirs] users-modify` 生效）。
4. **升级**（验证 P4 更新提示）：把 `%PROGRAMDATA%\SleepSchedule\config.json` 里 `check_updates` 保持 true （默认），
   用上一版安装包装一次并启动旧版 → 设置页应出现「发现新版本 x.y.z」；点链接应打开浏览器下载页。
   然后用新版安装包覆盖安装 → 数据仍在（记录条数不变）。
5. **同步**：设置→云同步 → 服务器留空 → 用测试账号登录并绑定 → 点「立即同步」→ 记录条数应与云端一致；
   若云端与本机都有方案，应弹出「使用本机方案 / 使用云端方案」二选一；选一个后方案卡片目标时间随之改变。
6. **卸载**：设置→应用→卸载 → 重新打开 `%PROGRAMDATA%\SleepSchedule`，**数据目录必须还在**；
   再装一次，记录仍在（卸载不删数据是设计）。
7. **回滚**：删除虚拟机或恢复 `clean` 快照，避免把测试账号/证书留在机器上。

验收记录建议：每条截图 + 安装包版本 + 日期，贴进 issue 或本文件下方「版本记录」。

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
| SmartScreen 首次运行警告 | 没签名时用户会看到「Windows 已保护你的电脑」，点「更多信息 → 仍要运行」即可；签名见下 |

### 代码签名：现价与开源免费方案

| 方案 | 参考价格 | 适合谁 | 说明 |
|---|---|---|---|
| 自签名（自建根证书） | 0 | 只有自己用 | 只在你亲手导入根证书的机器上能消警告，别人下载仍红 |
| Certum Open Source Code Signing | 约 €80–150/年 | 开源项目 | 需公开仓库；证书走 SimplySign 云签名 |
| Azure Trusted Signing | $9.99/月（约 ¥850/年） | 有 3 年历史的组织 | 需公司/组织实体与 3 年可查历史，个人开发者一般过不了 |
| 普通 OV 代码签名（Sectigo/DigiCert/GlobalSign 或国内代理） | ¥1,500–3,500/年（个人）／¥2,500–5,000/年（企业） | 个人/小团队 | 2023 起 CA/B 要求硬件令牌或云 HSM，不能只用 pfx 文件 |
| EV 代码签名 | ¥4,000–8,000/年 | 需要尽快建立信誉 | 硬件令牌，SmartScreen 信誉建立更快，但对本项目属过度投入 |
| SignPath.io 社区版 | 0 | **公开仓库 + OSI 许可** | 免费给开源项目签名（Windows 安装包/exe），但必须由它的 CI 集成构建，且有审核 |

结论（给本项目的建议）：**先不买证书**。当前是个人自用/小范围分发，警告可绕过；如果之后要公开发布给陌生人用，
最合适的是 `SignPath.io`（前提：仓库开源，例如 MIT/Apache-2.0），否则退而求其次选 Certum 的开源/个人 OV（约 ¥700–1,200/年）。
证书到手后只需在 `desktop/build/build.ps1` 里加一步 `signtool sign /fd SHA256 /tr <时间戳> /td SHA256 <exe>`，
并在安装包的 `[Run]` 之后对 setup 也签一次，其余流程不变。

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
- `app.py`：`--smoke` / `--serve-only` / `--check-gui` / `--port` / `--data-dir` / `--per-user-data` / `--version` / `--report`；`LocalServer` 用 uvicorn `Server` + 守护线程；
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
  **例外**：建号后 1 小时内、从**建号时的同一个 IP** 发起的同步直接放行——这是「新建账号后立刻绑定并同步」的正常路径；
  换 IP 或超过 1 小时仍按 24h 宽限处理（`_grace_violation(user, db, request)` 比对 `SystemLog.ip`）。
- 服务器地址（桌面端）：输入框**留空**表示官方 `https://yiuki.me`；自建服务器填自己的地址，允许 `http://`（内网）、端口和子路径
  （例如 `https://example.com/sleep`）。`desktop/sync.py` 的 `normalize_server()` 负责补 `https://`、去尾部斜杠、拒绝非 http(s) 协议；
  绑定阶段区分「登录 401」「服务器上没有同步接口」「服务器版本太旧没有设备令牌接口」，错误直接显示在设置页的同步卡上。

- 推送先整批校验，失败返回 `{ok:false, violations:[…]}` 且一行都不落库；成功返回逐行 `applied` / `stale` 与新游标。
- **方案也同步**（2026-11-26 追加）：`advice_plans` 只传当前活跃方案（历史快照留本机）；`sync_engine.plan_payload()` /
  `active_plan_row()`、`GET /api/sync/plan`；单行 payload 上限从 8 KB 放宽到 64 KB。绑定后设置页对比本机与云端的
  方案**和目标作息**（`GET /api/sync/settings` + `sync_engine.active_settings_row()`），任一不同就弹一个窗：
  「使用云端方案」= 方案与目标作息一起强制落地（两次 `force=True`），「使用本机方案」= 刷新本机时间戳后 push 覆盖云端。
  选择结果经 `POST /api/desktop/sync/plan` 落地。旧客户端不认识 `advice_plans` 行时 `apply_pull_row()` 返回 `ignored`
  直接跳过，所以不用改 `SCHEMA_VERSION`。
  同时修了一个服务端缺陷：push 上来的 `user_settings` 过去只写进 `SyncSettings.payload`，没有写回 `User` 行，
  导致「云端目标作息」永远是旧值；现在 `apply_rows()` 调 `_apply_settings_to_user()`，用**写会话里**的 User 实例落地。
- **网页侧的写入也要能被拉到**（2026-11-26 修复）：同步上线前就存在的行 `sync_rev` 全是 0，而 pull 只返回 `rev > cursor`，
  于是新建的桌面端会看到「pushed 0 / pulled 0」——记录明明在库里。现在：
  - `sync_engine.backfill_missing()` 在启动时（`main.py` 的 lifespan，`init_db()` 之后）给 `sync_rev = 0` 的行补 rev、
    给每个还没有 `SyncSettings` 的用户补一行设置；幂等，日志打印 `sync backfill: {...}`。
  - `sync_engine.stamp_row()` / `touch_settings()` 由网页写入路径调用：新增/编辑记录（`api/records.py`）、
    三餐打卡（`services/advice_plan.py`）、修改设置（`api/settings_api.py`）都会推进 rev 并写 `SyncLog`。
  - 删除改成**墓碑**（`deleted_at`），不再物理删除，这样别的设备 pull 得到「这条没了」；列表/详情/重叠校验/建议引擎/导出
    全部按 `deleted_at IS NULL` 过滤。物理删除只剩保留期清理（`services/retention.py`）。
- 发布：`deploy/apply_fix.ps1` 的 `$backendFiles` 与 `deploy/remote_apply_fix.sh` 的 `BACKEND_FILES` 已包含上表新增模块
  （`api/__init__.py`、`api/sync_api.py`、`api/runtime_api.py`、`api/desktop_api.py`、`version.py`、`core/local_guard.py`、
  `app/desktop.py`、`deps.py`、`main.py`、`services/sync_engine.py`）；上线后远端自证新增 4am 段（`/api/runtime` 200、
  `/api/sync/status` 401、`POST /api/sync/account` 无 `X-Client` 403，以及 sync_engine/db/main 的落地 grep）。
  注意 `api/__init__.py` 对 `desktop_api` 是延迟导入：服务端 venv 没有 `python-multipart`，顶层导入会让服务端起不来。

桌面客户端：

- `desktop/sync.py`（新增）：绑定/解绑（设备令牌只存 DPAPI 密文）、脏行收集、分批 push、按游标 pull + LWW 落到本地库、每次同步前导出 JSON 快照（保留 10 份）。
  关键约束：push 响应里的游标是服务端位置，**不能**拿它推进本机 pull 游标；脏行必须带上本地 `updated_at`，否则会被服务端判成 `stale`。
- `backend/app/api/desktop_api.py`：`GET /api/desktop/sync/state`、`POST /api/desktop/sync/{bind,create,run,unbind}` 桥接（`SyncError` → 409 `sync.failed`）；
  `GET /api/desktop/sync/plan` 拉两边方案对比（`plan_state`），`POST /api/desktop/sync/plan` 提交 `{"choice":"local"|"cloud"}`。
- `frontend/user/js/pages/settings.js`：「云同步」卡三态（未绑定 / 已绑定 / 同步异常），默认按「上传合并」绑定；解绑保留本机数据。
- i18n 新增 17 个同步键（zh/en）。

验收（本机实跑）：

| 项 | 结果 |
|---|---|
| `pytest tests\test_sync_protocol.py` | 1 passed（设备令牌、整批校验、幂等、墓碑、新账号宽限、限流） |
| `pytest tests\test_sync_convergence.py` | 6 passed（两台机器 50 条双向收敛、重复推送幂等、墓碑不复活、较新备注双向胜出、断网绑定报错、三餐随记录同步、方案同步与二选一） |
| 全套 pytest（advice / desktop_mode / desktop_shell / sync_protocol / sync_convergence） | 41 passed |

### P3 校验与导入 —— 已完成（2026-11-25）

导入（本机解析，只把结构化结果发给后端）：

- `desktop/importers/base.py`（新增）：`ImportRecord` / `ImportResult` 数据结构，`PARSER_VERSION = "1.0"`，低置信度阈值 `LOW_CONFIDENCE = 0.7`。
- `desktop/importers/files.py`（新增）：`.json`（本应用导出格式、`records/data/items/rows` 包装、设备格式 `date/sleepTime/wakeTime`）、
  `.csv`（表头别名含中文，`deep/light/rem` 分期列）、`.pdf`（pypdf 文本层，抽不到文本时返回 `import.needs_screenshot`）。
  重复日期去重、坏行跳过并附 warning；编码按 `utf-8-sig` → `gb18030` 回退。
- `desktop/importers/ocr.py`（新增，可选依赖）：RapidOCR + opencv，依赖见 `desktop/requirements-ocr.txt`；
  0.1.0 的安装包**已包含**这套依赖，`parse` 响应里的 `ocr_available` 为 `true`；构建机上没有这些包时会自动产出无 OCR 的小包，
  此时接口返回 `import.ocr_unavailable`。只做「缩放 720 宽 → 找日期/时间行 → 配对」，不做版面检测，识别结果一律进确认页。
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
| `pytest tests\test_importers.py`（desktop\.venv） | 8 passed + 1 skipped（导出格式、设备格式、CSV 分期、中文表头+BOM、坏行、PDF 无文本层、**合成截图 OCR 识别**；缺依赖降级用例在有依赖时按设计跳过） |
| `pytest tests\test_desktop_mode.py` | 2 passed（含导入 parse/commit、导出、清空、离线绑定 409） |
| 真实 UI 手工点一遍 | 桌面模式下设置页出现「本机数据 / 云同步 / 导入记录」三张卡；服务端模式退回密码卡+危险区 |

### P4 发布与更新 —— 已完成（2026-11-25）

- `desktop/build/installer.iss`（新增，Inno Setup 6）：装到 `{autopf}\SleepSchedule`，`PrivilegesRequired=admin`，
  卸载只删程序目录，**`%PROGRAMDATA%\YiukiSleep` 明确保留**。没有 ISCC 时 `build.ps1` 自动跳过，只产出 onedir。
  `ArchitecturesAllowed` 兼容 6.3 之前：`#if Ver >= 0x06030000` 用 `x64compatible`，否则退回 `x64`。
- ISCC 查找顺序：`-Iscc` 参数 → `desktop\build\innosetup\ISCC.exe`（**本机用的便携方式**）→ 三个标准安装目录。
  没有管理员权限安装 Inno Setup 时，可以用 `innoextract` 把官方安装包解到 `desktop\build\innosetup\`（该目录已 gitignore）：

  ```powershell
  # 一次性准备（不需要管理员）
  innoextract -d desktop\build\innosetup -e innosetup-6.0.5.exe
  ```

- `desktop/build/build.ps1`：pytest（可 `-SkipTests`）→ PyInstaller（`desktop/build/app.spec`）→ 跑 `dist\desktop\SleepSchedule\SleepSchedule.exe --smoke --report` →
  `SHA256SUMS.txt` → Inno 编译安装包 → `dist\desktop\latest.json`（`version` / `min_supported` / `notes` / `url` / `sha256` / `size`）。
  `latest.json` 优先指向 `SleepSchedule-setup-*.exe`；没有安装包时退回 `SleepSchedule.exe`。
  pytest 阶段优先用 `backend\.venv`（带 pytest + httpx），没有该 venv 时才退回构建 venv，所以构建 venv 只需运行时依赖。
- `backend/app/version.py` 的 `VERSION` 是版本唯一来源；`desktop/__init__.py` 与上一行保持一致，`build.ps1` 从后者读版本号。
- `desktop/updater.py`（新增）：读 `https://yiuki.me/desktop/latest.json`，`parse_version` / `is_newer` / `check()`；
  网络或格式失败只返回 `error` 不抛异常；非 https 下载地址与非法 sha256 进 `warnings`。
- `GET /api/desktop/update`（仅桌面模式）：返回 `{ok, current, latest, notes, url, sha256, size, min_supported, update_available, error, warnings}`。
- 设置页只在发现新版本时显示一行「发现新版本 x.y.z，可前往下载」的链接，**不自动下载、不静默替换**。
- 发布：`deploy/publish_desktop.ps1` 把 `dist\desktop\SleepSchedule-setup-*.exe` 与 `latest.json` 传到
  `/var/www/sleep-schedule/desktop/`（`chown www-data`、644），随后用公网 URL 复核清单与安装包都是 200；
  `-VerifyOnly` 只查不发。vhost 新增 `location ^~ /desktop/`（`try_files $uri =404`，缺文件必须 404，
  否则更新检查会拿到 SPA 首页 HTML 当成「清单格式错误」），远端自证新增 4an 段验证该 location 与 404 行为。
- `docs/DESKTOP.md` 的安装/升级/卸载手工清单见下；`README.md` 增加桌面版章节。

手工清单（未在干净虚拟机上执行，按顺序即可复现）：

1. 干净 Windows：装 `YiukiSleep-setup-0.1.0.exe` → 首次启动不出现登录页 → 设置页显示本机用户「本机」。
2. 建一条记录 → 关窗口 → 重新打开 → 记录仍在（安装版数据在 `%PROGRAMDATA%\YiukiSleep\data\app.db`，便携版在 `%LOCALAPPDATA%\YiukiSleep\data\app.db`）。
3. 设置页绑定云端账号 → 立即同步 → 另一台机器绑定同一账号后能看到记录；删除一条后同步，另一台不复活。
4. 导入 JSON / CSV / PDF（无文本层的 PDF 会给「请改用截图」提示）。
5. 断网后点同步：提示 `网络不可用…`，本机数据不受影响。
6. 把 `latest.json` 的 version 改大后重启：设置页出现更新链接，点了只开浏览器。
7. 卸载：程序目录删除，`%PROGRAMDATA%\YiukiSleep` 保留；重装后数据还在。

验收（本机实跑）：

| 项 | 结果 |
|---|---|
| `pytest tests\test_updater.py`（desktop\.venv） | 5 passed（版本比较、清单解析、非 https/sha 告警、网络失败、缺 version） |
| `pytest tests\test_desktop_frozen.py`（desktop\.venv，需先 `build.ps1`） | 1 passed / 1 skipped / 7.90s（**打包产物**：无令牌 403、建记录 201、重启后记录仍在、同步接口 200、multipart 导入 parse/commit 200、截图 OCR 返回 `source="ocr"`；第二条 `--smoke` + `--check-gui` 在本仓库路径下按环境限制 skip，见「已知限制」） |
| `pytest tests\test_desktop_runtime.py`（desktop\.venv） | 6 passed（安装版→ProgramData、便携版→LocalAppData、三个开关胜负关系、`per_user+machine` 抛错、不可写目录探测、窗口 URL 单斜杠） |
| 把新 onedir 装到 `%LOCALAPPDATA%\Programs\SleepSchedule` 后**在沙箱外**启动 | `--serve-only`：`db=C:\Users\…\AppData\Local\YiukiSleep\data\app.db`，`/healthz` 200、`/` 200、无令牌 `/api/runtime` 403；`--check-gui`：`{"ok": true, "backend": "winforms", "dotnet": "netfx"}` |
| `desktop\build\build.ps1`（含 pytest + PyInstaller + Inno） | exit 0：`bundled --smoke ok`、`YiukiSleep-setup-0.1.0.exe`（68.7 MB）、`SHA256SUMS.txt` 与 `latest.json` 生成 |
| 安装包内容抽查（`innoextract -l`） | `app\YiukiSleep.exe` 13.2 MiB + `app\_internal\…` 完整 |
| 把 onedir 复制到 `C:\Temp\ys-final` 后 `YiukiSleep.exe --check-gui` | `{"ok": true, "backend": "winforms", "dotnet": "netfx"}`，退出码 0（证明窗口后端在普通目录可用） |
| `deploy\accept_local.ps1`（服务端模式全量） | `local acceptance: ALL PASS` |

窗口后端自检（0.1.0 发布后的补充）：`desktop/app.py` 增加 `--check-gui`（只加载 pywebview + pythonnet + .NET，不开窗口，可配 `--report` 落盘），
`--smoke` 的报告新增 `"cffi": true/false`。原因是 0.1.0 首次发布时漏了 `cffi`：`clr-loader` 当初用 `--no-deps` 安装，
`clr_loader/ffi/__init__.py` 里 `import cffi` 在打包时被 PyInstaller 忽略，双击窗口直接崩在 .NET 初始化。
现在 `app.spec` 显式 hiddenimport `cffi` / `_cffi_backend`，`tests\test_desktop_frozen.py` 用打包产物断言 `smoke["cffi"] is True`。

窗口地址缺陷（0.1.0 发布后修复）：窗口 URL 原先用 `f"{build_url(port)}/?t={token}"` 拼接，而 `build_url()` 已经带结尾斜杠，
实际打开的是 `http://127.0.0.1:<port>//?t=…`——`//` 是另一个路径，被 SPA fallback 当成未知路由，窗口里只显示
`{"detail":"error.not_found","code":"error.not_found"}`。现在统一走 `runtime.build_window_url(port, token)`
（单斜杠 + `quote()`），`--smoke` 会把这条 URL 真请求一次并断言 200 且返回 HTML，
`tests\test_desktop_runtime.py` 里也钉住了 `http://127.0.0.1:<port>/?t=…` 的精确形状。

数据目录缺陷（0.1.0 发布后修复）：便携版原先无条件用 `%PROGRAMDATA%\YiukiSleep`，而该目录由普通用户创建时继承
`C:\ProgramData` 的 ACL（`BUILTIN\Users` 只有 `ReadAndExecute`），第一次能建出 `app.log`，第二次启动就
`[Errno 13] Permission denied: 'C:\ProgramData\YiukiSleep\logs\app.log'`。修复分两半：

- 便携版默认 `%LOCALAPPDATA%\YiukiSleep`（`runtime.default_root_mode()` 按 exe 是否在 Program Files 判断），
  安装版仍用机器级；新增 `--machine-data` 强制机器级、`--per-user-data` 强制用户级、`--data-dir` 覆盖；
  `runtime.app_root()` 拒绝 `per_user + machine` 同时给。
- 安装包用 `[Dirs] … Permissions: users-modify` 给 `{commonappdata}\YiukiSleep\{data,logs,backup,models}` 授权，
  这样安装版的多用户共享才真正可写。
- 启动前 `runtime.probe_writable()` 探一次可写性，失败弹窗给出三种出路并返回 2；`tests\test_desktop_runtime.py` 锁住这套选择逻辑。

自动化小口子：`desktop/app.py` 正常每次启动随机生成本机令牌；测试或脚本可以用环境变量 `SSP_LOCAL_TOKEN` 固定它
（打包产物测试就是靠这个带令牌访问 `/api/*`），不设置时行为不变。

已知限制（环境相关，不是产品缺陷）：

- **不要从被 Windows 判为「网络位置」的目录直接运行**：.NET Framework 拒绝从网络区域 `LoadFrom` 托管 DLL，
  报 `Failed to resolve Python.Runtime.Loader.Initialize`（HRESULT `0x80131515`，`LoadFile` 会提示需要 `loadFromRemoteSources`）。
  本机 DSH 沙箱把会话工作区（`…\default-workspace\…`）呈现成网络区域，所以从仓库目录双击 onedir 会失败；
  同一份产物复制到 `C:\Temp\ys-final` 后 `--check-gui` 正常。交付时必须装安装包（0.2.0 起为 `%PROGRAMFILES%\SleepSchedule`），
  或把整个 `SleepSchedule\` 目录移到 `%LOCALAPPDATA%\Programs\SleepSchedule` 之类的普通目录。
  程序在这种情况下会弹「could not load the window backend」并附上这条指引，而不是静默退出。
- 安装包需要在干净虚拟机上手工走一遍安装/升级/卸载（见上一节清单），本机无管理员权限、未做。
- **不要从编码代理的沙箱里启动**（例如在聊天里点文件卡片打开 exe）：这类进程只能写自己的工作区，
  连 `%LOCALAPPDATA%\YiukiSleep` 的 `mkdir`/写文件都会被拒（WinError 5），程序会弹「could not prepare the data folder」。
  双击桌面快捷方式、开始菜单或资源管理器启动不受影响（本机已用 WMI 在沙箱外启动同一份产物验证通过）。

已知未做：代码签名（安装包与 exe 均未签名）、自动更新（按 §9 明确不做）、干净虚拟机上的安装/卸载手工验证。
`latest.json` 与安装包由 `deploy/publish_desktop.ps1` 发布到 `https://yiuki.me/desktop/`（vhost 的 `/desktop/` location 已上线，
缺失文件回 404 而不是 SPA 首页）。

## 16. 版本记录

### 0.1.1（2026-11-26）

- 修「同步成功但数量都是 0」：同步上线前的历史行 `sync_rev` 为 0，而 pull 只返回 `rev > cursor`，所以新设备什么都拉不到。
  现在启动时 `services/sync_engine.backfill_missing()` 给这些行补 rev（幂等，日志 `sync backfill: {...}`）；
  网页侧的新增/编辑/删除也会推进 rev —— `api/records.py`（创建、编辑、软删除）、`services/advice_plan.py`（三餐打卡）、
  `api/settings_api.py`（设置）都调用 `stamp_row()` / `touch_settings()` 并写 `SyncLog`。
- 删除改为墓碑（`deleted_at`），列表、分页计数、详情、重叠校验、建议引擎、`/api/desktop/export` 全部按 `deleted_at IS NULL` 过滤；
  物理删除只留给保留期清理（`services/retention.py`）。这样别的设备 pull 得到「这条没了」，而不是凭空消失。
- 版本号 0.1.0 → 0.1.1（`desktop/__init__.py`、`backend/app/version.py`）；`MIN_SUPPORTED` 仍为 0.1.0，
  已装的 0.1.0 不会被拒绝，只会在「发现新版本」提示里看到 0.1.1。
- 线上部署：`deploy/apply_fix.ps1` → `fix applied: OK`；生产库回填后 `sleep_records 52/52 rev>0`、`meal_checks 22/22 rev>0`、
  `sync_log` 84 行（其中 40 条属于 tyq）。
- 产物：`YiukiSleep-setup-0.1.1.exe`（71,986,402 字节，sha256 `42b79bb2902ecffd2bc135db27b4908f02800a801430474ef6b961462d40323e`），
  `dist\desktop\latest.json` 已由 `deploy/publish_desktop.ps1` 发布到 `https://yiuki.me/desktop/`。

### 0.1.2（2026-11-26）

- **方案也进同步**：`advice_plans` 只传当前活跃方案（历史快照留本机）；`sync_engine.plan_payload()` / `active_plan_row()`、
  `GET /api/sync/plan`；单行 payload 上限 8 KB → 64 KB。绑定后若本机与云端都有方案且开始日期/目标时间不同，
  设置页弹「使用本机方案 / 使用云端方案」：选云端用 `apply_pull_row(..., force=True)` 忽略 LWW 强落地，
  选本机则刷新时间戳后 push 覆盖云端。旧客户端不认识 `advice_plans` 行时 `apply_pull_row()` 返回 `ignored` 跳过，
  所以 `SCHEMA_VERSION` 不变、`MIN_SUPPORTED` 仍是 0.1.0。
- 版本号 0.1.1 → 0.1.2（`desktop/__init__.py`、`backend/app/version.py`）；已装的 0.1.1 会在「发现新版本」里看到提示。
- 线上部署 `fix applied: OK`（4am 段新增 `stamp_row` / `backfill_missing` / `active_plan_row` / `@router.get("/plan")` 四条 grep）。
  生产库探针：`advice_plans` 19 列含 `client_row_id/sync_updated_at/sync_rev/deleted_at`，66 行中 5 条活跃方案全部 `rev > 0`
  （user 2/3/4/7/9 各 1 条），`sync_log` 89 行、其中 `table_name='advice_plans'` 5 行。
- 桌面端设置页同步卡：绑定后自动比对两边方案，冲突时显示二选一；选择结果经 `POST /api/desktop/sync/plan` 落地并自动再同步一次。
- 测试：`test_sync_convergence` 新增「方案同步与二选一」用例（A 建方案 → B 本地另有方案 → 绑定时 needs_choice →
  选云端生效 → 新机器 C 拉到同一方案 → B 改成本地方案后选「使用本机」覆盖云端），6 passed；desktop 套件 30 passed / 2 skipped。
- 产物：`YiukiSleep-setup-0.1.2.exe`（72,003,864 字节，sha256
  `a2c0a8b94eefc1741edec4750a5e659e64b6a2a59ab6d08681476947178ee5e1`），`latest.json` 已发布（HTTP 200，指向 0.1.2 安装包）。

### 0.1.3（2026-11-26）

- **一个弹窗同时管方案与目标作息**：云同步完成后，只要「方案」或「目标作息」有一边不同，就弹一个窗口问
  「使用云端方案 / 使用本机方案」。选云端 = 方案与目标作息（`user_settings` 行）都强制落地云端
  （`apply_pull_row(..., force=True)` 两次）；选本机 = 保持本机现状，刷新方案与用户资料时间戳后 push 覆盖云端。
  不再把选择做成卡片里的两个按钮，也不会连弹两个窗。
- 新增 `GET /api/sync/settings` + `sync_engine.active_settings_row()`：桌面端据此拿到云端目标作息做对比
  （`desktop/sync.py` 的 `plan_state()` 现在返回 `local_target` / `cloud_target`，`needs_choice` 覆盖两类差异）。
  修了服务端一个真实缺陷：push 上来的 `user_settings` 过去只写 `SyncSettings.payload`，没有写回 `User` 行，
  于是「云端目标作息」永远是旧值；`apply_rows()` 现在调用 `_apply_settings_to_user()`（用写会话里的 User 实例）。
- 前端：`settings.js` 用 `confirmDialog()` 呈现比较结果（云端/本机各一行「自 X 起，目标 A–B，共 N 天」），
  按钮文案复用 `settings.syncPlanCloud` / `settings.syncPlanLocal`；新增 `settings.syncChoiceTitle` / `settings.syncChoiceHint`。
  i18n 594 keys、zh 593 = en 593、missing 0。
- 测试：`test_sync_convergence` 的方案用例扩展为「方案 + 目标作息」——A 上传 23:00–07:00 方案与目标作息，
  B 本机 00:30–08:30 + 目标 01:00–09:00，选云端后 B 的目标作息变成 23:00–07:00；之后 B 改成 22:00 方案 +
  21:30–05:30 目标并选本机，新机器 C pull 到 22:00 与 21:30–05:30。6 passed。
- 版本号 0.1.2 → 0.1.3；部署自证 4am 段新增 `active_settings_row` / `_apply_settings_to_user` / `@router.get("/settings")` 三条 grep。
- 已知小事：从聊天/文件卡片里双击安装包会因沙箱令牌写不了 `%TEMP%\is-*.tmp` 报 `Error 5: 拒绝访问`；
  从桌面/资源管理器双击，或直接用免安装 zip，不受影响。

### 0.1.4（2026-11-26）

- **修安装版窗口双击无窗口直接崩溃**：`console=False` 的 exe 从资源管理器启动时没有继承 std 句柄，
  `sys.stdout` / `sys.stderr` 为 `None`，uvicorn 的默认 logging config 在 `sys.stdout.isatty()` 上抛
  `AttributeError: 'NoneType' object has no attribute 'isatty'`，整个进程在建窗口前退出。
  - `desktop/app.py` 新增 `_ensure_std_streams()`：`main()` 第一步把缺失的 `stdout` / `stderr` 指向 `os.devnull`
    （真正的日志仍写 `%PROGRAMDATA%\YiukiSleep\logs\app.log`）。
  - `uvicorn.Config(..., log_config=None)`：不再让 uvicorn 自己 rebuild logging（它假设有控制台）。
- 这不是安装器的问题：同一份 0.1.3 用 `--serve-only`、`--smoke` 或从 cmd 启动都正常，只有「资源管理器双击窗口版」这一条路径会崩，
  所以之前的 `--smoke` / frozen 测试都没覆盖到。

### 0.2.0（2026-11-26）

- **应用改名**：显示名 `Sleep schedule`（窗口标题、页头 `app.name`、`app.userTitle`、`app.adminTitle`、浏览器标题）；
  exe 与安装包改为 `SleepSchedule.exe` / `SleepSchedule-setup-x.y.z.exe`，安装目录 `%ProgramFiles%\SleepSchedule`。
  **数据目录名不变**：`%PROGRAMDATA%\YiukiSleep` 与 `%LOCALAPPDATA%\YiukiSleep` 保留原名，这样 0.1.x 的用户升级后
  记录、方案与设备绑定都还在（`runtime.APP_FOLDER` 未改）。
  - `installer.iss` 仍是同一个 `AppId`：升级会沿用旧安装目录，因此新增 `[InstallDelete]` 删掉旧的 `YiukiSleep.exe`，
    避免桌面/开始菜单指向旧二进制。
- **桌面版隐藏备案号**：`frontend/user/css/app.css` 加 `html[data-mode="desktop"] .page-foot-icp { display: none; }`；
  网页版（`data-mode` 不是 `desktop`）照旧显示两条备案号，法规公示不受影响。
- **「一天调整/多天调整」控件右缘对齐**：`.segment` 加 `align-self: flex-start`。它所在的 `.field` 是纵向 flex，
  默认 `stretch` 会把分段控件拉满整行，右侧看上去比「多天调整」按钮多出一段空白。
- 发布物：`dist\desktop\SleepSchedule\`、`dist\desktop\SleepSchedule-setup-0.2.0.exe`、`latest.json`；
  桌面 `SleepSchedule-setup-0.2.0.exe` 与 `SleepSchedule-portable-0.2.0.zip`；GitHub 仓库打 `v0.2.0` tag 并写 release notes。

### 0.2.1（2026-11-26）

- **同步后自动刷新目标作息**：`GET /api/sync/settings` 拉到的云端 `user_settings` 会写进本机 `User` 行，
  但页面里的输入框读的是 `ctx.state.user`（登录时的旧对象），所以同步后「目标作息」仍显示旧时间，只有整页刷新才更新。
  修法：`frontend/user/js/pages/settings.js` 新增 `refreshUser(ctx)`（重新 `GET /api/auth/me` → `ctx.updateUser`），
  `loadSyncCard()` 的 `finish()` 与 `askPlanChoice()` 成功后都先 `await refreshUser(ctx)`，再 `ctx.reload()` 重渲染整页
  （`ctx.reload` 即 `main.js` 的 `renderRoute()`），因此绑定、立即同步、选择方案三条路径都会自动带出新时间。
- **显示名改为 `Sleep schedule`**：`sleepschedule` 作为产品名不够正式。i18n 的 `app.name` / `app.userTitle` /
  `app.adminTitle`、`index.html` 的 `<title>`、pywebview 窗口标题、安装器 `MyAppName`、FastAPI `title`、
  `APP_NAME`、README 全部改成 `Sleep schedule`；exe / 安装包 / 安装目录名仍是 `SleepSchedule*`（不加空格）。
- 版本 `0.2.1`：`backend/app/version.py`、`desktop/__init__.py`、`deploy/remote_apply_fix.sh` 的 4am grep 同步。


### 0.2.2（2026-11-26）

- **应用图标换成网页 favicon**：`frontend/user/favicon.svg`（蓝底 + 月牙 + 两颗星）用 Edge headless
  以 256×256 透明背景栅格化，再由 Pillow 生成 7 档尺寸的 `desktop/build/app.ico`（16/24/32/48/64/128/256）。
  `desktop/build/app.spec` 用它作为 exe 图标，`desktop/build/installer.iss` 用 `SetupIconFile=app.ico`
  并给组内快捷方式 / 可选桌面图标指定 `IconFilename`，所以窗口、任务栏、开始菜单与安装向导是同一枚图标。
- **「先不改，继续原计划」只问一次**：`services/advice_plan.py` 的 `deviation()` 增加 `acked`，
  `replan(strategy="keep")` 不再空返回，而是调用 `_remember_deviation_ack()` 把
  `deviation_ack = {"date": 起床日, "minutes": 偏差}` 写进方案 payload（并 `stamp_row` 进同步游标，
  另一台设备也能拿到这次确认）。同一晚、同样幅度再次进页面时 `acked=True`，前端
  `askDeviation()` 直接 return；换一晚或幅度变了 ack 对不上，仍然正常询问。
- `POST /api/advice/replan` 现在三种策略都返回 `saved: true`，并额外返回 `deviation_ack_saved`。
- 版本 `0.2.2`：`backend/app/version.py`、`desktop/__init__.py`、`deploy/remote_apply_fix.sh`
  的 4am grep 同步，另加 `deviation_ack`（后端）与 `deviation.acked`（前端）两条自证。


### 0.2.3（2026-11-26）

收尾批次（用户批准的 6 项 + 数据目录改名）。

- **服务器数据库每日备份**：`deploy/backup_db.py`（纯标准库，`sqlite3` 在线备份 + `PRAGMA integrity_check`
  + 按 mtime 保留 14 份 + 冲突加后缀），`deploy/install_backup.ps1` / `install_backup_remote.sh`
  装到 `/opt/sleep-schedule/deploy/`，cron 条目写到 `/etc/cron.d/sleep-schedule-backup`（每天 04:17 UTC，
  日志 `/var/log/sleep-schedule-backup.log`）。`tests/test_backup_db.py` 覆盖行数完整、保留份数、源库缺失。
  写 cron 时踩过坑：`printf ... | sudo_run tee` 会让 `tee` 读到 `sudo -S` 的密码并写进 cron 文件
  （644，世界可读）；现在改成先写普通文件再 `install -m 644`，并加「文件里必须含 backup_db.py、不能含 sudo」自检。
  事故之后部署账号密码已轮换，`/etc/systemd/system/sleep-schedule.service` 里的引导管理员账号密码
  移到了 `/etc/sleep-schedule/env`（600 root）+ `EnvironmentFile=`。
- **墓碑与 `sync_log` 清理**：`retention.purge_old_tombstones()`（默认 180 天，四张同步表）与
  `retention.trim_sync_log()`（`SSP_SYNC_LOG_RETENTION_DAYS`，默认 90 天）接入 `run_cleanup()`。
  `sync_log.rev` 改为 `AUTOINCREMENT` 主键，避免清理掉最大 rev 后 SQLite 复用 rev 造成游标漏行：
  `models.SyncLog.__table_args__ = {"sqlite_autoincrement": True}`，老库由
  `db._migrate_sync_log_autoincrement()` 原地重建（重命名→建新表→搬行→删旧表→建索引）。
- **CI 扩容**：`.github/workflows/ci.yml` 在原有静态检查与 `test_advice_engine` 之外，加了
  `test_desktop_mode`、`test_sync_protocol`、`test_sync_convergence`、`test_importers`、`test_updater`、
  `test_desktop_runtime`、`test_backup_db`（装 `python-multipart==0.0.32`、`pypdf==6.4.0`；
  `test_desktop_frozen` / `test_desktop_shell` 在 Linux 上自行 skip）。
- **更新提示端到端**：`desktop/updater.manifest_url()` 支持 `SSP_UPDATE_MANIFEST_URL` 覆盖（自建服务器、
  测试用 `file://` 清单），`updater.check()` 默认走它；`tests/test_updater.py` 加直接读本地清单的用例，
  `tests/test_desktop_mode.py` 的桌面探针请求 `/api/desktop/update` 并断言 `update_available is True`。
- **安装文档**：新增 `docs/INSTALL.md`（安装版/便携版/源码三种形态、升级、数据与备份、卸载、常见问题），
  桌面的 `SleepSchedule 安装说明.txt` 按它重写（不再写死版本号）。
- **数据目录改名**：`desktop/runtime.py` 的 `APP_FOLDER` 改为 `SleepSchedule`（旧名保留为
  `LEGACY_APP_FOLDER`）；`migrate_legacy_root()` 在新目录还没有 `data\app.db`、而旧目录有时复制一次
  （旧目录不删，失败下次重试），`app.py` 在 `ensure_dirs()` 前调用它；`installer.iss` 的 `[Dirs]`
  改为 `{commonappdata}\SleepSchedule`；`tests/test_desktop_runtime.py` 同步断言并加迁移用例。
- 版本 `0.2.3`：`backend/app/version.py`、`desktop/__init__.py`、`deploy/remote_apply_fix.sh` 的 4am grep 同步，
  另加 `purge_old_tombstones` / `trim_sync_log` / `AUTOINCREMENT` / `LEGACY_APP_FOLDER` 四条自证。

### 0.2.4（2026-11-27）

文案与安装体验整理。

- **界面文案改为书面语**：`frontend/shared/i18n.js` 中 87 条偏口语的键（中英各一份）统一改为正式、精简的
  表述，例如 `records.overview.fewer` 由「至少两晚记录才能看推移」改为「需至少两晚记录方可显示推移」，
  导入错误、同步提示、偏离提示等也一并收紧；占位符与键名不变，`scripts/check_i18n_keys.py` 仍为 0 missing、
  0 placeholder mismatch，中英键数一致。脚本 `.tmp/formalize_i18n.py` 按 CRLF 原样改写，改完复查界面中文
  里不再出现「吧/呢/你/我」这类口语成分。
- **安装器清理旧快捷方式**：0.2.1 及更早的安装使用「Yiuki Sleep」程序组和 `sleepschedule.lnk`，
  升级后残留在开始菜单里（其中一个指向已删除的 `YiukiSleep.exe`，图标显示为旧版默认图）。
  `installer.iss` 增加 `UsePreviousGroup=no` 与 `[InstallDelete]`，安装时删除旧程序组里的快捷方式、
  旧桌面图标和旧 exe；新安装只保留一个「Sleep schedule」程序组。
- **图标说明**：应用 exe、安装包与快捷方式共用 `desktop/build/app.ico`（由 `frontend/user/favicon.svg`
  渲染）。0.2.0/0.2.1 的 exe 没有自定义图标，显示的是 PyInstaller 默认图标（黄色图形，容易被看成蛇），
  0.2.2 起已改为网页同款月亮图标。
- 版本 `0.2.4`：`backend/app/version.py`、`desktop/__init__.py`、`deploy/remote_apply_fix.sh` 的 4am grep 同步。
