# YanziHelper

Windows 上基于 Python / Tkinter 的按键流程助手，提供上元、翔舞等技能的快捷键配置、选人流程和回雪衔接。

维护署名：**Yukki / ssyb**。原有署名与参考来源见 [NOTICE.md](NOTICE.md)。源码采用 [MIT License](LICENSE)。

## 功能

- 上元、翔舞支持普通触发键和最低血量专用触发键。
- 普通键支持不选人、选最低血量、选盟友，以及盟友 HOT 后切最低血量接回雪。
- 短按执行一次；长按重复；停止或切换流程会取消尚未提交的按键。
- 可选前台窗口保护，切出后停止，切回须手动启用。
- 配置校验与导入导出、单工作线程输出、驱动故障恢复和独立退出监护。

程序只发送配置的快捷键，不读取实际血量，也不判断技能是否生效。具体操作见 [使用说明](使用说明.txt)。

## 环境与启动

支持 Windows，建议使用 **64 位 Python 3.12（含 Tkinter）**。运行代码只使用 Python 标准库；构建 EXE 才需要 PyInstaller。

在源码目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m tkinter
if (-not (Test-Path -LiteralPath config.ini)) { Copy-Item config.example.ini config.ini }
```

第二条命令用于确认 Tkinter 可用。已有 `config.ini` 时请保留自己的配置。示例配置来自程序默认值，启用前应核对游戏内绑定。

正常启动还需要自行取得并安装匹配的 **DD HID 63340 驱动**，将 `ddhid.63340.dll` 放到源码目录，然后运行：

```powershell
.\.venv\Scripts\python.exe YanziHelper.py
```

正常入口会请求管理员权限并管理 DD 虚拟设备。图标 `24983.png` 和提示音 `start.wav` / `stop.wav` 在源码运行时可选，缺失时不显示图标或播放音效。

DD DLL 与驱动不属于本项目的 MIT 授权，也未随仓库提供。官方资料中未找到禁止调用方应用开源的明确条款，但尚未确认驱动本体的再分发授权；核查范围与来源见 [DD 许可核查](docs/DD-LICENSE.md)。

DD 63340 可能只保存禁用设置，并要求重启后才停止内核驱动。看到 `restart_required` 时，关闭窗口不代表驱动已停止；状态记录在程序目录的 `dd-driver-status.json`。尚未验证与其他游戏的兼容性。

## 开发与测试

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

单元测试使用模拟驱动和设备后端；UI 测试不安装全局键盘钩子，也不加载真实 DD DLL。完整测试需要 Windows 与 Tkinter，源码测试无需自备 DLL 或音效。Windows / Python 3.12 的自动测试工作流示例见 [docs/windows-tests.yml.example](docs/windows-tests.yml.example)。

## 构建 EXE

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\build_release.ps1 -PythonExe .\.venv\Scripts\python.exe
```

构建前需自行提供 `ddhid.63340.dll`、`start.wav`、`stop.wav`；音效必须为非空 PCM WAV。图标可选。这些文件因许可或来源未确认而不随仓库分发。请只打包有权再分发的资源。

构建脚本先校验实际配置，再执行回归测试、PyInstaller 单文件打包和隐藏自检。自检会读取并验证内嵌默认配置，使用内存模拟驱动，检查 DLL 文件存在但不加载它，也不修改个人配置。自检报告记录 EXE 的 SHA-256，包装时核对，防止误用旧报告。

完整候选发行目录和 ZIP 验证通过后才更新正式产物；提交失败会尝试恢复旧版本。公开源码中的 `config.example.ini` 会作为无个人配置时的构建默认配置。已有发行配置会保留并校验，旧发行文件和 ZIP 会备份。

## 可选：构建含驱动版的一键启动器

[启动器源码](tools/driver_kit/Launcher.cs)适用于 Windows x64。用户解压后双击 `启动助手.exe` 并允许管理员授权。已有匹配的 DD HID 63340 设备时，启动器直接打开助手；缺失时先运行原厂安装器，重新检测到匹配设备后才打开助手。Windows 若提示重启，请手动重启后再试。

先按上节构建正式版，再准备一个只含同版原厂 `ddc.exe`、`ddhid63340.inf`、`ddhid63340.sys`、`ddhid63340.cat` 的目录。下面的第一条命令可独立运行，不需要驱动或发行文件；第二条命令用本机的 .NET Framework 编译器生成与该正式版 EXE 匹配的启动器。

```powershell
.\tools\driver_kit\build_launcher.ps1 -TestOnly
.\tools\driver_kit\build_launcher.ps1 -DriverDirectory .\vendor\ddhid63340 -ReleaseDirectory .\release
```

制作本地使用包时，在新目录解压标准 ZIP，将生成的 `启动助手.exe` 放在 `YanziHelper.exe` 旁，把上述四个原厂文件放入同级的 `driver\` 子目录，再压缩整个目录。更换助手 EXE 后须重新构建启动器。仓库不提供第三方驱动二进制，来源及许可核查见[相关说明](docs/DD-LICENSE.md)。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `YanziHelper.py` | Tk 界面、配置交互与事件队列 |
| `app_config.py` | 配置校验、热键集合与步骤计划 |
| `macro_engine.py` | 串行输出、取消、停止与故障恢复 |
| `keyboard_hook.py` | 物理按键状态机与 Windows 钩子 |
| `foreground_guard.py` | 前台窗口身份与失焦锁定 |
| `pydd.py` | DD 调用、按键释放与 DLL 生命周期 |
| `dd_device.py` / `dd_lifecycle.py` | 设备身份校验与独立监护 |
| `app_selftest.py` | 打包后的隐藏模拟自检 |

## 署名与来源核对

8 个关键模块包含 16 处带编号的 Yukki / ssyb 注释标记，不改变程序运行。首次及本次核心源码的 SHA-256 快照与核验方法见[来源标记与版本指纹](docs/PROVENANCE.md)。

复制或分发时请保留适用的版权与许可声明。来源标记用于辅助追溯；本项目没有增加隐蔽运行逻辑或联网追踪。

仓库通过 `.gitignore` 白名单只纳入已审查源码、测试、构建文件和文档；个人配置、驱动、素材、旧程序及分析目录保留在本地。
