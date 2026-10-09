# Modbus 从机模拟器

Vue 3 + TypeScript 管理界面，Python/FastAPI 管理服务与共享内存 Modbus TCP 运行时。前端编译为静态文件，启动一个后端即可使用。所有设备共用一个应用实例；无需独立前端服务器、Redis 或外部数据库。

支持分别创建“从机 1#、2#、3#…”：共用 IP／端口时使用不同 Unit ID，或分别使用不同端口；点位、策略和值互相独立，停止一台不影响其他设备。默认最多 16 台设备、8 个端点、10000 个点位。

| 启动方式 | 适用场景 | 所需环境 |
| --- | --- | --- |
| [Release 包启动](#release-包启动推荐) | 下载解压后直接使用 | 无需安装 Python、Node.js 或数据库服务 |
| [源码启动](#源码启动) | 修改代码、本地开发和自行构建 | Python 3.12、Node.js 22.12+、Git |
| [wheel 部署](#wheel-部署需-python与升级) | 已有 Python 环境，自行分发安装包 | Python 3.12 和对应平台依赖 |

当前版本为 `0.2.0rc6` 功能预发布。三个发布目标已通过原生检查和下载校验，长期运行验收尚未通过；详细证据见 [验收记录](docs/acceptance.md)。

## Release 包启动（推荐）

在 [v0.2.0rc6 下载页](https://github.com/zhfk/modbus-slaver-simulator/releases/tag/v0.2.0rc6) 的 **Assets** 中选择对应平台包。包内包含 Python、应用依赖、前端页面和离线字体；不要下载 `Source code` 代替独立运行包。完整解压并保留 `_internal/` 目录，不能只复制可执行文件。

| 平台 | 下载文件 | 兼容与验证范围 |
| --- | --- | --- |
| Linux x86_64 | [modbus-simulator-0.2.0rc6-linux-x86_64.tar.gz](https://github.com/zhfk/modbus-slaver-simulator/releases/download/v0.2.0rc6/modbus-simulator-0.2.0rc6-linux-x86_64.tar.gz) | Ubuntu 22.04 原生验证，glibc 2.35+；不支持 Alpine/musl |
| Windows x64 | [modbus-simulator-0.2.0rc6-windows-x86_64.zip](https://github.com/zhfk/modbus-slaver-simulator/releases/download/v0.2.0rc6/modbus-simulator-0.2.0rc6-windows-x86_64.zip) | Windows Server 2022 原生验证，目标 Windows 10／11 尚须实机验收 |
| macOS Apple Silicon | [modbus-simulator-0.2.0rc6-macos-arm64.tar.gz](https://github.com/zhfk/modbus-slaver-simulator/releases/download/v0.2.0rc6/modbus-simulator-0.2.0rc6-macos-arm64.tar.gz) | macOS 15 原生验证，更老系统未验证；不提供 Intel 版本 |

下载页的 `SHA256SUMS.txt` 可用于核对文件完整性。

### Linux

在下载文件所在目录执行：

```bash
tar -xzf modbus-simulator-0.2.0rc6-linux-x86_64.tar.gz
cd modbus-simulator-0.2.0rc6-linux-x86_64
./start.sh
```

### Windows

解压 zip，进入包目录双击 `start.cmd`。也可在下载目录打开 PowerShell 执行：

```powershell
Expand-Archive -Path .\modbus-simulator-0.2.0rc6-windows-x86_64.zip -DestinationPath .
Set-Location .\modbus-simulator-0.2.0rc6-windows-x86_64
.\start.cmd
```

### macOS Apple Silicon

在下载文件所在目录执行：

```bash
tar -xzf modbus-simulator-0.2.0rc6-macos-arm64.tar.gz
cd modbus-simulator-0.2.0rc6-macos-arm64
./start.command
```

也可双击 `start.command`。包未做 Developer ID 签名／公证，首次运行若被系统拦截，确认下载来源后在“系统设置 → 隐私与安全性”中允许运行；无需关闭 Gatekeeper。

### 自定义端口和数据目录

独立包默认启动外部监督器，由它运行单个后端。以下命令替代上述启动命令，调整 Web 管理端口和数据目录：

```bash
# Linux / macOS，在解压后的包目录运行
./modbus-simulator --port 8001 --data-dir "$HOME/modbus-data"
```

```powershell
# Windows，在解压后的包目录运行
.\modbus-simulator.exe --port 8001 --data-dir "$env:LOCALAPPDATA\ModbusSimulator"
```

使用自定义端口后访问 `http://127.0.0.1:8001`。`--version` 显示版本；常驻注册、离线维护和独立包升级见 [独立包说明](docs/standalone.md)。

## 源码启动

先安装 Python 3.12、Node.js 22.12+ 和 Git。Node.js 用于构建前端，构建完成后仅启动后端即可使用页面，不需要单独运行前端服务。获取源码：

```bash
git clone https://github.com/zhfk/modbus-slaver-simulator.git
cd modbus-slaver-simulator
```

### Linux / macOS / 云开发环境

确认 `python3 --version` 为 Python 3.12，`node --version` 至少为 22.12，然后在仓库根目录执行：

```bash
bash scripts/setup.sh
.venv/bin/python -m simulator.supervisor --port 8000
```

安装脚本创建 `.venv`、安装锁定依赖、构建前端并安装当前项目。启动命令使用外部监督器，数据默认写入系统用户目录。

### Windows PowerShell

在仓库根目录执行：

```powershell
.\scripts\build.ps1
.\.venv\Scripts\python.exe -m simulator.supervisor --port 8000
```

构建脚本创建 `.venv`、安装锁定依赖、构建前端及 wheel；上面的启动命令直接运行当前仓库的源码，不需要再安装 wheel。首次安装和构建需要下载依赖。

### 开发调试

需要直接运行后端时，用下面的命令替代监督启动，不要同时启动两份应用：

```bash
.venv/bin/python -m simulator --data-dir .data --port 8000
```

Windows 对应 `.\.venv\Scripts\python.exe -m simulator --data-dir .data --port 8000`。修改前端源码后执行 `npm --prefix frontend run build`；修改后端源码后重启应用。可选 Vite 开发流程见 [开发与验证](#开发与验证)。

## 启动后使用与多设备配置

两种启动方式均在后端就绪后，通过本机浏览器访问 `http://127.0.0.1:8000`。首次没有设备，须创建并显式启动。终端按 Ctrl+C 停止服务；关闭浏览器不会停止服务。

后端管理端口默认 8000，仅监听本机；Modbus 端口按设备独立设置，默认 1502。需要外部 PLC／SCADA 接入时，将**设备**监听 IP 设置为实际网卡地址或 `0.0.0.0`，并允许对应端口的入站访问。客户端连接运行模拟器电脑的实际 IP，不能把 `0.0.0.0` 作为目标地址。管理服务仍只允许本机访问。

1. 点击“新建设备”，选择温控模板或空设备。
2. 设置监听 IP、端口、Unit ID；配置点位或导入 Excel。
3. 点击“启动设备”，再连接外部 Modbus TCP 客户端。
4. 在点位表格中赋值、配置策略、查看趋势和真实通信记录。

多设备配置示例：

| 设备名称 | 监听地址 | Unit ID |
| --- | --- | --- |
| 从机 1# | `0.0.0.0:1502` | 1 |
| 从机 2# | `0.0.0.0:1502` | 2 |
| 从机 3# | `0.0.0.0:1502` | 3 |

外部主机通过 Unit ID 选择设备；同一 IP／端口不能重复 Unit ID。新建温控模板、空设备和编辑设备均校验此规则，冲突时提示已占用的设备并保留输入；不同 IP 或端口允许重复 Unit ID。若主机软件不支持多个 Unit ID，可以分别使用 1502、1503、1504 端口，每台都用 Unit ID 1。

`0.0.0.0` 表示监听所有 IPv4 地址，因此不能再用 `127.0.0.1` 或某个网卡 IPv4 地址另建同端口端点，即使 Unit ID 不同也会冲突；IPv6 的 `::` 与具体 IPv6 地址同理。共享端口时应为所有设备填写相同监听 IP（例如都填 `0.0.0.0`），并使用不同 Unit ID。不同具体 IP 或不同端口仍允许重复 Unit ID。

通配地址重叠校验已在当前 `main` 源码修复，已发布的 `v0.2.0rc6` 包尚未包含此修复。源码升级后，旧版保存的重叠配置仍可在页面中读取和修正；停止相关设备，把监听 IP 统一并调整 Unit ID，或改用不同端口，再保存。

不要使用 `--reload` 或多个 Web worker。内存中的寄存器是协议、管理界面与策略的唯一数据来源。“暂停策略”保留通信；“停止设备”停止该设备通信。

## 功能

- 操作采用右侧覆盖抽屉，主页面保持布局和滚动位置；存储设置展示后端实际数据目录，路径只读、可选中复制。
- 多设备、共享端点与独立 Unit ID；支持功能码 1、2、3、4、5、6、15、16；可选启用 22、23 与设备身份读取 43／14。
- 四类数据区；Bool、Int16、UInt16、Int32、UInt32、Float32、Float64；倍率、偏移、字节和寄存器顺序。
- 原子批量写入、部分多寄存器写入、外部写入权限、未配置地址和有效地址范围。
- 固定、随机、正弦、斜坡、随机游走、噪声、状态序列、联动、受限表达式、历史回放、温控与回差报警。
- 人工保持、临时覆盖、继续模拟、控制输入；设备暂停与点位保持互相独立。
- Excel 模板、稳定 ID 更新、显式设备替换、校验预览；导出配置、当前快照、历史数据和导入错误清单。
- 真实状态推送、分页筛选、批量操作、实时趋势、通信诊断、限时限量报文捕获。
- SQLite WAL 分库、容量与时间保留、快照、校验备份、日志轮换、健康监测与进程监督。

通用表达式仅支持四则运算和 `t`、`x0…x31`，不执行用户 Python。回放样本为 `[时间秒, 工程值]` 列表，序列样本为 `[持续秒数, 工程值]`。在点位编辑中设置参数与依赖。

附加参数编辑框只填写表单没有展示的参数。例如噪声的 `noise` 在上方表单填写，JSON 填写 `{"base":"fixed","value":25,"distribution":"normal"}`；删除 JSON 中的键会删除该附加参数。非法 JSON、重复的表单参数和保存失败都保留草稿，离开未保存的编辑会提示。历史采样关闭后仍可查询／导出已有数据；历史导出遵循窗口中选择的点位和时间范围。

默认上限为 16 台设备、8 个端点、全局 10000 个点位、64 个 TCP 连接、每端点最多 32 个连接。每页最多 100 个点位、实时趋势最多 4 个点位、实时页面会话最多 4 个。Excel 上传最大 20MiB，解压总量最大 100MiB；重任务并发 1、排队 2、超时 60 秒；历史单次导出最多 10000 条。达到上限会明确拒绝请求，不能据此承诺任意硬件上的相同性能。

## 架构

```text
浏览器 ── 同源 HTTP / WebSocket ──┐
PLC / SCADA ── Modbus TCP ────────┤ 单个 FastAPI / asyncio 应用
                                └─ 统一内存寄存器与策略引擎
                                   ├─ 配置写入器 → config.db
                                   ├─ 历史写入器／读取器 → telemetry.db
                                   ├─ 快照、备份与滚动日志
                                   └─ 可终止子进程 → Excel 临时文件
外部监督器 ── 健康与业务进展检查 → 有限重启应用
```

协议和界面不维护不同的当前值副本。一次批量赋值先完整校验后提交；单个策略出错保留最后值并隔离该点位。数据库使用固定归属线程及有界队列；Excel 子进程的失败、超时和取消都会回收临时文件。关闭时拒绝新工作并排空已接纳的数据库任务；工作线程未退出时保留实例锁至进程退出。

## 存储与恢复

源码监督启动和 Release 包默认使用以下数据目录，与代码、安装目录分开：

| 系统 | 默认数据目录 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\ModbusSimulator` |
| Linux | `$XDG_DATA_HOME/modbus-simulator`，未设置时为 `~/.local/share/modbus-simulator` |
| macOS | `~/Library/Application Support/ModbusSimulator` |

通过 `--data-dir` 设置本机可写目录；源码开发可使用 `--data-dir .data`。同一数据目录只能由一个应用实例使用。升级时保留数据目录，避免覆盖或删除已有配置。

| 文件 | 作用 |
| --- | --- |
| `config.db` | 事务配置和配置版本，FULL 同步 |
| `telemetry.db` | 可选历史与有界操作事件，NORMAL 同步 |
| `snapshot*.json` | 最近两份校验快照，包括原始位、策略进度和随机状态 |
| `backups/config.*.db` | 最多七份配置备份 |
| `application.log*` | 日志轮换，总量约 50MiB |
| `tmp/` | 受预算限制的导入、导出临时文件 |

历史默认关闭。启用前选择点位并检查容量估算；采样、事件和报文缓存受容量上限约束，不承诺无限保留或完整永久审计。超限丢弃会累计计数；磁盘不足会停止非必要写入并拒绝配置保存，内存读写继续。最近值的断电损失窗口取决于最近一次成功快照，不是每次写入同步落盘。

默认预算：历史库 1024MiB、配置库 64MiB、配置备份 256MiB、临时目录 256MiB，磁盘保留 512MiB；历史保留 7 天，快照间隔 60 秒、自动恢复最大年龄 24 小时。快照单份最大 32MiB、保留两份；超限时保留最近成功版本并报告错误，不写入无法恢复的文件。在“存储与恢复”中调整预算并查看当前占用。数据目录使用本机磁盘，不放在网络共享目录上；启动运行身份须有读写权限。

可选历史库损坏时保留原文件并显示故障，可靠配置和 Modbus 服务仍可使用。配置提交成功后的额外备份失败单独报告，不把已生效的修改说成回滚；历史写入恢复不会抹去尚未恢复的快照或备份错误。历史批次同时限制条数与字节，并在预算压力下提前清理，适用于较小的存储预算。

上述源码监督启动与 Release 包启动均在应用进程之外检查实际业务进展，连续失败后终止／重启，10 分钟内最多重启 3 次。源码 Windows 部署可运行 `scripts/install-startup-task.ps1`，独立包对应 `service/install-windows-task.ps1`，注册登录启动任务；无人值守开机运行时，在任务计划程序配置合适的服务账户、启动触发器及数据目录权限。Linux systemd、macOS launchd 和详细常驻配置见 [独立包说明](docs/standalone.md) 与 [部署与维护](docs/deployment.md)。

正常停止后执行维护；活动实例锁会阻止同时修改数据文件：

```bash
.venv/bin/python -m simulator.maintenance --data-dir .data compact
.venv/bin/python -m simulator.maintenance --data-dir .data restore --backup .data/backups/config.0.db
```

恢复先验证完整性、数据库版本和配置内容，保留损坏数据库及 WAL 的副本到 `recovery/`；空配置备份也可恢复。不支持的版本在替换前拒绝。历史不默认备份；空间回收需要额外磁盘空间，不能在线反复 VACUUM。启动时检测不支持的数据库版本而不覆盖版本或自动清空数据。

## wheel 部署（需 Python）与升级

构建机器运行安装脚本后，再构建发布包；顺序不能颠倒，wheel 必须包含前端与字体：

```bash
npm --prefix frontend run build
.venv/bin/python -c 'import shutil; shutil.rmtree("build", ignore_errors=True)'
.venv/bin/python -m build --no-isolation
```

Windows 使用 `scripts/build.ps1` 完成锁定依赖安装、前端构建和 Python 打包。输出为 `dist/modbus_slaver_simulator-0.2.0rc6-py3-none-any.whl`；将该文件及 `requirements.lock.txt` 复制到运行机器。运行机器仅需 Python 3.12+；安装依赖需要网络，或预先准备对应平台的离线安装包。

Linux：在 wheel 与锁定文件所在目录运行：

```bash
python3 -m venv "$HOME/modbus-simulator/venv"
"$HOME/modbus-simulator/venv/bin/python" -m pip install --constraint requirements.lock.txt ./modbus_slaver_simulator-0.2.0rc6-py3-none-any.whl
"$HOME/modbus-simulator/venv/bin/python" -m simulator.supervisor --data-dir "$HOME/.local/share/modbus-simulator" --port 8000
```

Windows PowerShell：在 wheel 与锁定文件所在目录运行：

```powershell
$AppDir = Join-Path $env:LOCALAPPDATA 'ModbusSimulatorApp'
$DataDir = Join-Path $env:LOCALAPPDATA 'ModbusSimulator'
py -3.12 -m venv "$AppDir\venv"
& "$AppDir\venv\Scripts\python.exe" -m pip install --constraint requirements.lock.txt .\modbus_slaver_simulator-0.2.0rc6-py3-none-any.whl
& "$AppDir\venv\Scripts\python.exe" -m simulator.supervisor --data-dir $DataDir --port 8000
```

发布包可从任意工作目录启动。Linux 常驻可使用 systemd，示例见 [部署与维护](docs/deployment.md)。Windows 源码部署可使用登录启动脚本；wheel 部署在任务计划程序配置上述 Python 路径、监督器参数、明确的数据目录和运行账户，执行时间设为无限，重复实例设为“不启动新实例”。无人值守开机使用启动触发器，配置无需交互登录的合适账户。监督器超出重启上限后退出，系统服务管理器不要再无限重启它。

升级流程：正常停止监督器和应用 → 保存整个数据目录的离线副本 → 安装新 wheel（重复版本使用 `pip install --force-reinstall --constraint requirements.lock.txt ...`）→ 使用原数据目录启动 → 检查健康、配置版本及设备端口。只有明确开启“自动启动”的设备才会恢复监听。失败时停机并还原原发布包与数据副本；不自动迁移未知数据库版本。维护操作与运行进程不能同时访问该数据目录。

## 常见问题

| 现象 | 检查与处理 |
| --- | --- |
| 页面提示前端未构建 | 源码部署先执行前端构建；发布包检查 wheel 包含 `simulator/static/index.html` 和资源 |
| 设备启动失败 | 检查 IP 确实属于本机、端口占用、权限、同端点 Unit ID 冲突；优先使用 1502 |
| 外部客户端连接失败 | 确认设备已启动，设备监听实际网卡，连接的是 Modbus 端口，防火墙允许入站 |
| 写入后值再次变化 | 检查写入模式；“继续模拟”会在下一策略周期覆盖，“控制输入”用于驱动其他点位 |
| 配置版本冲突 | 保留草稿，重新获取并比较当前配置，不盲目重试覆盖 |
| 存储降级或历史丢弃 | 检查健康页的磁盘余量、文件占用、队列及错误；停机后执行恢复或空间回收 |
| 数据目录已有实例 | 检查监督器／任务计划程序是否重复启动；不要删除锁文件绕过检查 |

健康接口为 `/api/health/live`、`/api/health/ready`、`/api/health`。存活／初始化成功不代表每个设备正在监听，仍须检查设备状态和存储故障。

## 开发与验证

```bash
.venv/bin/python -m pytest -q
npm --prefix frontend run build
.venv/bin/python -m build --no-isolation
```

前端可选开发：`npm --prefix frontend run dev`，API 代理到后端；应用运行以编译产物由后端提供为准。发布 wheel 包含静态资源和本地字体；安装 wheel 后运行不需要 Node.js。

浏览器验收使用真实 Chromium，先启动独立、空的数据目录以免修改开发数据：

```bash
.venv/bin/python -m simulator --port 8001 --data-dir artifacts/browser-data
# 另一个终端运行；Chromium 需存在，或设置 CHROMIUM_EXECUTABLE。
.venv/bin/python scripts/browser_check.py --url http://127.0.0.1:8001
```

持续运行测试会自己启动独立后端、使用独立数据目录、记录实际时间与资源指标，出现阈值超限则以非零状态退出。输出目录不可复用已有数据：

```bash
.venv/bin/python scripts/soak.py --stages 60 --points 1000 --clients 4 --output artifacts/soak-smoke
.venv/bin/python scripts/soak.py --stages 86400,259200 --points 1000 --clients 4 --output artifacts/soak-release
```

24 小时通过后才执行 72 小时阶段，结果持续写入各阶段 `report.json`，最终汇总为 `summary.json`。短时通过不等于耐久验收通过。详细证据和未完成项见 [验收记录](docs/acceptance.md)，设计依据见 [AGENTS.md](AGENTS.md)。

为了使长测试不受后续源码修改影响，可以复制当前构建产物和后端代码后独立运行：

```bash
python3 -m venv artifacts/soak-python
artifacts/soak-python/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python scripts/launch_soak.py --python artifacts/soak-python/bin/python --output artifacts/soak-review
```

此命令返回已启动进程的信息，不能据此判断通过。检查 `artifacts/soak-review/results/stage-1/report.json`，随后是 `stage-2/report.json` 和最终 `summary.json`；启动日志在 `runner.log`。已有该目录时不要重复运行命令，应先检查现有进程和报告；新运行使用新的输出目录。测试需要云机器连续运行 96 小时，环境关闭、重启或发布中断进程后必须重新执行受影响阶段，不能累计中断前后的时长。源码更新后应重新运行对应版本，报告中的源码指纹必须匹配。
