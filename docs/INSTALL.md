# 安装与升级（桌面版）

Sleep schedule 提供三种分发形态：

| 形态 | 安装方式 | 数据目录 | 适用场景 |
| --- | --- | --- | --- |
| 安装版 | 运行 `SleepSchedule-setup-<版本>.exe` | `%PROGRAMDATA%\SleepSchedule`（整机共用一份档案） | 日常使用（推荐） |
| 便携版 | 解压 `SleepSchedule-portable-<版本>.zip`，运行其中的 `SleepSchedule.exe` | `%LOCALAPPDATA%\SleepSchedule` | 免安装、可放入移动存储 |
| 源码运行 | 见 [README](../README.md) 的「桌面版」一节 | 由 `--data-dir` 指定 | 开发与调试 |

安装包与压缩包发布在 <https://yiuki.me/desktop/> 与 [GitHub Releases](https://github.com/Yiukiwhk/sleep-schedule/releases)。应用启动后会检查新版本并在设置页提示，不执行静默替换。

## 安装版

1. 从**资源管理器**运行安装包。不要从聊天窗口、浏览器下载栏或受限终端启动，这类进程无法写入 `%TEMP%`，Inno Setup 会报 `Error 5: 拒绝访问`（`is-XXXX.tmp`）。
2. 向导中可勾选「创建桌面图标」。程序安装到 `%ProgramFiles%\SleepSchedule`；安装程序以管理员权限运行，并为 `%PROGRAMDATA%\SleepSchedule` 授予「用户可修改」权限，否则标准用户再次启动时会因无法写入 `app.log` 与 `app.db` 报 `Permission denied`。
3. 首次启动会创建本地档案「本机」，无需注册或登录。

## 便携版

1. 解压到任意**可写**目录，例如 `%LOCALAPPDATA%\Programs\SleepSchedule` 或 `C:\Apps\SleepSchedule`。
2. 运行 `SleepSchedule.exe`，数据默认写入 `%LOCALAPPDATA%\SleepSchedule`。
3. 指定其它位置：`SleepSchedule.exe --data-dir D:\sleep-data`。

数据目录开关：

- `--per-user-data`：强制使用 `%LOCALAPPDATA%\SleepSchedule`（每用户独立）。
- `--machine-data`：强制使用 `%PROGRAMDATA%\SleepSchedule`（需该目录可写，通常仅安装版具备）。
- `--data-dir PATH`：完全指定路径，优先级最高。

## 升级

- 安装版：运行新安装包即可。AppId 一致会沿用原安装目录，并删除旧版残留的 `YiukiSleep.exe`；数据目录不变。
- 便携版：关闭应用后用新压缩包覆盖旧目录。`device.json`、`config.json` 与 `data\` 均位于数据目录，不受覆盖影响。
- 0.2.2 及更早版本的数据位于 `%PROGRAMDATA%\YiukiSleep` / `%LOCALAPPDATA%\YiukiSleep`。0.2.3 起目录名改为 `SleepSchedule`，首次启动会将旧目录**复制**到新位置（旧目录保留），实现见 `desktop/runtime.py` 的 `migrate_legacy_root()`。

## 数据、备份与卸载

- 数据库：`<数据目录>\data\app.db`；日志：`<数据目录>\logs\app.log`。
- 每次同步前会自动导出 JSON 快照到 `<数据目录>\backup\`，保留最近 10 份。
- 服务器（自建部署）的每日备份见 [deploy/backup_db.py](../deploy/backup_db.py)：`sqlite3` 在线备份与 `PRAGMA integrity_check` 校验，保留 14 份，由 `/etc/cron.d/sleep-schedule-backup` 触发。
- 卸载默认**保留**数据目录，重装后记录、方案与云同步绑定仍然可用。如需彻底清除，先卸载，再手动删除数据目录。

## 常见问题

**启动时提示 `could not prepare the data folder` 或 `Permission denied`**
数据目录不可写。安装版请使用安装程序安装（安装程序会授予权限）；便携版请改用可写目录，或加 `--per-user-data`。

**双击后无响应，或窗口立即关闭**
查看 `<数据目录>\logs\app.log`。0.2.3 起窗口版会将缺失的 stdout/stderr 指向空设备，并把启动失败写入日志与弹窗。

**安装程序报 `Error 5: 拒绝访问`（`is-XXXX.tmp`）**
安装程序由受限进程启动（例如聊天软件的「打开文件」或沙箱终端）。请关闭该进程，并从资源管理器运行安装包。

**更新检查始终提示已是最新**
设置页的更新检查读取 `https://yiuki.me/desktop/latest.json`；自建服务器可用环境变量 `SSP_UPDATE_MANIFEST_URL` 指向自有清单。

## 服务器端（可选）

桌面版可绑定云端账号实现多机同步。官方服务器为 `https://yiuki.me`（设置中留空即使用官方服务器）。自建部署见 [docs/DESKTOP.md](DESKTOP.md) 的部署小节与 `deploy/` 下的脚本：`apply_fix.ps1`、`publish_desktop.ps1`、`install_backup.ps1`。
