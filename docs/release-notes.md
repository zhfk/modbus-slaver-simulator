# 独立预发布包 0.2.0rc3

解压即可运行，包含 Python 3.12、应用依赖、Vue 前端资源与离线字体。用户不需要安装 Python、Node.js、数据库服务器或另一个前端服务。默认启动进程外监督器和单个应用子进程。

## 下载与启动

| 平台 | 下载文件 | 启动 |
| --- | --- | --- |
| Linux x86_64 | `modbus-simulator-0.2.0rc3-linux-x86_64.tar.gz` | 解压后执行 `./start.sh` |
| Windows x64 | `modbus-simulator-0.2.0rc3-windows-x86_64.zip` | 解压后双击 `start.cmd` |
| macOS Intel | `modbus-simulator-0.2.0rc3-macos-x86_64.tar.gz` | 解压后执行 `./start.command` |
| macOS Apple Silicon | `modbus-simulator-0.2.0rc3-macos-arm64.tar.gz` | 解压后执行 `./start.command` |

打开本机浏览器的 `http://127.0.0.1:8000`。使用 `modbus-simulator --port 8001 --data-dir <目录>` 调整端口和数据目录；Windows 可执行文件带 `.exe` 后缀。配置和快照默认存入系统用户数据目录，不写进安装包。

可以创建“从机 1#、2#、3#…”；默认最多 16 台设备／8 个端点／10000 个点位。共用一个 IP 和端口时选择不同 Unit ID，各台设备的点位、策略、值和启停独立；需要时也可选择不同端口。外部主机通过设备监听地址与 Modbus 端口接入，管理页面仅在本机开放。

此候选版本修复 Windows 备份文件被 SQLite 连接占用而无法轮换的问题，备份替换前显式释放文件句柄。

## 平台要求和验证范围

- Linux 官方构建使用 Ubuntu 22.04 x86_64，目标为 glibc 2.35+ 系统；Alpine/musl 不适用。云工作区本机构建可能使用更高 glibc，以各包 `BUILD-INFO.json` 为准。
- Windows 原生构建使用 Windows Server 2022 x64，面向 Windows 10/11 x64；需要目标桌面 Windows 实机进一步验收。
- macOS 两种架构分别原生构建。构建和测试使用 macOS 15；更老系统尚未验证。包未做 Developer ID 签名／公证，首次启动可能需要在系统隐私与安全设置中允许来源可信的程序；不要求关闭 Gatekeeper。
- 每个原生包须实际通过页面、WebSocket、三台设备共享端点读写、独立停止、Excel 子进程导入／导出及离线维护检查，全部平台成功后才上传 GitHub 预发布。
- 上一版本耐久测试在 28665.328 秒后因约 504.8ms 调度延迟超过 200ms 门槛失败。24 小时及后续 72 小时未通过；此包为功能预发布，不能标为已验证长期稳定版本。跨机器 LAN、防火墙、目标 Windows/macOS 常驻运行仍需目标环境验收。

SHA256SUMS.txt 提供下载完整性检查；各包包含构建信息、依赖版本、许可证及服务注册模板。常驻配置、备份恢复和升级见包内 `docs/deployment.md` 与 `docs/standalone.md`。
