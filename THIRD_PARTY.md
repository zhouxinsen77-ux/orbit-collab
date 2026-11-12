# 第三方组件

Orbit 是独立项目。自有工作台、任务编排、检索、记忆与适配代码适用根目录MIT许可证；下列组件保持各自许可，不因集成而改变作者归属。

| 组件 | 版本 | 分发方式 | 来源与许可 |
| --- | --- | --- | --- |
| Three.js | 0.186.0 | 选定浏览器文件随源码分发 | [upstream](https://github.com/mrdoob/three)，MIT；完整条款见 `web/vendor/THREE-LICENSE.txt` |
| agent-autoloop | v1.7.0 / `fade2cee76bd91ed38e889b9a91276fd78e90625` | 用户按需下载，未内嵌源码或状态 | [upstream](https://github.com/KKKKhazix/agent-autoloop)，保留上游MIT |
| DeepSeek Harness | @deepseek-ai/dsh 0.2.2-rc.1 | npm按需安装 | [upstream](https://github.com/deepseek-ai/deepseek-harness)，以安装包LICENSE及服务条款为准 |
| WorkBuddy / CodeBuddy | @tencent-ai/codebuddy-code 2.181.0 | npm按需安装 | [官方文档](https://www.workbuddy.ai/docs/cli/headless)，以安装包许可及服务条款为准 |
| Codex CLI | 用户安装的兼容版本 | 复用用户安装与登录 | [官方文档](https://learn.chatgpt.com/docs/cli/reference)，CLI及模型服务分别遵循其许可/条款 |

网页无远程CDN依赖。Three.js文件来源、大小和SHA-256保存在 `web/vendor/manifest.json`。历史设计预览使用的GSAP未包含在本发行版。示例知识库为本项目新建的合成内容，不复制个人库中的来源摘要或私人原文。
