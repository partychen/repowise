# Review Memory

[English](README.md) | 简体中文

让 AI 助手同步项目的 PR 评审记录，提炼团队经验，并持续积累可追溯的项目知识。

## 安装

```powershell
npx skills add partychen/review-memory --skill review-memory -a github-copilot -g
```

需要 Node.js/npx、Python 3.11+。同步 GitHub 项目前，先完成 GitHub CLI 登录：

```powershell
gh auth login
```

已经登录的用户无需重复登录。首次使用时，助手会准备 Skill 所需的隔离 Python 环境；
不需要你另装 Python 后端或配置模型 API Key。

## 安装后怎么调用

**打开你要学习的项目，在 AI 助手的对话框里输入下面的话，而不是在 PowerShell 中输入。**

最直接的方式是明确写出 Skill 名称：

```text
使用 review-memory，同步这个项目的 PR，并积累评审知识。
```

在 Copilot CLI 中，也可以通过 `/review-memory` 显式指定：

```text
使用 /review-memory，同步这个项目的 PR，并积累评审知识。
```

“同步 PR”“学习历史评审”“积累评审知识”等任务描述可帮助助手自动匹配。
**推荐直接带上 `review-memory`，不要依赖单个关键词触发。**

如果安装时 Copilot CLI 已经打开，先在 CLI 会话内执行：

```text
/skills reload
/skills info review-memory
```

其他宿主如果没有刷新到新 Skill，重新打开会话，并确认安装时选择了对应宿主。

## 第一次使用：指定项目

在目标项目会话中输入，替换成你自己的 GitHub 项目：

```text
使用 review-memory。
项目是 https://github.com/acme/my-project，本地目录就是当前工作区。
同步已合并 PR 的评审记录，提炼经验并保存为项目知识。
```

这里的 `acme/my-project` 是**你要学习的项目**，不是安装命令中的 Skill 仓库。
如果缺少项目地址或本地目录，助手会询问，不会替你随意选择。

助手会依次完成：

1. 记录项目绑定，准备运行环境。
2. 分批获取已合并 PR 的评审和讨论。
3. 阅读证据，提炼有适用条件、例外和来源的知识候选。
4. 保存候选与知识索引，报告进度和待处理数量。

默认每批处理 20 个 PR。历史较多或会话预算不足时会保存进度，下次继续。
如果只想学习某段历史，可以在首次连接时说明：

```text
使用 review-memory，为 acme/my-project 学习 2026-01-01 之后合并的 PR。
本地目录使用当前工作区。
```

## 后续常用指令

| 想做什么 | 在助手对话中输入 |
| --- | --- |
| 继续同步与学习 | 使用 review-memory，继续同步当前项目，并完成剩余知识提炼。 |
| 看进度 | 使用 review-memory，查看当前项目还有多少 PR 和学习任务待处理。 |
| 看积累的知识 | 使用 review-memory，汇总当前项目的知识，并列出支持它们的 PR。 |
| 完整补查历史反馈 | 使用 review-memory，完成待处理队列后，完整刷新历史 PR 的评审记录。 |
| 学习指定 PR | 使用 review-memory，学习 acme/my-project 的 PR #123，提炼并保存评审经验。 |
| 用已批准知识审查 | 使用 review-memory，按当前项目已批准的规则审查这次变更。 |

每次调用时执行同步，不会在安装后自动启动常驻后台任务。
知识候选会持续保存；用于正式规则审查前，需要维护者检查并批准。

## 知识保存在哪里

都保存在**目标项目**的 `.review` 目录中：

| 路径 | 内容 |
| --- | --- |
| `.review/config.yaml` | 项目绑定 |
| `.review/local/state/sync.json` | 同步进度和待处理 PR |
| `.review/local/proposals` | 提炼出的候选知识及其证据关联 |
| `.review/local/learning/index.json` | 累积知识索引 |

更新或重新安装 Skill 不会主动删除这些项目数据。
`.review/local` 默认不提交到 Git，避免把原始评审数据公开。

## 更多说明

- [安装、迁移与发布](docs/distribution.zh.md)
- [同步流程](.github/skills/review-memory/references/sync.md)
- [知识审批](.github/skills/review-memory/references/approval.md)
- [代码审查](.github/skills/review-memory/references/review.md)
- [来源与许可说明](THIRD_PARTY.md)
