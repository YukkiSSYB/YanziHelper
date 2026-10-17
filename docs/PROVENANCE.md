# 来源标记与版本指纹

本次整理为 Yukki / ssyb 加入静态来源标记。它们全部是 Python 注释，不改函数名、变量名、返回值、按键序列或线程行为，也不声明原有代码均为该署名原创。

| 模块 | 文件头编号 | 关键实现编号 |
| --- | --- | --- |
| app_config.py | CFG-01 | CFG-02：选人与技能步骤计划 |
| macro_engine.py | MAC-01 | MAC-02：提交步骤与输出锁 |
| keyboard_hook.py | KEY-01 | KEY-02：首次按下决定整个按住周期 |
| foreground_guard.py | FGD-01 | FGD-02：失焦状态保持到明确重置 |
| dd_device.py | DEV-01 | DEV-02：设备身份校验 |
| dd_lifecycle.py | LIFE-01 | LIFE-02：进程身份与监护清理 |
| pydd.py | DD-01 | DD-02：按下/抬起与 DLL 关闭同步 |
| YanziHelper.py | UI-01 | UI-02：配置对话框期间暂停总开关 |

`SOURCE_PROVENANCE.json` 记录首次整理时 9 个运行模块的文件名与 SHA-256。生成时间只是这份快照的生成时间，不是原始创作时间。后续修改后可通过 Git 历史回到该快照核对；新版本需要建立新的快照记录。

在仓库根目录用 PowerShell 验证所有文件：

```powershell
$snapshot = Get-Content SOURCE_PROVENANCE.json -Raw -Encoding UTF8 | ConvertFrom-Json
$mismatches = @($snapshot.files | Where-Object {
    -not (Test-Path -LiteralPath $_.path -PathType Leaf) -or
    (Get-FileHash -LiteralPath $_.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $_.sha256
})
if ($mismatches.Count) { throw '来源快照与当前文件不同，请核对 Git 版本和换行设置。' }
'来源快照核验通过'
```

`.gitattributes` 将 Python 源码统一为 LF，以便检出后仍能核验哈希。公开仓库首次接收提交的时间、提交内容及独立保存的历史记录可帮助核对某版本何时已公开存在；本地 Git 作者时间、注释和自行生成的哈希均不能单独证明最早创作权属。
