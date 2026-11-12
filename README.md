# 轨道协作 Orbit

**让多个 Agent 带着知识协作，让每次任务留下可复核的经验。**

Orbit 是一个独立的本地多 Agent 协作平台：任务图与角色分工、RAG 知识检索、Obsidian Markdown 资料库、后台「做梦」记忆整理、独立复核和跨任务反馈形成完整流程。它不是 agent-autoloop 的改名发行版；后者仅是可选的事件入口依赖，核心任务和记忆功能可以独立运行。

首个公开版本 **v0.1.0**。中文界面，Python 标准库后端，原生 JavaScript / Three.js 前端。源码使用 MIT 许可。

## 能做什么

- 多角色任务：总控、研究、执行、审查；依赖调度、并行、暂停、取消、有限重试和中断恢复。
- 可追溯检索：从允许的 Markdown 目录检索片段，记录来源、行号、指纹、传入情况和产物引用。
- 做梦记忆：任务终态后提炼候选经验，由独立复核决定是否启用；新任务按项目召回，证据变化则停用。
- 实际反馈：有效 / 无改善 / 有害 / 未验证，与内部验收分别记录。
- 资料与产物：接入自己的项目索引和 Obsidian 库；任务产物、SQLite 状态和经验保存在本地。
- 可选执行器：Codex、DeepSeek Harness、WorkBuddy / CodeBuddy；Grok、豆包提供人工复制回填。
- 可选事件入口：安装 agent-autoloop 后启用固定项目规则、事件去重与同任务接续。

当前 RAG 使用确定性的词面匹配和有界片段检索，**不是向量数据库或全库语义搜索**。做梦记忆是证据驱动的提炼与复核，不是模型训练。

## 下载安装

首发面向 **macOS / Linux、Python 3.9+**。macOS 已实际验收；已配置 Linux CI 隔离测试，运行结果见仓库 Actions，真实执行器和 WSL2 需要各环境验证。Windows 原生暂不支持。网页不需要 Node；可选 Agent / 事件组件需要 Node.js 24+。

在 GitHub 点击 **Code → Download ZIP** 并解压，或执行：

```sh
git clone https://github.com/zhouxinsen77-ux/orbit-collab.git
cd orbit-collab
python3 install.py init
python3 install.py doctor
python3 launcher.py start --open
```

浏览器打开 <http://127.0.0.1:8765>。macOS 也可在解压目录双击 `start.command`。首次启动只加载原创示例项目和六篇示例知识，不需要密钥，也不会调用模型。已安装执行器时会检查版本与登录状态。要执行任务，请接入至少一个自动执行器。

```sh
python3 launcher.py status
python3 launcher.py stop
```

若端口已被占用，修改 `orbit.json` 的 `port`，例如 `8767`，再启动。启动器会拒绝接管其他 Orbit 实例。

## 接入第一个 Agent

最简单的起点是已安装、已登录的 Codex CLI。按 [官方安装文档](https://learn.chatgpt.com/docs/cli) 安装，然后：

```sh
codex login
python3 install.py doctor
```

默认从 PATH 查找 `codex`；也可在 `orbit.json` 设置 `codex_bin` 的完整路径。CLI 必须支持 `--ignore-user-config`、`--ignore-rules`、`--ephemeral` 和 `--output-schema`，不支持时请升级。Orbit 使用独立任务工作目录、workspace-write 沙箱和结构化输出，不加载用户的全局 MCP / hooks 配置。参见 [官方命令说明](https://learn.chatgpt.com/docs/cli/reference)。

打开「新建协作任务」，选择 **Orbit 入门示例**，粘贴 [示例任务](examples/project/PROJECT.md)。默认所有角色跟随主执行器；需要时再为各角色选择不同 Agent。模型执行和后台经验整理使用各提供商的账号、配额或计费，开源软件不包含模型服务额度。

可选组件按需安装，不必全部安装：

```sh
python3 install.py install harness
# 将 .env.example 复制为 .env，填入自己的 DEEPSEEK_API_KEY。

python3 install.py install workbuddy
python3 codebuddy_adapter.py login
python3 codebuddy_adapter.py verify

python3 install.py install autoloop
python3 autoloop_service.py start
```

Harness 固定 `@deepseek-ai/dsh@0.2.2-rc.1`；WorkBuddy 固定 `@tencent-ai/codebuddy-code@2.181.0`；Autoloop 固定 v1.7.0 对应提交，安装时保留上游许可。WorkBuddy 接口仅提供文本任务，系统工具关闭；代码执行选 Codex / Harness。Grok / 豆包仍需人工在客户端执行并回填。

## 接入自己的项目与 Obsidian

配置示例见 [orbit.example.json](orbit.example.json)。`orbit.json` 是本地配置，不进入 Git；可用 `ORBIT_CONFIG` 指向其他配置文件，或用 `ORBIT_*` 环境变量覆盖，详见 [配置说明](docs/CONFIGURATION.md)。

1. 将 `examples/projects.json` 复制为根目录 `projects.json`，填写自己的项目；项目 `root` 相对索引文件所在目录解析，也可用绝对路径。
2. 将 `orbit.json` 的 `project_index` 改为 `projects.json`，`vault` 改为你明确允许 Agent 使用的 Markdown / Obsidian 资料目录。
3. 重启平台。默认检索 `知识/概念`、`知识/综合分析`、`知识/来源摘要`，最多600篇、非递归读取；其他目录不自动纳入。

**选中的资料片段会发送给你选择的模型服务。** 建议先用单独的可供 Agent 使用的精选库；私人标记与来源过滤不能识别所有敏感事实。完整用户库、聊天、密钥和本地配置均不随本仓库分发。详细范围见 [隐私与安全](SECURITY.md)。

## 验证与开发

```sh
python3 -m unittest discover -v
python3 scripts/release_audit.py --tracked
```

隔离测试不调用模型。发布前还应从干净源码解压启动，验证页面、知识检索和所选执行器；真实模型能力不由模拟测试代替。验证范围与限制见 [发布验收](docs/VALIDATION.md)。

程序只监听 `127.0.0.1`，不适合作为公网多用户服务直接部署。没有账号隔离、团队权限或远程访问认证。浏览器关闭不影响后台任务；停止服务会保留状态，下次启动需明确重试中断任务。

## 项目结构与许可

`server.py`：编排 / API / 持久化；`knowledge_loop.py`：检索与经验；`adapters.py`、`codebuddy_adapter.py`：执行器；`web/`：工作台；`autoloop_*`：可选事件桥接；`data/`：运行数据（不发布）；`examples/`：原创合成入门资料。

Orbit 自有代码使用 [MIT](LICENSE)。第三方组件保留独立版权和许可，见 [THIRD_PARTY.md](THIRD_PARTY.md)。欢迎通过 Issues 报告可复现问题、通过 Pull Requests 贡献改进；提交前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

**English:** Orbit is an independent, local-first multi-agent workbench with task graphs, Markdown/Obsidian retrieval, evidence-based memory consolidation and review. Start with Python 3.9+ on macOS/Linux using the commands above. The UI and primary documentation are currently in Chinese. Agent credentials and private knowledge are never included in the distribution. Autoloop is an optional event adapter, not the identity of this project.
