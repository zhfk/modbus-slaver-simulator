# 下载、解压和直接运行

GitHub [Releases](https://github.com/zhfk/modbus-slaver-simulator/releases) 中的平台包包含 Python、依赖、页面和字体。选择匹配操作系统与 CPU 架构的文件，完整解压后运行；不要只复制单独的可执行文件，`_internal/` 目录必须保留。

## 启动

Linux：

```bash
tar -xzf modbus-simulator-0.2.0rc2-linux-x86_64.tar.gz
cd modbus-simulator-0.2.0rc2-linux-x86_64
./start.sh
```

Windows：解压 zip，双击 `start.cmd`。需要自定义时打开 PowerShell：

```powershell
.\modbus-simulator.exe --port 8000 --data-dir "$env:LOCALAPPDATA\ModbusSimulator"
```

macOS：选择 Intel 的 `x86_64` 或 Apple Silicon 的 `arm64` 包，解压后双击 `start.command`，或在终端执行 `./start.command`。程序未公证时，通过系统设置允许已确认来源的下载，不关闭系统安全机制。

控制台显示服务已启动后，在本机浏览器打开 `http://127.0.0.1:8000`。首次没有设备，点击“新建设备”创建温控模板或空设备；配置完成后显式启动。关闭浏览器不停止服务；在启动终端按 Ctrl+C 正常停止。首次使用不需要下载依赖，应用页面和字体也不访问公网。

可执行文件默认运行外部监督器；`serve` 命令用于直接运行应用，通常无需手动使用。`--version` 显示版本。数据默认位于：

| 系统 | 用户数据目录 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\ModbusSimulator` |
| Linux | `$XDG_DATA_HOME/modbus-simulator`，未设置时为 `~/.local/share/modbus-simulator` |
| macOS | `~/Library/Application Support/ModbusSimulator` |

可通过 `--data-dir` 设置本机可写目录，常驻任务使用明确的绝对路径；升级替换安装目录时保留数据目录。一个数据目录只能由一个应用实例使用。

## 多个从机

分别创建“从机 1#、从机 2#、从机 3#”。例如都监听 `0.0.0.0:1502`，Unit ID 分别设为 1、2、3；外部客户端连接同一主机的 1502 端口，通过 Unit ID 选择设备。相同端点不能重复 Unit ID。各设备可有相同寄存器地址，但数值互不影响。停止其中一台不关闭其他设备的监听；最后一台停止才释放端点。

如果主机软件不支持多个 Unit ID，给设备分别配置 1502、1503、1504 端口即可。绑定实际网卡或 `0.0.0.0` 才能接受其他电脑接入；按需允许相应设备端口的防火墙入站。管理接口仍只开放本机。

## 常驻与维护

包内 `service/` 提供三个系统的模板：

- Linux：调整 `modbus-simulator.service` 的可执行路径、账户及数据目录；按照部署文档注册 systemd。使用 `Restart=no`，不绕过监督器的有限重启上限。
- Windows：执行 `service/install-windows-task.ps1` 注册登录启动。无人值守开机需要在任务计划程序设启动触发器和适当账户；不是注册 Windows 原生 SCM 服务。
- macOS：把 `com.modbus.simulator.plist` 中的绝对路径替换为真实安装、数据及日志路径；复制到 `~/Library/LaunchAgents/`，用 `launchctl bootstrap gui/$(id -u) ...` 加载。`KeepAlive=false` 保留监督器停止后的故障状态。系统启动级 LaunchDaemon 需要管理员单独配置身份和目录权限。

正常停止后执行内置维护，不需要 Python：

```bash
./modbus-simulator maintenance --data-dir /实际数据目录 compact
./modbus-simulator maintenance --data-dir /实际数据目录 restore --backup /实际数据目录/backups/config.0.db
```

Windows 对应 `.\modbus-simulator.exe maintenance ...`。恢复前保留数据目录离线副本，历史库不默认备份。下载新版本后停止旧服务、解压到新的安装目录、使用原数据目录启动，并更新常驻任务的可执行路径；不要把 `_internal/` 的不同版本混合覆盖。

## 构建与发布

开发者构建需要 Python 3.12 和 Node.js 22.12+；用户运行包不需要这些工具。必须在对应 OS／架构原生构建，不把 Linux 程序重命名为 Windows/macOS 包。

```bash
python -m venv .release-venv
.release-venv/bin/python -m pip install -r requirements-release.txt
npm --prefix frontend ci
npm --prefix frontend run build
.release-venv/bin/python scripts/build_release.py
# 用开发环境运行验证器，执行的是独立可执行文件
.venv/bin/python scripts/release_check.py --binary dist/release/实际包目录/modbus-simulator
```

Windows 对应 `.release-venv\Scripts\python.exe` 和 `modbus-simulator.exe`。默认输出 `dist/release/`，已有同名包时拒绝覆盖；可用 `--output` 指定新的输出目录。构建信息中的 libc 基线决定 Linux 兼容范围，不能承诺所有发行版均可使用。

GitHub Actions 的 `release.yml` 在四种原生 runner 构建、回归检查并验证解压包。创建与 `pyproject.toml` 版本匹配的 `v*` 标签触发发布；手动运行仅生成 Actions 产物。所有平台检查成功后，发布任务使用仓库自带的 `GITHUB_TOKEN` 上传四个平台包及校验文件。当前自动发布标记为 prerelease；长期验收通过后才能另行提升为稳定版本。
