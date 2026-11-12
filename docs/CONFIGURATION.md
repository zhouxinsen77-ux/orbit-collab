# 配置与数据

`python3 install.py init` 只在不存在时创建 `orbit.json`，不会覆盖已有配置。配置优先级：`ORBIT_*` 环境变量 → JSON字段 → 默认值。`ORBIT_CONFIG` 可指定配置文件；所有相对路径以该文件所在目录为基准。命令名如 `codex`、`node` 从 PATH 查找，完整路径也可使用。

| JSON字段 | 环境变量 | 默认值 / 用途 |
| --- | --- | --- |
| data_dir | ORBIT_DATA_DIR | `data`，任务、数据库、经验、日志及PROJECT归档 |
| vault | ORBIT_VAULT | `examples/vault`，允许Agent读取的Markdown库 |
| project_index | ORBIT_PROJECT_INDEX | `examples/projects.json`，项目清单 |
| codex_bin | ORBIT_CODEX_BIN | `codex` |
| node_bin | ORBIT_NODE_BIN | `node`，可选组件要求Node24+ |
| harness_entry | ORBIT_HARNESS_ENTRY | `tools/harness-runtime/node_modules/@deepseek-ai/dsh/lib/bin.js` |
| deepseek_env_file | ORBIT_DEEPSEEK_ENV_FILE | `.env`，DeepSeek配置来源 |
| codebuddy_entry | ORBIT_CODEBUDDY_ENTRY | `tools/workbuddy/node_modules/.bin/codebuddy` |
| autoloop_dir | ORBIT_AUTOLOOP_DIR | `tools/agent-autoloop` |
| port | ORBIT_PORT | `8765`，工作台回环端口 |
| autoloop_port | ORBIT_AUTOLOOP_PORT | `3350`，可选事件回环端口 |

两端口须不同，范围1024—65535。配置修改后重启。若修改 `data_dir` 或端口，先用旧配置停止原实例，再改配置启动。目录包含空格时，在终端给路径加引号。

DeepSeek 示例使用 `deepseek-flash`；可通过 `DEEPSEEK_MODEL` 选择账号可用模型，当前名称以 [官方模型列表](https://api-docs.deepseek.com/api/list-models/) 为准。

项目索引格式：

```json
{
  "projects": [
    {"key": "my_project", "name": "我的项目", "root": "project", "summary": "项目目标"}
  ]
}
```

`root` 相对索引文件所在目录。项目目录里的 `PROJECT.md` 作为任务上下文摘要，只填写愿意交给所选Agent处理的内容。平台产物写数据目录，不自动合并回业务源项目。

## Obsidian / Markdown库

不需要安装Obsidian插件。三个精选目录为 `知识/概念`、`知识/综合分析`、`知识/来源摘要`。检索只读根层Markdown文件，最多600篇；保留来源与内容指纹，不追随符号链接。建议先从示例库复制目录结构，逐步添加可公开或已授权的资料。

任务中的「已检索」「已传入」「产物引用」分别记录。经验必须有证据且通过复核，才进入同项目后续任务召回。过期、证据不可读或有害反馈可使经验停用；人工修改要保留复核理由。

## 运行数据与备份

停止平台后整体备份 `data_dir`，包含数据库、运行目录与记忆文件。运行中仅复制SQLite主文件不是完整备份。模型CLI登录保存在其自己的标准目录，Orbit不复制登录文件。`.env`、`orbit.json`、自定义项目和知识库应由用户单独私密备份，不能放进公开仓库。

更新时先停止平台并备份运行数据，再更新源码。保留 `orbit.json`、`.env` 和数据目录；下载ZIP更新时不要覆盖这些本地文件。
