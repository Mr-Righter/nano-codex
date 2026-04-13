<div align="center">
  <h1>Nano-Codex</h1>
  <p><i>轻量级 Codex 风格命令行工具，支持可定制的智能体、工具与上下文控制。</i></p>
</div>

<p align="center">
  <a href="README.md">English</a> |
  <a href="README.zh.md">简体中文</a>
</p>

<div align="center">

  [![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
  [![License](https://img.shields.io/badge/License-MIT-green?style=flat)](LICENSE)
  [![Framework](https://img.shields.io/badge/Framework-Agent%20Framework-C94F4F?style=flat)](https://github.com/microsoft/agent-framework)

</div>

<div align="center">

  <a href="#主要特性">主要特性</a> &nbsp;•&nbsp;
  <a href="#运行时概览">运行时概览</a> &nbsp;•&nbsp;
  <a href="#项目结构">项目结构</a> &nbsp;•&nbsp;
  <a href="#安装">安装</a> &nbsp;•&nbsp;
  <a href="#快速开始">快速开始</a> &nbsp;•&nbsp;
  <a href="#内置功能">内置功能</a> &nbsp;•&nbsp;
  <a href="#扩展文档">扩展文档</a>

</div>

<div align="center">
  <img src="docs/assets/banner.png" alt="Nano-Codex 横幅" />
</div>

Nano-Codex 是一个基于 [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) 构建的轻量级、高度可扩展的编码命令行工具。它提供 Codex 风格的工具集，并支持可配置的智能体、可控的上下文以及可审视的执行过程。

## 主要特性

### 内置工具集

内置工具覆盖常见的编码、Shell、网络、媒体、规划、技能及子智能体工作流，同时支持通过 MCP 针对具体项目进行扩展。

### 上下文工程

上下文可通过智能体、对话及函数中间件进行塑造，既支持自动压缩，也支持在长时间会话中手动调用 `/compact` 进行压缩。

### 智能体定制

每个智能体及子智能体均可独立配置其工具、技能以及聊天补全选项（例如 `enable_thinking`）。

### 可观测的用户界面

控制台及 TUI 输出以结构化的方式呈现思考过程、工具调用、子智能体活动、上下文压缩以及会话事件，使智能体循环保持透明可见。

## 运行时概览

Nano-Codex 采用 ReAct 风格的智能体循环来处理编码工作流，并围绕该核心执行模式构建了工具、中间件及用户界面可观测性。

### 系统提示组装

系统提示由三个主要输入组合而成：

- `agent.md` 中的指令正文
- 包含工作目录、平台及当前日期的运行时环境块
- 在 `agent.md` 的 YAML 前置元数据中声明并从 `skills_dir` 加载的技能

初始系统提示不会被压缩移除，并在整个会话期间始终作为活跃上下文的一部分保留。

### 中间件分层

Nano-Codex 使用三种中间件类型，每种分别挂载在运行时的不同层级：

- `Agent 中间件` 包裹一次完整的智能体循环。适用于重写消息列表、调整运行级选项或添加应影响整个循环的提醒信息。
- `Chat 中间件` 包裹循环内的每次 LLM 请求/响应。适用于在内容到达模型之前进行转换，或在后续阶段消费之前重塑模型响应。
- `Function 中间件` 包裹每一次单独的工具调用。可用于检查已验证的工具参数、附加元数据，或在工具结果返回至循环之前对其进行改写。

这些分层分别用于智能体循环级、LLM 调用级以及工具调用级的上下文工程任务。

## 项目结构

```text
nano-codex/
├── README.md
├── agent.md                         # 主智能体定义（YAML 前置元数据 + 指令）
├── launcher.py                      # CLI 入口
├── requirements.txt                 # 主要 pip 依赖清单
├── nano_codex.yaml                  # 默认运行时配置入口
├── configs/
│   ├── agents/                      # 运行时加载的子智能体定义
│   ├── mcp_config.json              # MCP 服务器配置
│   ├── model_config.json            # 模型端点及别名配置
│   └── skills/                      # 本地技能定义及引用
├── docs/                            # 文档资源及扩展指南
└── src/
    ├── agent_framework_patch/       # 框架补丁层，用于处理元数据、历史记录及聊天客户端行为
    │   ├── function_invocation_layer.py      # 经过修补的函数调用循环集成
    │   ├── history_compaction_runtime.py     # 支持压缩的会话历史运行时
    │   ├── openai_chat_completion_client.py  # 经过修补的聊天补全客户端
    │   └── tool_invocation.py                # 工具调用元数据传播补丁
    ├── core/                        # 核心运行时组装及工作流编排
    │   ├── interactive_workflow.py          # 智能体循环周边的交互式工作流
    │   └── nano_codex.py                    # 主 Nano-Codex 智能体构造
    ├── middlewares/                 # 中间件注册表及内置中间件实现
    │   ├── agent_middlewares.py            # 智能体循环中间件实现
    │   ├── chat_middlewares.py             # LLM 调用中间件实现
    │   ├── function_middlewares.py         # 工具调用中间件实现
    │   └── middleware_registry.py          # 中间件注册与加载
    ├── toolkit/                     # 内置工具包及工具包加载
    │   ├── bash/                           # Shell 执行工具
    │   ├── file_operation/                 # 文件、图像及视频工具
    │   ├── planning/                       # 待办事项及开发日志工具
    │   ├── skilling/                       # 技能加载工具
    │   ├── subagent/                       # 子智能体委托工具
    │   ├── web_operation/                  # 网络搜索及获取工具
    │   ├── tool_loader.py                  # 工具包注册及 MCP 加载
    │   └── tool_support.py                 # 共享的工具包运行时上下文及辅助函数
    ├── ui/                          # 共享的控制台及交互式 UI 层
    │   ├── console_display.py              # Rich 控制台渲染器
    │   ├── console_formatters.py           # 控制台格式化辅助函数
    │   ├── events.py                       # 共享的 UI 事件定义
    │   ├── factory.py                      # UI 运行时工厂
    │   ├── presenters.py                   # 框架至 UI 的事件呈现器
    │   ├── protocol.py                     # UI 运行时接口
    │   ├── theme.py                        # 控制台主题定义
    │   └── tui/                            # Textual 应用、斜杠命令、会话记录状态及小组件
    └── utils/                       # 提示组装、模型配置、历史 I/O 及压缩辅助函数
        ├── auto_compact.py                 # 自动与手动压缩逻辑
        ├── env_loader.py                   # Bash 环境验证辅助函数
        ├── history_io.py                   # 会话持久化辅助函数
        ├── markdown_parser.py              # Markdown + YAML 前置元数据解析
        ├── model_client.py                 # 模型配置解析及客户端创建
        ├── plugin_discovery.py             # 技能发现辅助函数
        └── prompt_assembler.py             # 系统提示组装
```

## 安装

支持 Python 3.10 及以上版本。

```bash
git clone https://github.com/Mr-Righter/nano-codex.git
cd nano-codex
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

## 快速开始

### 1. 配置模型访问

编辑 `configs/model_config.json`，将其指向一个兼容 OpenAI 的聊天补全端点。

示例：

```json
{
  "global": {
    "base_url": "https://your-openai-compatible-endpoint/v1",
    "api_key": "your-global-api-key"
  },
  "models": {
    "gpt-5.4": {
      "model_id": "gpt-5.4"
    },
    "gemma-4-31B": {
      "model_id": "google/gemma-4-31B-it",
      "base_url": "https://your-gemma-endpoint/v1",
      "api_key": "your-gemma-api-key"
    }
  }
}
```

`nano_codex.yaml`、`agent.md` 或命令行参数中的 `model` 字段应与 `models` 下的某个键相匹配。当某个模型条目定义了自己的 `base_url` 或 `api_key` 时，这些值将覆盖 `global` 中的对应设置。

### 2. 配置运行时输入

`nano_codex.yaml` 定义了默认的运行时行为。一个实用的起始配置如下所示：

```yaml
is_interactive: true
model: gpt-5.4
work_dir: /absolute/path/to/workdir
agent_loop_max_iterations: 40

agent_config_path: ./agent.md
model_config_path: configs/model_config.json
mcp_config_path: configs/mcp_config.json
skills_dir: configs/skills
agents_dir: configs/agents

middlewares:
  - "user_message_reminder"
  - "logging_response"
  - "move_tool_media_to_user_message"
  - "tool_result_reminder"
  - "logging_function_result"

auto_save_history: true
auto_compact_enabled: true
auto_compact_max_tokens: 200000
auto_compact_keep_last_groups: 0
```

注意：

- 如需查看完整的启动器配置参考，请运行 `python launcher.py -h`。

### 3. 配置 `agent.md`

`agent.md` 使用由 `---` 标记包围的 YAML 前置元数据以及 Markdown 指令。前置元数据控制运行时相关的字段，例如模型选择、工具暴露、预加载技能及默认聊天补全选项，而 Markdown 正文则提供智能体在运行时所遵循的指令。

示例：

```markdown
---
name: Nano-Codex
description: 通用工程智能体。
model: gpt-5.4
tools: [read, write, edit, glob, grep, bash, web_search, use_skill, solve_task_with_subagent]
skills: [agent-browser]
default_options:
  reasoning_effort: high
---

# Nano-Codex

在此处编写您的指令正文。
```

常用前置元数据字段：

- `name`：智能体显示名称
- `description`：运行时及子智能体工具所使用的简短描述
- `model`：来自 `configs/model_config.json` 的默认模型别名
- `tools`：该智能体可用的工具名称列表
- `mcp_service`：从 `configs/mcp_config.json` 加载的 MCP 服务名称
- `skills`：预加载到系统提示中的技能
- `hidden_skills`：该智能体无法通过 `use_skill` 调用的技能
- `default_options`：默认聊天补全选项，例如 `enable_thinking`

子智能体在 `configs/agents/` 下采用相同的 `YAML 前置元数据 + 指令` 格式。

### 4. 运行 Nano-Codex

非交互式单任务模式：

```bash
python launcher.py --config nano_codex.yaml --is_interactive false --task "检查仓库并撰写摘要"
```

交互式 TUI 模式：

```bash
python launcher.py --is_interactive true
```

您也可以直接覆盖配置值：

```bash
python launcher.py --is_interactive true --work_dir /absolute/path/to/workdir
```

使用 `nano_codex_debug.log` 进行启动及运行时调试。

## 内置功能

### 工具包

Nano-Codex 从 `src/toolkit/` 加载内置工具包，随后根据活跃智能体在 `agent.md` 或子智能体定义中的 `tools:` 前置元数据对最终工具集进行过滤。

| 工具 | 分组 | 用途 |
| --- | --- | --- |
| `read` | 文件 | 将文件内容读入当前上下文。 |
| `write` | 文件 | 创建或覆盖文件。 |
| `edit` | 文件 | 修改现有文件。 |
| `glob` | 文件 | 按路径模式查找文件。 |
| `grep` | 文件 | 按模式搜索文件内容。 |
| `bash` | Shell | 在持久化的 Shell 会话中运行命令。 |
| `bash_output` | Shell | 读取后台 bash 进程的新输出。 |
| `kill_bash` | Shell | 终止后台 bash 进程。 |
| `view_image` | 媒体 | 将图像内容直接读入当前上下文，适用于前端页面生成等任务。 |
| `analyze_image` | 媒体 | 使用模型分析图像并返回文本输出，更适用于一般的图像分析任务。 |
| `view_video` | 媒体 | 通过采样帧将视频内容读入当前上下文。`video_frame_fps` 和 `video_max_frames` 控制帧提取行为。 |
| `analyze_video` | 媒体 | 使用模型分析采样后的视频帧并返回文本输出。`video_frame_fps` 和 `video_max_frames` 控制帧提取行为。 |
| `write_todos` | 规划 | 创建或更新结构化的待办事项列表。 |
| `write_dev_log` | 规划 | 持久化记录调试或执行说明。 |
| `web_search` | 网络 | 搜索网络并返回摘要结果。 |
| `web_fetch` | 网络 | 获取并提取指定网页的内容。 |
| `use_skill` | 技能 | 将一项本地技能加载到当前运行中。 |
| `solve_task_with_subagent` | 子智能体 | 将一项边界明确的任务委托给已配置的子智能体。 |

### 中间件

| 中间件 | 层级 | 用途 |
| --- | --- | --- |
| `user_message_reminder` | Agent | 在一次完整的智能体循环运行前，紧邻最新的用户消息插入一条提醒。 |
| `logging_response` | Chat | 为控制台及 TUI 层发送助手响应事件。 |
| `move_tool_media_to_user_message` | Chat | 将工具返回的媒体内容改写为后续的用户消息，用于下一次模型调用。 |
| `strip_reasoning` | Chat | 在向下游发送聊天请求前移除推理条目。 |
| `tool_result_reminder` | Function | 在工具执行后，向选定的工具结果追加后续提醒。 |
| `logging_function_result` | Function | 为 UI 层发送结构化的工具生命周期事件。 |

关于执行顺序、数据流及扩展示例，请参阅 [docs/extensions/middlewares.md](docs/extensions/middlewares.md)。

### 斜杠命令

| 命令 | 用途 |
| --- | --- |
| `/compact` | 对当前会话强制执行一次手动压缩。 |
| `/clear` | 清除非系统历史记录，同时保留当前系统上下文。 |
| `/model` | 打开交互式模型选择器。 |
| `/exit` | 退出 Nano-Codex。 |

### 上下文管理

#### 压缩

自动压缩由 `nano_codex.yaml` 中的 `auto_compact_*` 设置驱动。当最近一次模型调用的 Token 计数超过 `auto_compact_max_tokens` 时，Nano-Codex 会将较旧的可见消息组总结为一条延续摘要，并将已被总结的消息标记为已排除，而非直接丢弃。`auto_compact_keep_last_groups` 用于保留最近的消息组使其保持可见。手动压缩命令 `/compact` 也使用相同的总结路径。

#### 会话恢复

交互式会话历史记录以序列化的 `AgentSession` JSON 文件形式持久化。默认情况下，Nano-Codex 在 `{work_dir}/.sessions/session_history.json` 处保存及恢复；若设置了 `history_file`，则该路径将成为明确的恢复/保存目标。这使得会话恢复成为一种内置的持久化机制，而非斜杠命令功能。

## 技能与子智能体

### 技能

默认情况下，Nano-Codex 从 `configs/skills/*/SKILL.md` 发现技能。如需使用其他根目录，可在 `nano_codex.yaml` 中修改 `skills_dir` 或通过命令行传递 `--skills_dir` 参数。

每个技能均为一个基于目录的指令包。`SKILL.md` 使用由 `---` 标记包围的 YAML 前置元数据以及 Markdown 指令。前置元数据告知 Nano-Codex 应何时以及如何使用该技能，Markdown 正文则提供技能被调用时所遵循的指令。

示例：

```markdown
---
name: agent-browser
description: 用于导航、截图及 DOM 交互的浏览器自动化命令行工具。
invoke_when: 任务需要浏览器交互或页面检查时调用。
---

# agent-browser

当任务依赖于操作浏览器或检查实时页面时，请使用此技能。
```

参考目录结构：

```text
configs/skills/
└── agent-browser/
    ├── SKILL.md
    └── references/
        └── commands.md
```

### 子智能体

子智能体采用与 `agent.md` 相同的 `YAML 前置元数据 + 指令` 格式。默认情况下，Nano-Codex 从 `configs/agents/*.md` 发现它们。如需使用其他目录，可在 `nano_codex.yaml` 中修改 `agents_dir` 或通过命令行传递 `--agents_dir` 参数。

## 扩展文档

- [自定义工具](docs/extensions/custom-tools.md)
- [中间件](docs/extensions/middlewares.md)
- [斜杠命令](docs/extensions/slash-commands.md)

## 后续规划

- `更多斜杠命令`：添加更多内置斜杠命令，例如 `/plan` 和 `/review`。
- `工具模式`：引入工具模式控制，例如 `ask-before-edit`（编辑前询问）和 `edit-automatically`（自动编辑）。
- `交互改进`：通过中断活跃的智能体循环以及诸如 `ask_user_question` 等工具，改善用户交互体验。

## 许可证

MIT 许可证。详见 [LICENSE](LICENSE) 文件。
