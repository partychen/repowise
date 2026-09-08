# RepoWise

[English](README.md) | 简体中文

> 把团队积累的经验，带进下一次代码变更。

**RepoWise** 为 GitHub Copilot、Claude Code 和 Codex 提供持久化、有证据支撑的项目知识。
它从 PR 评审中学习，将已批准的经验用于后续审查，并指导 agent 按照仓库架构和约定实现功能。

[安装](#安装) | [工作流](#工作流) | [审查角色](#审查角色) |
[项目记忆](#项目记忆) | [信任模型](#信任模型) | [文档](#文档)

## 功能

| 工作流 | 产出 |
| --- | --- |
| 学习 PR 经验 | 可续跑的采集流程、有适用范围的知识、支持证据和修订历史 |
| 审查代码变更 | 结合项目上下文的发现、经过校验的源码引用和明确的覆盖缺口 |
| 实现功能 | Agent 完成的代码修改、经授权的检查和完成记录 |

项目知识随着新的 PR 证据持续演进：后续评审可以补充支持证据、揭示例外或推动修订。
演进的是项目知识，而非模型权重。每条知识保留适用范围、反例和来源，
只有维护者批准的具体修订才能成为审查策略。

## 环境要求

| 依赖 | 用途 |
| --- | --- |
| GitHub Copilot CLI、Claude Code 或 Codex CLI | 执行 Skill 并完成推理 |
| 带 `npx` 的 Node.js | 安装 Skill |
| 带 `venv`、`ensurepip` 的 Python 3.11+ | 执行内置 CLI |
| Git | 读取不可变的代码和策略提交 |
| 已认证的 GitHub CLI（`gh`） | 采集 PR 元数据、评审和讨论 |
| 支持 SSH 签名的 OpenSSH | 签署和验证策略审批 |

访问仓库前，完成 GitHub CLI 认证：

```powershell
gh auth login
```

首次使用时，agent 会通过内置、固定哈希的 PyYAML wheel 准备隔离 Python 环境。
依赖安装离线完成，无需单独安装运行时或配置模型 API Key。
环境及 Git 要求详见[项目设置](.github/skills/repowise/references/setup.md)。

## 安装

选择使用的 agent，在本地项目目录中执行对应的终端命令，
然后在 agent 对话中输入 Skill 请求。`-g` 将 Skill 安装到用户级目录，供各项目使用。

### GitHub Copilot CLI

**终端**

```powershell
npx skills add partychen/repowise --skill repowise -a github-copilot -g
copilot
```

**Skill 请求**

```text
使用 /repowise，同步当前项目的 PR 评审并建立项目知识。
```

[GitHub Copilot Skill 文档](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills)

### Claude Code

**终端**

```powershell
npx skills add partychen/repowise --skill repowise -a claude-code -g
claude
```

**Skill 请求**

```text
/repowise 同步当前项目的 PR 评审并建立项目知识。
```

[Claude Code Skill 文档](https://code.claude.com/docs/en/skills)

### Codex CLI

**终端**

```powershell
npx skills add partychen/repowise --skill repowise -a codex -g
codex
```

**Skill 请求**

```text
$repowise 同步当前项目的 PR 评审并建立项目知识。
```

[Codex Skill 文档](https://developers.openai.com/codex/skills/)

## 工作流

以下请求适用于各 agent 的对话。可以使用上述显式调用方式，
也可以在请求中直接指定 `repowise`。

### 1. 连接项目并学习

指定 GitHub 仓库及其已有的本地工作区：

```text
使用 repowise，项目是 https://github.com/acme/my-project。
本地目录使用当前工作区。
同步已合并 PR 的评审，提炼经验并保存为项目知识。
```

将 `acme/my-project` 替换为目标仓库。Agent 会绑定项目、采集评审证据，
与已有经验对照，并保存经过校验的知识提案。结果包含累积知识和剩余工作。

默认范围为已合并 PR 历史，每批处理 20 个，进度跨会话保存。
指定初始时间范围：

```text
使用 repowise，学习当前项目自 2026-01-01 起合并的 PR。
```

| 任务 | 请求 |
| --- | --- |
| 继续学习 | 使用 repowise，继续同步当前项目并完成待处理的学习任务。 |
| 查看进度 | 使用 repowise，显示剩余 PR 和学习任务。 |
| 浏览知识 | 使用 repowise，汇总项目知识并附上支持它们的 PR 链接。 |
| 学习单个 PR | 使用 repowise，学习 PR #123 并保存评审经验。 |
| 刷新评审 | 使用 repowise，完成待处理队列后，完整刷新历史 PR 的评审记录。 |

学习由请求触发；安装不启动调度器或 webhook。
采集、学习和策略审批分别记录完成状态。

### 2. 批准项目知识

```text
使用 repowise，展示待审批的知识候选、证据、适用范围和例外。
为我选中的修订准备签名请求。
```

Agent 展示可读预览并准备未签名的审批请求。
维护者配置可信签名身份、签署选定修订，再将经过验证的记录提交到外部知识目录的 Git 仓库。
后续审查可以选用该策略提交。

学习不要求预先审批。知识审批与检测器审批相互独立；agent 不代签或授予信任。
详见[审批流程](.github/skills/repowise/references/approval.md)。

### 3. 审查 PR

准备好已批准的知识后，指定 PR 和可信策略提交：

```text
使用 repowise，审查 https://github.com/acme/my-project/pull/123。
使用项目外部知识仓库中的 POLICY_SHA 作为可信策略提交。
```

将 `POLICY_SHA` 替换为维护者选定的提交。RepoWise 从本地 Git 对象解析 PR 的不可变
BASE/HEAD，采集有界的项目上下文并准备审查。所需代码对象须已存在于本地。

审查涵盖架构、已有框架与工具、契约、安全、资源生命周期、并发、测试、惯用法和变更范围。
一致性类发现需要精确的 BASE 对照；行为错误需要具体影响和触发条件。
所有策略类发现均引用已批准的知识修订。

产出为包含发现、证据、评估和覆盖缺口的本地报告。
Markdown 按组展示相关发现并提供可展开的支持证据；
JSON 保留底层记录和来源。

### 4. 实现功能

```text
使用 repowise，为现有列表接口实现游标分页。
遵循项目架构和适用的已批准知识。
允许修改相关文件并运行现有的针对性检查。
保留我的已有修改，不要提交或推送。
```

Agent 准备项目上下文，执行获授权的修改与检查，并记录实际结果。
没有已批准策略时也可以开始实现，缺失的策略覆盖会明确记录。
完成记录包含变更文件、检查结果和剩余工作。

## 审查角色

主 agent 负责最终覆盖和结果归并。专业角色基于同一份冻结的代码、
项目上下文和已批准知识开展专项分析。

| 角色 | 关注点 |
| --- | --- |
| 架构 | 模块边界、依赖方向、已有机制和局部惯用法 |
| 业务逻辑与契约 | 业务不变量、类型、调用行为、兼容性和迁移范围 |
| 安全 | 认证、授权、不可信输入、敏感数据和隔离 |
| 可靠性 | 错误传播、取消、重试、部分失败和资源清理 |
| 并发与性能 | 竞态、阻塞、竞争、背压和资源上限 |
| 测试与可观测性 | 回归场景、有效断言和诊断信号 |

自动规划将小变更交给主 agent，对较大变更选择相关角色。
也可以显式指定审查分工：

```text
使用 repowise，让架构、安全和可靠性角色审查 PR #123。
最多同时运行两个子 agent，最后复核并归并发现。
```

默认并发上限为三个子 agent。执行使用宿主提供的子 agent 工具；
单 agent 和串行执行分别记录。缺失或失败的角色保留为覆盖缺口。

主 agent 复核推理、处理分歧并按根因归组。
原始证据和驳回理由保持可追溯；同一位置的不同缺陷分别保留，
静态检测结果不能被隐藏。
路由、CLI 选项和响应格式详见[审查协调](.github/skills/repowise/references/review-agents.md)。

## 项目记忆

每个本地工作区都有独立的外部知识目录：

```text
~\.repowise\projects\<项目名>-<路径哈希>\.review\
```

规范化的工作区路径决定独立空间，GitHub 仓库绑定另行验证。
命令返回准确的 `storage_root` 和 `local_path`；
产物中的相对路径以 `storage_root` 为基准。

| 相对于外部存储根目录的路径 | 内容 |
| --- | --- |
| `.review/config.yaml`、`.review/project.json` | 项目配置和本地工作区绑定 |
| `.review/local/state/sync.json` | 采集进度和待处理 PR |
| `.review/local/raw/evidence` | 带版本的评审证据 |
| `.review/local/proposals`、`.review/local/learning/index.json` | 候选、响应、修订关联和未批准知识 |
| `.review/knowledge`、`.review/approvals`、`.review/detectors` | 知识修订、签名和检测器审批 |
| `.review/local/runs` | 审查任务、子 agent 记录和报告 |
| `.review/local/features` | 功能上下文和完成记录 |
| `.review/local/evaluation` | 回放任务和独立裁定 |

通过 `REPOWISE_HOME` 或 CLI 的 `--data-home` 选项指定其他外部父目录。
更新 Skill 保留项目记忆。CLI 不在目标工作区创建知识文件或忽略规则。

## 信任模型

| 组件 | 职责 |
| --- | --- |
| 宿主 agent | 分析证据、协调审查并执行获授权的功能开发 |
| 内置 CLI | 采集数据、冻结上下文、校验签名和响应、运行已批准的固定检测器并保存产物 |
| 维护者 | 选择可信策略、管理签名身份、批准知识和工具修订 |
| 参考包 | 提供 Rust 审查问题和反例，不构成仓库策略 |

学习与审查读取不可变 Git 对象，不运行目标项目的构建、测试、hooks 或生成的检测器。
功能修改和检查需要用户授权；提交、推送和发布属于独立操作。

当前固定检测器 `rust.forbidden-dependency.v1` 分析 Cargo 依赖声明，
不运行 Cargo 或解析完整依赖图。
历史回放不使用当前版本的参考包，并要求独立人工裁定。

上下文缺失、知识过期、agent 失败和漏评均保留为覆盖缺口。
精确引用校验证明来源一致性，不证明模型推理正确。
运行时或依赖变化后，受运行时绑定影响的记录需要维护者重新审批。

## 文档

| 主题 | 参考 |
| --- | --- |
| 安装与分发 | [分发指南](docs/distribution.zh.md) |
| 项目设置 | [环境与存储](.github/skills/repowise/references/setup.md) |
| 知识积累 | [同步](.github/skills/repowise/references/sync.md)、[学习](.github/skills/repowise/references/learning.md) |
| 知识生命周期 | [结构与修订](.github/skills/repowise/references/knowledge.md)、[审批](.github/skills/repowise/references/approval.md) |
| PR 审查 | [审查流程](.github/skills/repowise/references/review.md)、[角色协调](.github/skills/repowise/references/review-agents.md) |
| 功能实现 | [功能工作流](.github/skills/repowise/references/feature.md) |
| 评估 | [历史回放](.github/skills/repowise/references/replay.md)、[范围与限制](docs/pilot.md) |
| 项目开发 | [贡献指南](AGENTS.md) |
