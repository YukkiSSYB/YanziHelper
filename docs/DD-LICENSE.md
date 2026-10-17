# DD 许可核查

核查日期：2026-10-18（北京时间）。本记录说明公开资料与本地材料中查到了什么，不代替供应方针对具体版本、下载或购买渠道给出的协议。

## 结论与发布范围

已查资料中未找到“调用 DD DLL 的应用不得开源”的明确条款；同样，没有找到可将 DD DLL 或内核驱动按 MIT 再许可的授权。本次仅公开应用源码与调用层，DD DLL、驱动文件及旧版 EXE 均不随本次发布提交，也不作为 Release 或打包后的 EXE 上传。

本项目 MIT 只覆盖有权许可的应用代码。`pydd.py` 参考的 Python 包装器采用 MIT，不表示它所调用的 DD 驱动也是 MIT。

## 官方资料

- [DD 官网](https://www.ddxoft.com/)直接链接到 `github.com/ddxoft`，页面也说明 `pbxoft.com == ddxoft.com`。
- [官方仓库](https://github.com/ddxoft/master)在核查时的 HEAD 为 [`f788622778993fd4255175370bdc01a420e5d25b`](https://github.com/ddxoft/master/commit/f788622778993fd4255175370bdc01a420e5d25b)，根目录列出 README 与 `2026.DD.EV.HVCI.63xxx.7z`，未列出 LICENSE 文件；GitHub API 的 `license` 为 `null`。
- [该版本的 README](https://github.com/ddxoft/master/blob/f788622778993fd4255175370bdc01a420e5d25b/README.md)说明免费版加载时会联网验证，并提供联系渠道。该说明没有提供调用方必须闭源的要求，也没有明确给出驱动本体的 MIT 等开源授权。
- 只读检查了[该版本的官方压缩包](https://github.com/ddxoft/master/blob/f788622778993fd4255175370bdc01a420e5d25b/2026.DD.EV.HVCI.63xxx.7z)：包含 HID 63340 的 DLL、SYS、INF、CAT、安装脚本和示例。未发现独立 LICENSE、EULA 或 README 文件；所查示例源码、脚本和资源文本也未发现禁止调用方应用开源或明确允许驱动再分发的条款。未运行包内程序。

这里的 `license: null` 只表示 GitHub 未识别到仓库许可证，不能单凭这个字段判断全部权利。公开可下载也不能作为按 MIT 再许可的依据；参见 [GitHub 的仓库许可说明](https://docs.github.com/zh/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)。

## 本地版本

本地适配文件为 `ddhid.63340.dll`，版本资源标为 `www.ddxoft.com`、`ddhid63340`、`ddxoft(R)`。其 SHA-256 为：

```text
01e8db6893cf79e9e7aa3afbee76bea6c4220c4d1a2c63bc2e5b7c109fdb831e
```

在项目内的说明文件、旧 ZIP、已解包内容及该 DLL 的可读字符串中，未找到适用于此文件的明确许可协议。元数据和数字签名只帮助识别来源，不能确认再分发授权。本次未运行 DD 文件。

本地 DLL 与上述官方包中的 `2.hid/ddhid.63340.dll` 字节一致，SHA-256 相同。这将本次包内检查对应到了当前项目适配的二进制版本。

## 未确认的范围

已核对当前官方包，但尚未取得本地文件在最初下载、安装或购买时可能另行适用的协议；定制版、付费版或其他版本的条款也未确认。对具体版本的使用和再分发范围，应以其适用协议或供应方明确授权为准。因此，本次发布保持源码与第三方 DD 二进制分离。
