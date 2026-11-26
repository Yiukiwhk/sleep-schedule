# 安装与升级（桌面版）

Sleep schedule 有三个可以拿到手的形态，按从简到全排列：

| 形态 | 怎么装 | 数据放哪 | 适合谁 |
| --- | --- | --- | --- |
| 安装版 | 双击 `SleepSchedule-setup-<版本>.exe` | `%PROGRAMDATA%\SleepSchedule`（整台机器一个档案） | 日常使用，推荐 |
| 便携版 | 解压 `SleepSchedule-portable-<版本>.zip`，双击里面的 `SleepSchedule.exe` | `%LOCALAPPDATA%\SleepSchedule` | 不想动注册表、装在 U 盘上带走 |
| 源码运行 | 见 [README](../README.md) 的"桌面版"一节 | 由 `--data-dir` 决定 | 开发/调试 |

安装包与 zip 都发布在 <https://yiuki.me/desktop/> 和 [GitHub Releases](https://github.com/Yiukiwhk/sleep-schedule/releases)。应用启动后会在设置页提示是否有新版本，不会静默替换。

## 安装版

1. 从**资源管理器**双击安装包（不要从聊天窗口、网页的下载条或某些"受限"终端里启动安装程序——那种进程没有写 `%TEMP%` 的权限，Inno Setup 会报 `Error 5: 拒绝访问` 并退到 `is-XXXX.tmp` 创建失败）。
2. 向导里可以勾选"创建桌面图标"。安装目录是 `%ProgramFiles%\SleepSchedule`，只有安装程序会提权；它会给 `%PROGRAMDATA%\SleepSchedule` 授"用户可修改"权限，否则标准用户第二次启动就会因为写不了 `app.log` / `app.db` 报 `Permission denied`。
3. 首次启动会建一个本地档案「本机」，不需要注册和登录。

## 便携版

1. 解压到任意**你有写权限**的目录（例如 `%LOCALAPPDATA%\Programs\SleepSchedule`、`C:\Apps\SleepSchedule`）。
2. 双击 `SleepSchedule.exe`。数据默认写到 `%LOCALAPPDATA%\SleepSchedule`。
3. 想指定别的位置：`SleepSchedule.exe --data-dir D:\sleep-data`。

便携版的三种数据目录开关：

- `--per-user-data`：强制用 `%LOCALAPPDATA%\SleepSchedule`（每用户独立）。
- `--machine-data`：强制用 `%PROGRAMDATA%\SleepSchedule`（需要该目录可写，通常只有安装版才具备）。
- `--data-dir PATH`：完全指定，优先级最高。

## 升级

- 安装版：直接运行新安装包即可，AppId 相同会沿用原目录；会删掉旧版残留的 `YiukiSleep.exe`。数据目录不动。
- 便携版：关掉应用，用新 zip 覆盖旧目录；`device.json`、`config.json` 和 `data\` 都在数据目录里，不受覆盖影响。
- 0.2.2 及更早的数据写在 `%PROGRAMDATA%\YiukiSleep` / `%LOCALAPPDATA%\YiukiSleep`。0.2.3 起目录名改为 `SleepSchedule`，首次启动会自动把旧目录**复制**过来（旧目录保留不删），见 `desktop/runtime.py` 的 `migrate_legacy_root()`。

## 数据、备份与卸载

- 数据库：`<数据目录>\data\app.db`；日志：`<数据目录>\logs\app.log`。
- 每次同步前会自动导出一份 JSON 快照到 `<数据目录>\backup\`，保留最近 10 份。
- 服务器（自建部署）上的每日备份见 [deploy/backup_db.py](../deploy/backup_db.py)：`sqlite3` 在线备份 + `PRAGMA integrity_check`，保留 14 份，由 `/etc/cron.d/sleep-schedule-backup` 触发。
- 卸载默认**保留**数据目录：重装后记录、方案和云绑定都还在。想彻底清空，先卸载，再手动删除数据目录。

## 常见问题

**启动时弹 `could not prepare the data folder` / `Permission denied`**
数据目录不可写。安装版请用安装程序装（它会授权）；便携版换一个可写目录，或加 `--per-user-data`。

**从资源管理器双击没反应，或者窗口一闪就没了**
看 `<数据目录>\logs\app.log`。0.2.3 起窗口版会先把缺失的 stdout/stderr 指向空设备，并把启动失败写进日志和弹窗。

**安装程序报 `Error 5: 拒绝访问`（`is-XXXX.tmp`）**
安装程序被一个受限进程拉起（例如聊天软件的"打开文件"、某些沙箱终端）。关掉它，从资源管理器里双击安装包。

**更新检查一直说"已是最新"**
设置页的更新检查读 `https://yiuki.me/desktop/latest.json`；自建服务器可以用环境变量 `SSP_UPDATE_MANIFEST_URL` 指向自己的清单。

## 服务器端（可选）

桌面版可以绑定云端账号做多机同步。官方服务器是 `https://yiuki.me`（设置里留空即用它）。自建见 [docs/DESKTOP.md](DESKTOP.md) 的部署小节，以及 `deploy/` 下的脚本：`apply_fix.ps1`、`publish_desktop.ps1`、`install_backup.ps1`。
