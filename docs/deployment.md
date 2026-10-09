# 部署与维护

安装命令和功能清单见 [README](../README.md)，工程约束见 [AGENTS](../AGENTS.md)，实测结果见 [验收记录](acceptance.md)。本应用由一个后端同时提供页面、API、策略和 Modbus 服务；前端不另启服务。

独立目录包用户无需安装 Python；下载、启动与三台从机配置见 [独立包说明](standalone.md)。包内 `service/` 提供 systemd、Windows 任务计划与 macOS launchd 模板；下文 Python 命令适用于源码／wheel 部署。

## 发布准备

1. 构建机器安装 Python 3.12+、Node.js 22.12+，执行安装脚本。
2. 执行后端检查、前端构建、真实浏览器检查；构建 wheel 并在仓库外验证。
3. 复制 wheel 和 `requirements.lock.txt` 到目标机器，以约束文件安装到独立 Python venv；运行机器不需要 Node.js。
4. 选择本机可写数据目录，检查磁盘余量和身份权限；首次启动不开启设备监听，先配置、验证端口后显式启动。

依赖需要下载时使用运行环境已配置的网络及证书设置。离线部署在同平台、同 Python 版本的联网机器准备依赖：

```bash
python -m pip download --constraint requirements.lock.txt --dest wheelhouse ./modbus_slaver_simulator-0.2.0rc5-py3-none-any.whl
# 将 wheelhouse 一并复制到目标机器
python -m pip install --no-index --find-links wheelhouse --constraint requirements.lock.txt ./modbus_slaver_simulator-0.2.0rc5-py3-none-any.whl
```

以上约束不会安装全部开发工具，仅锁定应用实际需要的依赖。不同平台的二进制依赖不能直接混用。

## Linux systemd 示例

以下是需要由主机管理员配置的模板；将路径、账户及网卡替换为实际值。先创建 `modbus` 运行账户，使 `/var/lib/modbus-simulator` 归该账户所有；在 `/opt/modbus-simulator/venv` 安装发布包。SQLite 数据目录使用本机磁盘。

保存到 `/etc/systemd/system/modbus-simulator.service`：

```ini
[Unit]
Description=Modbus TCP simulator and local management
After=network.target

[Service]
Type=simple
User=modbus
Group=modbus
WorkingDirectory=/opt/modbus-simulator
ExecStart=/opt/modbus-simulator/venv/bin/python -m simulator.supervisor --data-dir /var/lib/modbus-simulator --port 8000
Restart=no
TimeoutStopSec=25
KillMode=mixed
UMask=0077

[Install]
WantedBy=multi-user.target
```

监督器自行处理有限重启；`Restart=no` 保留达到上限后的故障状态，避免服务管理器重新启动监督器而形成无限重启。`KillMode=mixed` 先让监督器正常终止应用，超过停止时限才清理整个服务进程组。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now modbus-simulator
sudo systemctl status modbus-simulator
sudo journalctl -u modbus-simulator -n 100
```

管理端口只在本机访问。Modbus 设备可绑定实际网卡或 `0.0.0.0`；防火墙只放行计划使用的设备端口及来源。使用标准 502 端口时需要相应绑定权限；调试和常规部署优先选择 1502。

## Windows 常驻运行

源码部署执行 `scripts/build.ps1` 后，`scripts/install-startup-task.ps1 -DataDir <目录> -Port 8000` 注册登录启动任务。脚本仅创建登录触发器，不能把它当作无人登录的开机服务。

wheel 部署在任务计划程序新建任务：

| 项目 | 配置 |
| --- | --- |
| 程序 | 已安装 wheel 的 venv 中 `python.exe` 绝对路径 |
| 参数 | `-m simulator.supervisor --data-dir "C:\实际可写数据目录" --port 8000` |
| 触发器 | 登录时；无人值守时使用启动触发器及合适账户 |
| 运行身份 | 有数据目录读写权限、设备网卡和端口访问权限的账户 |
| 执行时限 | 不设置最长运行时间 |
| 重复实例 | 不启动新实例 |
| 失败重启 | 不在监督器达到上限后无限重新启动任务 |

检查任务实际启动进程、健康接口、设备端口及数据写入。为防止计划任务强制停止绕过正常保存，可先通过管理页面停止设备、等待成功快照，再停止任务；强制终止按最近成功快照恢复，不能保证保存最后一次写入。开启设备自动启动前须在目标机器验证地址、端口和防火墙。

## 升级与恢复

1. 停止常驻任务／systemd 服务，确认监督器、应用和重任务子进程退出。
2. 保留当前 wheel、锁定文件及整个数据目录的离线副本；不要只复制活动 SQLite 的 `.db` 文件。
3. 安装经过验证的新 wheel，使用相同的数据目录启动；检查 `/api/health/ready`、完整 `/api/health`、配置与设备状态。
4. 故障时停止新版本并还原旧发布包与离线数据副本；如果仅配置损坏，使用已验证配置备份离线恢复。

```bash
python -m simulator.maintenance --data-dir /实际数据目录 restore --backup /实际数据目录/backups/config.0.db
python -m simulator.maintenance --data-dir /实际数据目录 compact
```

恢复会拒绝未知版本，保留故障配置及 WAL 到 `recovery/` 后替换；历史库不自动备份和修复。活动实例锁阻止在线维护。空间回收可能需要两倍数据库文件大小加 512MiB 的空闲空间，必须在停机窗口执行。

## 发布验收

Linux 回归、真实浏览器、安装包和短时负载通过不替代目标机器验收。检查另一台电脑通过实际网络完成 Modbus 读写；Windows 实机验证常驻、重启、恢复、文件权限和防火墙；按验收记录完成当前源码版本的 24 小时及后续 72 小时测试。未完成的项目保持待验收。
