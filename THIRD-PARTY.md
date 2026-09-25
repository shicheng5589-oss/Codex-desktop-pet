# 第三方组件与授权

本仓库自己的代码是 MIT（见 LICENSE），但仓库里还带着别人的东西，发布/使用时请注意：

| 位置 | 组件 | 版本 | 授权 | 说明 |
| --- | --- | --- | --- | --- |
| `web/lib/pixi-v6.min.js` | pixi.js | 6.5.10 | MIT | 文件头保留了版权声明 |
| `web/lib/pixi.min.js` | PixiJS | v7 系列（备用，程序默认不加载） | MIT | 同上 |
| `web/lib/pixi-live2d-display-cubism4.min.js`<br>`web/lib/pixi-live2d-index.min.js` | pixi-live2d-display | 打包版 | MIT | 让 pixi 能画 Live2D Cubism 4 模型 |
| `web/lib/live2dcubismcore.min.js` | **Live2D Cubism Core** | — | **Live2D 公司专有授权**<br>（文件头写的是 “Redistributable Code”） | 属于 Live2D Cubism SDK。可以随本程序一起分发，但**不能单独拿去卖/改许可**，商用有额外条款，请阅读 Live2D 官网的 SDK 许可：<https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_cn.html> |
| `vendor/` | pythonnet 3.1.0 / clr-loader 0.3.1 / cffi 2.1.1 / pycparser 3.0 / typing_extensions 4.16.0 / Pillow 12.3.0 / pywebview 6.2.1 / bottle 0.13.4 | 见 `vendor/*.dist-info/` | MIT / BSD-3 / PSF / MIT-CMU 等 | 每个包自己的 LICENSE 都在 `vendor/*.dist-info/licenses/` 里，保留了原文 |
| `vendor/webview/lib/Microsoft.Web.WebView2.*.dll` | Microsoft WebView2 SDK（.NET 部分） | — | 微软的许可条款 | 用于在 WinForms 窗口里嵌网页 |
| 运行期 | **Microsoft Edge WebView2 Runtime** | 由系统/Edge 提供 | 微软 | 不随仓库分发，需要用户机器上已有（Win11 自带，Win10 一般随 Edge 装好） |
| `web/model/` | **Live2D 模型（角色形象）** | — | **取决于模型作者，通常不是本仓库的许可** | 见下方说明 |

## 关于 `web/model/`（重要）

模型文件（`c_0120.moc3`、两张贴图、动作与表情 json）是这个桌宠的“皮”，
它有自己的作者和授权，**跟代码的 MIT 是两回事**：

* 如果模型是你自己做的 —— 建议在 `web/model/README.md` 里写明作者、授权方式
  （例如“仅限个人使用，不得二次分发”或 CC-BY 等），别人 remix 时才不会踩坑。
* 如果模型来自别处（买的、别人分享的、VTube Studio 里下载的）——
  **不要**直接把它随仓库发到 GitHub，除非作者明确允许再分发。
  这种时候建议：仓库里**只放代码**，在 README 里告诉使用者
  “把你的 Cubism 4 模型放进 `web/model/`，并按下文改一下 `settings.json`”。

把模型从仓库里去掉的做法见 README 的「换成你自己的 Live2D 模型」一节。
