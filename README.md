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

构建脚本依次执行回归测试、PyInstaller 单文件打包和隐藏自检；自检通过后更新 `release/` 并生成 ZIP。自检只用内存模拟驱动，验证 DLL 文件存在，不加载它。公开源码中的 `config.example.ini` 会作为无个人配置时的构建默认配置。已有发行配置会保留，已有 EXE/ZIP 会备份。

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

8 个关键模块包含 16 处带编号的 Yukki / ssyb 注释标记，不改变程序运行。初次整理的核心源码 SHA-256 记录在 [SOURCE_PROVENANCE.json](SOURCE_PROVENANCE.json)，核验方法见 [docs/PROVENANCE.md](docs/PROVENANCE.md)。

复制或分发时请保留适用的版权与许可声明。来源标记用于辅助追溯；本项目没有增加隐蔽运行逻辑或联网追踪。

仓库通过 `.gitignore` 白名单只纳入已审查源码、测试、构建文件和文档；个人配置、驱动、素材、旧程序及分析目录保留在本地。
