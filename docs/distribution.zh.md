# 安装、存储与发布

[English](distribution.md) | 简体中文

## 用户入口：安装 Skill，然后指定项目

使用标准 [skills CLI](https://github.com/vercel-labs/skills) 安装：
`partychen/review-memory` 是本 Skill 的发布仓库，不是要学习的项目。

```powershell
npx skills add partychen/review-memory --skill review-memory -a github-copilot -g
```

其他宿主使用相应的 `-a` 值；推荐保留 `-g`，采用个人级安装。
不带 `-g` 的项目级安装本身仍会向项目添加 Skill 文件，虽然运行时的知识数据已经全部外置。
审查不可信 PR 时使用独立可信的个人级安装，
避免把 PR 修改的 Skill 指令当成可信工具。安装器可复制或链接 Skill 目录，
宿主应从实际加载位置定位脚本，不能假定总是在项目 `.github` 下。

然后在目标项目会话中告诉 Copilot：

> 使用 review-memory，同步 acme/my-project 的 PR，并累积评审知识。

宿主会准备隔离 Python 依赖、绑定项目、分批同步已合并 PR、读取证据、
提炼并保存候选，再更新累积知识索引。后续说“继续同步这个项目”即可恢复。
不需要另行安装 review-memory wheel，不需要另配模型 API Key。

**安装命令本身不启动任务或后台服务。** 自动化发生在宿主执行 Skill 时。
需要带 `venv`/`ensurepip` 的 Python 3.11+；在线同步还需要 GitHub CLI 和已有的登录授权。
依赖准备只使用本地文件，仍遵守宿主的工具批准机制；下载 Skill 和读取 GitHub 仍需要联网。
不会偷偷安装 gh、申请权限或操作私钥。

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
  wheels\
    pyyaml-6.0.3-py3-none-any.whl
    LICENSE.PyYAML.txt
    provenance.json
  scripts\
    main.py
    review_memory\
  references\
  packs\
```

脚本、知识包、固定依赖 wheel 及其许可证和来源记录随 Skill 一起安装。
`main.py setup` 使用标准库创建用户缓存中的隔离环境，**不访问 PyPI，直接安装本地 wheel**。
`requirements.txt` 固定版本与 SHA256，启动器和 pip 都会校验哈希；
安装不使用包索引、全局 pip 缓存、依赖解析或源码构建，也不会回退到其他下载源。
纯 Python wheel 不需要编译器或特定平台的二进制文件。
它由固定的上游 PyYAML 源码构建，并非冒充未经修改的官方 PyPI wheel；
`wheels/provenance.json` 记录来源和构建信息，同时保留 MIT 许可证。

日常命令执行当前 Skill 携带的代码，不能回退到全局同名模块。宿主负责准备环境，
不要求用户自己寻找路径和执行 pip。新机器应重建环境，不能直接复制虚拟环境。
依赖包缺失或哈希不符时，需要重新安装完整、可信的 Skill，不会临时联网补包。

如果旧版曾在 `files.pythonhosted.org` 下载失败，更新助手实际加载位置的 Skill 后再运行
`setup`。新依赖包会使用不同的缓存键，不复用旧版下载失败的环境。
如果新的离线安装被中断，先确认没有进程使用对应目录，再按启动器提示修复那个具体目录
或过期锁；不要清空整个缓存、删除项目知识或降低 TLS 安全性。

## 独立项目数据

默认存储根目录为 `~\.review-memory\projects\<项目名>-<路径哈希>`。
项目命令返回准确的 `storage_root` 和 `local_path`；数据既不在目标仓库，也不在 Skill 安装目录。
规范化的本地路径决定独立空间，GitHub 仓库绑定另行检查，同名但不同路径的项目不会混用知识。

可在命令前加全局选项 `--data-home 绝对路径`，或在宿主环境中设置 `REVIEW_MEMORY_HOME`，
指定其他外部父目录。它不能位于目标仓库或 Skill/源码目录内。
长期知识目录与首次启动使用的依赖缓存分离。

| 数据 | 相对于 storage_root 的路径 |
| --- | --- |
| 项目绑定 | `.review/config.yaml` |
| 本地目标路径标识 | `.review/project.json` |
| 可恢复同步队列 | `.review/local/state/sync.json` |
| 原始证据和学习任务 | `.review/local/raw`、`.review/local/proposals/tasks` |
| 模型提炼的候选 | `.review/local/proposals` |
| 累积知识索引（未批准） | `.review/local/learning/index.json` |
| 人工批准的规则 | `.review/knowledge`、`.review/approvals` |

移动或重装 Skill 时，宿主重新解析安装位置，独立数据目录保持原样。
目标项目换到不同的规范化路径后会选择新的独立空间。
当前未发布版本不做旧布局兼容或迁移：目标中的旧 `.review` 不读取、不移动、不删除。
任务和证据的相对路径应基于 `storage_root` 解析，不要基于目标或 Skill 目录；
不要修改历史签名或 task 哈希。

学习阶段不需要知识目录有 Git 历史。正式规则审查前，由维护者把外部存储根目录初始化为
独立 Git 仓库，将审阅过的策略、签名记录和授权配置提交到那里。
`--trusted-ref` 指向这个知识仓库的提交，base/head 仍来自只读的目标代码仓库。
`.review/local` 和本地路径绑定文件只在外部知识仓库中被忽略，不改变目标的忽略文件。

同一安装也支持 feature 实现：CLI 准备项目上下文，宿主在用户明确授权后实际修改代码、
运行获准的检查，再通过 `feature-finish` 记录结果。CLI 本身仍不写目标代码或执行项目命令，
完成记录明确标注为宿主报告，不是 CLI 独立证明实现正确。

`approval-queue` 提供可阅读的候选交接，`prepare-approval` 为选中的知识生成未签名请求。
它们不会签名、授信或自动提交，详见[审批流程](../.github/skills/review-memory/references/approval.md)。
不要把私有知识目录打进 Skill 发布包。运行时变更会使已有审批绑定失效，
需要维护者重新审批，不能通过改签名或放宽校验绕过。

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

仓库/npx 安装、源码发行包和 Skill ZIP 都必须包含 wheel、许可证、来源记录，
以及哈希匹配的依赖声明。更新依赖时，按来源记录中的构建方式生成并检查新的 wheel，
同步更新哈希和来源信息。运行时或依赖变更导致已有绑定不匹配时，需要维护者重新审批，
不能修改旧签名来迁就升级。

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
