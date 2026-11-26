# 发布说明

本文件记录 GitHub Releases 的说明文案与可复核的产物信息。发布流程见
[`docs/DESKTOP.md`](DESKTOP.md) 第 10、13 节；桌面端用 `deploy\publish_desktop.ps1` 发布到
`https://yiuki.me/desktop/`，GitHub Release 的附件与此处列出的文件一致。

## v0.2.3（2026-11-26）

已发布：<https://github.com/Yiukiwhk/sleep-schedule/releases/tag/v0.2.3>

- **服务器数据库自动备份**：`deploy/backup_db.py` 每天 04:17 用 SQLite 在线备份 API 导出一份
  `/var/backups/sleep-schedule/app-<UTC 时间>.db`，校验 `PRAGMA integrity_check` 后保留最近 14 份；
  安装脚本 `deploy/install_backup.ps1`。
- **墓碑与同步日志清理**：删除记录留下的墓碑（180 天）由 `purge_old_tombstones()` 清理，
  `sync_log` 只保留 90 天（`SYNC_LOG_RETENTION_DAYS`），并给 `sync_log.rev` 加 `AUTOINCREMENT`
  迁移（避免清空后 SQLite 复用 rev、客户端游标漏行）。
- **桌面数据目录改名**：`%PROGRAMDATA%\SleepSchedule` / `%LOCALAPPDATA%\SleepSchedule`；首次启动会
  从旧的 `YiukiSleep` 目录复制一份数据（旧目录保留），升级不丢记录、方案与云同步绑定。
- **更新提示端到端**：`SSP_UPDATE_MANIFEST_URL` 可覆盖清单地址，`/api/desktop/update` 与
  `tests/test_desktop_mode.py` 现在覆盖「发现新版本」这条链路；CI 增加同步/导入/更新/备份测试。
- **安装文档**：新增 `docs/INSTALL.md`，桌面安装说明重写为不写死版本号。

| 文件 | 大小（字节） | SHA-256 |
| --- | --- | --- |
| `SleepSchedule-setup-0.2.3.exe` | 72,008,744 | `221f4b00f19b786c3eb3304d6faee71fedf2872ba094a7297e24f27f34f4c31f` |
| `SleepSchedule-portable-0.2.3.zip` | 96,813,294 | `d5f28e37e03b0db7790fb324ab3024087c34cacf66f4f13e79d7099cf6d3c4b4` |

## v0.2.2（2026-11-26）

已发布：<https://github.com/Yiukiwhk/sleep-schedule/releases/tag/v0.2.2>

- **应用图标换成网页 favicon**：窗口、任务栏、开始菜单、桌面快捷方式与安装向导统一使用网页版那枚
  蓝底月牙图标（`desktop/build/app.ico`，由 `frontend/user/favicon.svg` 渲染，16–256 px 共 7 档）。
- **「先不改，继续原计划」只问一次**：方案可能无法完成时弹出的偏离提示，选「继续原计划」后会把这次
  偏离（起床日 + 分钟数）记进方案，同一晚、同样幅度再进页面不再追问；换一晚或偏差变了仍会正常询问。
  这次确认也会随方案同步到其它设备。
- **更新清单不再带 BOM**：`latest.json` 改为无 BOM 的 UTF-8 写出，客户端解析同时兼容带 BOM 的旧清单，
  避免「检查更新」静默失败。

| 文件 | 大小（字节） | SHA-256 |
| --- | --- | --- |
| `SleepSchedule-setup-0.2.2.exe` | 72,009,629 | `1699864891e41aafa521fc93e5fe0ee4b4f16b9f955e0fccf95de5d3b7e93f22` |
| `SleepSchedule-portable-0.2.2.zip` | 96,810,120 | `883b78eca9a7dc526c5bf07ce473ccbd3f3963fcc9c5b7e7fc6e3d50a2ebd1eb` |

## v0.2.1（2026-11-26）

已发布：<https://github.com/Yiukiwhk/sleep-schedule/releases/tag/v0.2.1>

- **同步后自动刷新目标作息**：绑定 / 立即同步 / 选择方案之后，页面会重新拉一次 `/api/auth/me` 并重渲染，
  「目标作息」输入框立刻显示云端同步下来的时间，不需要再手动刷新整页。
- **显示名改为 `Sleep schedule`**：窗口标题、页头、浏览器标题、安装器显示名都改成 `Sleep schedule`；
  exe、安装包与安装目录仍是 `SleepSchedule`（文件名不加空格）。
- 数据目录仍是 `%PROGRAMDATA%\YiukiSleep` / `%LOCALAPPDATA%\YiukiSleep`，从 0.2.0 升级不需要迁移。

| 文件 | 大小（字节） | SHA-256 |
| --- | --- | --- |
| `SleepSchedule-setup-0.2.1.exe` | 72,013,029 | `b1006bae0fd2e5e4e81982b34d2baa0d8045a08584f525b36061750fef010bd9` |
| `SleepSchedule-portable-0.2.1.zip` | 96,847,736 | `467b0a5215b1c78c8f7707b6fad0c1d2bef40243243de1f9290559d20c8e4047` |

## v0.2.0（2026-11-26）

第一个公开版本：网页版（`https://yiuki.me/user/`）与 Windows 桌面版同源发布。

已发布：<https://github.com/Yiukiwhk/sleep-schedule/releases/tag/v0.2.0>
（`main` 与 `v0.2.0` tag 均已推送；安装包与免安装 zip 作为 release 附件上传）

### 桌面版（Windows 10/11 x64）

- **免安装**：`SleepSchedule-portable-0.2.0.zip`（解压后双击 `SleepSchedule.exe`，
  数据默认在 `%LOCALAPPDATA%\YiukiSleep`）
- **安装包**：`SleepSchedule-setup-0.2.0.exe`（装到 `%ProgramFiles%\SleepSchedule`，
  数据在 `%PROGRAMDATA%\YiukiSleep`，安装器已授权普通用户可写；卸载保留数据）

| 文件 | 大小（字节） | SHA-256 |
| --- | --- | --- |
| `SleepSchedule-setup-0.2.0.exe` | 72,019,639 | `564f6781c4e8efa8eadee0f90728e05e6be9a36e54fb7500f01749c4e91ab992` |
| `SleepSchedule-portable-0.2.0.zip` | 96,847,039 | `D0CD1BF61C08B29BB72F264920EC833EC34B3E07DDCA39533890359BA54C5453` |

安装包未做代码签名（SmartScreen 可能提示「未知发布者」，选择「仍要运行」即可）；
校验哈希可用：

```powershell
Get-FileHash .\SleepSchedule-setup-0.2.0.exe -Algorithm SHA256
```

### 主要功能

- 记录每天的入睡/起床时间与三餐，生成本机作息建议与多日调整方案
- 桌面版免登录：首次启动自动建本机用户「本机」，没有管理后台入口，写入不限速、不按 6 个月删数据
- 云同步（可选）：用网页版账号登录后双向同步记录、三餐、设置、当前方案与目标作息；
  冲突按 LWW 合并、删除用墓碑不复活，同步前自动导出 JSON 快照
- 导入：应用导出的 JSON、其它设备导出的 JSON/CSV、PDF 文本层；安装包内含 OCR，
  可直接识别睡眠 App 截图（分期色带按截图横轴换算）
- 更新提示：只检查 `https://yiuki.me/desktop/latest.json`，不静默下载
- 自建服务器：服务器地址留空用官方 `https://yiuki.me`，也可以填自己的部署地址

### 相对 0.1.x 的改动（内部版本，未单独发布）

- 桌面版数据目录按安装方式区分：安装版用 `%PROGRAMDATA%\YiukiSleep`，便携版用 `%LOCALAPPDATA%\YiukiSleep`
  （0.1.0 的便携版误用机器级目录，第二次启动会因 ACL 报 `Permission denied`）
- 修「窗口版从资源管理器双击直接崩溃」：无 std 句柄时 `sys.stdout is None`，uvicorn 日志初始化报
  `AttributeError: 'NoneType' object has no attribute 'isatty'`
- 同步补 rev 回填与网页侧写入推进（否则旧账号同步数量一直是 0）；删除改为墓碑
- 方案也能同步；云同步后若方案或目标作息不一致，弹**一个**窗口让用户选云端/本机
- 应用改名 `Sleep schedule`：exe、安装目录、窗口标题、页头都改；数据目录名保留 `YiukiSleep`，升级不丢数据
- 桌面窗口不再显示 ICP/公安备案号（网页版照旧显示）
- 修建议页「一天调整/多天调整」分段控件被拉伸、右缘与按钮不对齐

### 已知限制

- 安装包与 exe 均未签名
- 未在干净虚拟机上做安装/升级/卸载手工验收（本机已用安装版与便携版跑通）
- 不要把安装包从聊天窗口/沙盒终端里双击，那里会因写不了真实 `%TEMP%` 报 `Error 5`
- 桌面版定位是可选的本地增强；服务器版仍是完整形态（多用户、管理后台）

### 服务器版

同一版本已部署到 `https://yiuki.me`：`GET /api/runtime` 返回
`{"mode":"server","desktop":false,"version":"0.2.0",...}`；桌面端的同步接口
（`/api/sync/*`）与方案接口（`/api/sync/plan`、`/api/sync/settings`）都在线。

许可证：MIT（见 [LICENSE](../LICENSE)）。
