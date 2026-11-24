# 从源码运行与打包

使用 Windows x64 和带 Tkinter 的 64 位 Python 3.12 或更新版本。以下命令从仓库根目录的 PowerShell 执行。

## 安装依赖和运行测试

```powershell
py -m venv .venv
$pythonExe = '.\.venv\Scripts\python.exe'
& $pythonExe -m pip install -r requirements.txt
& $pythonExe -m tkinter
& $pythonExe -B -m unittest discover -s tests -v
```

关闭 Tkinter 测试窗口后，终端会继续运行单元测试。测试使用模拟的输入和设备后端，在全新源码检出中即可执行。仓库提供 [Windows 测试工作流示例](windows-tests.yml.example)。

## 运行源码

安装与程序匹配的 DD HID 63340 x64 驱动。将从可信来源取得的 `ddhid.63340.dll` 放在仓库根目录，再运行下面的命令。允许 Windows 管理员授权后，助手会管理匹配的虚拟设备。

```powershell
& $pythonExe YanziHelper.py
```

`assets/` 包含随源码提供的界面素材。`24983.png` 窗口图标及 `start.wav`、`stop.wav` 在运行源码时为可选文件。配置保存在同目录的 `config.ini`，可复制 [config.example.ini](../config.example.ini) 作为起点。DD 组件的来源和授权范围见 [DD 许可核查](DD-LICENSE.md)。

## 构建标准包

在仓库根目录准备匹配的 `ddhid.63340.dll`，以及有权使用的 `start.wav` 和 `stop.wav`。两个音效必须是非空 PCM WAV。`24983.png` 窗口图标仍为可选文件。

```powershell
& $pythonExe -m pip install -r requirements-dev.txt
.\build_release.ps1 -PythonExe $pythonExe
```

构建脚本先校验配置、运行单元测试，再打包 EXE 并执行模拟自检。通过后会更新 `release/` 和 `YanziHelper-新版.zip`。构建优先使用根目录的 `config.ini`，也可在只有 `config.example.ini` 的情况下构建。已有的 `release/config.ini` 会作为发行包配置保留。标准包适用于已装好匹配驱动的电脑。

## 构建精简含驱动包

沿用标准构建所需的依赖和资源。另外将同一版本的原厂 `ddc.exe`、`ddhid63340.sys`、`ddhid63340.inf` 和 `ddhid63340.cat` 放在一个目录中。下例使用 `vendor/ddhid63340`。

```powershell
$candidate = .\build_release.ps1 -CandidateOnly -PythonExe $pythonExe |
    Where-Object FullName |
    Select-Object -Last 1
$candidateDirectory = [IO.Path]::GetDirectoryName($candidate.FullName)
& $pythonExe -B .\tools\package_release.py --minimal-driver-candidate $candidateDirectory --driver-directory .\vendor\ddhid63340
```

通过自检和文件校验后，脚本会生成 `YanziHelper-精简含驱动版-x64.zip`。完整解压后双击 `YanziHelper.exe`。缺少匹配设备时，助手会先询问，获得确认后调用原厂安装程序。检测到设备后继续启动。如果 Windows 或原厂安装程序要求重启，重启后再打开助手。

## 构建独立启动器

先构建标准包，再运行下列命令。第一条使用模拟后端测试启动器，第二条使用 Windows 自带的 .NET Framework C# 编译器生成与该发行 EXE 匹配的 `启动助手.exe`。

```powershell
.\tools\driver_kit\build_launcher.ps1 -TestOnly
.\tools\driver_kit\build_launcher.ps1 -DriverDirectory .\vendor\ddhid63340 -ReleaseDirectory .\release
```

完整解压标准包，将生成的 `启动助手.exe` 放在 `YanziHelper.exe` 旁，并将四个原厂文件放入同级的 `driver/` 文件夹。每次更换助手 EXE 时都要重新构建匹配的启动器。
