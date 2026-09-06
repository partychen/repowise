# 安装、迁移与发布

[English](distribution.md) | 简体中文

## 用户入口：安装 Skill，然后指定项目

使用标准 [skills CLI](https://github.com/vercel-labs/skills) 安装：
`partychen/review-memory` 是本 Skill 的发布仓库，不是要学习的项目。

```powershell
npx skills add partychen/review-memory --skill review-memory -a github-copilot -g
```

其他宿主使用相应的 `-a` 值；不带 `-g` 是项目级安装。审查不可信 PR 时推荐个人级安装，
避免把 PR 修改的 Skill 指令当成可信工具。安装器可复制或链接 Skill 目录，
宿主应从实际加载位置定位脚本，不能假定总是在项目 `.github` 下。

然后在目标项目会话中告诉 Copilot：

> 使用 review-memory，同步 acme/my-project 的 PR，并累积评审知识。

宿主会准备隔离 Python 依赖、绑定项目、分批同步已合并 PR、读取证据、
提炼并保存候选，再更新累积知识索引。后续说“继续同步这个项目”即可恢复。
不需要另行安装 review-memory wheel，不需要另配模型 API Key。

**安装命令本身不启动任务或后台服务。** 自动化发生在宿主执行 Skill 时。
需要 Python 3.11+；在线同步还需要 GitHub CLI 和操作者已有的登录授权。
依赖安装、网络读取仍遵守宿主的工具批准机制；不会偷偷安装 gh、申请权限或操作私钥。

## 本地开发体验

本地开发时，可以从源码根目录安装到指定宿主：

```powershell
npx skills add . --skill review-memory -a github-copilot -g --copy
```

如已有同名安装，先检查安装器提示，避免覆盖另一版本。开发单测仍可使用
`python -m pip install -e .`；它不是用户安装路径，也不参与已安装 Skill 的模块选择。

## 自包含目录与依赖

```text
review-memory\
  SKILL.md
  requirements.txt
  scripts\
    main.py
    review_memory\
  references\
  packs\
```

脚本、知识包和依赖声明随 Skill 一起安装。`main.py setup` 使用标准库创建用户缓存中的
隔离环境，并准备固定依赖；日常命令执行当前 Skill 携带的代码，不能回退到全局同名模块。
宿主负责这一步，不要求用户自己寻找路径和执行 pip。新机器需要重新准备依赖，
不能直接复制虚拟环境；完全离线首次使用需事先提供可信依赖包。

项目数据在目标项目的 `.review` 中，不在 Skill 安装目录：

| 数据 | 路径 |
| --- | --- |
| 项目绑定 | `.review/config.yaml` |
| 可恢复同步队列 | `.review/local/state/sync.json` |
| 原始证据和学习任务 | `.review/local/raw`、`.review/local/proposals/tasks` |
| 模型提炼的候选 | `.review/local/proposals` |
| 累积知识索引（未批准） | `.review/local/learning/index.json` |
| 人工批准的规则 | `.review/knowledge`、`.review/approvals` |

移动 Skill 不需要替换脚本路径；宿主重新解析实际安装位置。移动目标项目时保留需要的
`.review` 数据，同步状态使用项目相对路径。不要修改历史签名或 task 的哈希。
`.review/local` 含私有数据，不应发布。正式规则的审批绑定运行时代码与依赖；
将来更新运行时后，需要维护者重新审批，不会自动签署规则。

## 发布

发布仓库为 [partychen/review-memory](https://github.com/partychen/review-memory)，
保留 `.github/skills/review-memory` 目录。
skills CLI 可以发现这个布局；也可以用完整的 Skill 子目录 GitHub URL 安装。
无需为了 `npx skills add` 发布 npm 包或 Python 包。

若需要 Release 附件，从本项目根目录打包：

```powershell
python .\tools\package_skill.py
# 可选：给独立 CLI 用户提供 wheel，而非 Skill 的前置要求
python -m pip wheel . --no-deps --wheel-dir .\dist
```

Skill ZIP 和校验文件使用 `pyproject.toml` 中的版本号命名。
ZIP 使用明确文件清单，包含依赖声明，不包含虚拟环境、私有项目数据或机器缓存；
解压后的整个 `review-memory` 目录也可手动放到宿主认可的 Skill 目录。

发布前由项目所有者决定许可证，保留第三方来源声明，核对内容。
本地打包不自动创建仓库、上传 GitHub Release 或发布 PyPI/npm。
仓库源码安装不依赖 GitHub Release 附件。

## 同步范围与边界

默认收集所有可枚举的已合并 PR，单批 20 个，可恢复；可在首次连接时指定
`sync --since YYYY-MM-DD` 限制**合并时间**。该范围保存后不能静默改变。
分页有明确的安全上限，触顶或读取失败会报告未完成，而不是把剩余历史当作已处理。

增量检测比较 PR 列表元数据。`sync --refresh` 在当前队列完成后发起完整反馈重读，
用于补查未反映在 PR 元数据里的评论变动；相同证据不重复创建任务。
采集完成和知识提炼完成是不同状态。宿主预算不足时保存待办，不声称已经学习全部 PR。
固定周期自动运行需要宿主调度或用户另外配置任务；这不是安装器附带的守护进程。
