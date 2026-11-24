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

前两份快照记录上表 8 个模块及 `app_selftest.py`。2026-11-24 快照同时记录新版界面、键位录入、DD 安装入口和生命周期检查模块。路径相对于仓库根目录，SHA-256 按 Git 保存的文件字节计算。Python 源码按 [.gitattributes](../.gitattributes) 使用 LF。

| 版本 | 快照 | 核验对象 |
| --- | --- | --- |
| 首次源码发布 | [SOURCE_PROVENANCE.json](../SOURCE_PROVENANCE.json) | 首次提交 `86d6e1271d97d7fc6539da097cfd711d915da9b1` 中的源码 |
| 2026-11-15 更新 | [SOURCE_PROVENANCE-2026-11-15.json](SOURCE_PROVENANCE-2026-11-15.json) | 提交 `9feb5589e1a6fdaeda505e6e628c0f7f3fb0f39f` 中的源码 |
| 2026-11-24 更新 | [SOURCE_PROVENANCE-2026-11-24.json](SOURCE_PROVENANCE-2026-11-24.json) | 本版检出后的源码 |

在仓库根目录用 PowerShell 运行以下示例（需要 Python 3 和 Git）。示例从 Git 历史核验前两版，从工作目录核验本版。

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
verify("docs/SOURCE_PROVENANCE-2026-11-15.json", "9feb5589e1a6fdaeda505e6e628c0f7f3fb0f39f")
verify("docs/SOURCE_PROVENANCE-2026-11-24.json")
'@ | python -
if ($LASTEXITCODE -ne 0) { throw '来源快照核验失败。' }
```

后续可给最后一个 `verify()` 传入 2026-11-24 更新对应的 Git 提交，核验该提交中的快照和源码。新版本另存一份快照。

快照时间是生成指纹的时间，不是原始创作时间。公开仓库首次接收提交的时间、提交内容及独立保存的历史记录可帮助核对某版本何时已公开存在；本地 Git 作者时间、注释和自行生成的哈希均不能单独证明最早创作权属。
