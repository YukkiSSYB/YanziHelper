# 来源标记与版本指纹

首次整理为 Yukki / ssyb 加入了以下静态来源标记。它们全部是 Python 注释，不改函数名、变量名、返回值、按键序列或线程行为，也不声明原有代码均为该署名原创。

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

两份快照均记录同一组 9 个运行模块（上表 8 个模块及 `app_selftest.py`）。路径相对于仓库根目录，SHA-256 针对源码文件的原始字节；Python 源码按 [.gitattributes](../.gitattributes) 使用 LF，与 Git 保存的文件内容一致。它们不记录 EXE、ZIP 或第三方驱动的指纹。

| 版本 | 快照 | 核验对象 |
| --- | --- | --- |
| 首次源码发布 | [SOURCE_PROVENANCE.json](../SOURCE_PROVENANCE.json) | 首次提交 `86d6e1271d97d7fc6539da097cfd711d915da9b1` 中的源码 |
| 2026-11-15 更新 | [SOURCE_PROVENANCE-2026-11-15.json](SOURCE_PROVENANCE-2026-11-15.json) | 本次更新的源码；本版检出后可直接按相对路径核验 |

在仓库根目录用 PowerShell 运行以下示例（需要 Python 3 和 Git）。它从 Git 历史读取首次版本，从工作目录读取本次版本；不会切换分支或修改文件。

```powershell
@'
import hashlib, json, subprocess
from pathlib import Path

def verify(snapshot, revision=None):
    def read(path):
        if revision is None:
            return Path(path).read_bytes()
        return subprocess.check_output(["git", "cat-file", "blob", f"{revision}:{path}"])

    files = json.loads(read(snapshot))["files"]
    for file in files:
        path = file["path"]
        actual = hashlib.sha256(read(path)).hexdigest()
        if actual != file["sha256"]:
            raise SystemExit(f"SHA-256 mismatch: {snapshot}: {path}")
    print(f"OK: {snapshot} ({len(files)} files)")

verify("SOURCE_PROVENANCE.json", "86d6e1271d97d7fc6539da097cfd711d915da9b1")
verify("docs/SOURCE_PROVENANCE-2026-11-15.json")
'@ | python -
if ($LASTEXITCODE -ne 0) { throw '来源快照核验失败。' }
```

今后源码改动后，可给第二个 `verify()` 同样传入 2026-11-15 更新对应的 Git 提交，核验该提交的快照及源码，而不依赖当时的工作目录。新版本另存新快照，不覆盖旧记录。

快照时间是生成指纹的时间，不是原始创作时间。公开仓库首次接收提交的时间、提交内容及独立保存的历史记录可帮助核对某版本何时已公开存在；本地 Git 作者时间、注释和自行生成的哈希均不能单独证明最早创作权属。
