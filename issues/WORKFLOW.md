# Issue 生命周期

问题和增强使用相同流程，只是分别保存在 `bugs` 与 `enhancements` 下。

```text
GitHub Issue 或本地发现
          ↓
    unresolved 档案
          ↓
       分支与 PR
          ↓
    验证并记录结论
          ↓
      resolved 封存
          ↓
   合并 PR / 关闭 Issue
```

## 1. 两个入口，一个闭环

GitHub Issue 是在线协作入口，适合提交、讨论、分配负责人和查看开放状态。本目录
是长期技术档案，适合 AI 精确读取复现、调查、验证和历史决策。

两者按内容分工，不互相复制全部正文：

- 实时协作状态以 GitHub Issue 为准；
- 技术事实、测试证据和解决历史以本地档案为准；
- 进入开发阶段时互相链接；GitHub Issue 回链 `issues/README.md` 中对应编号的
  稳定锚点，不直接链接会移动的档案路径；
- PR 合并或 Issue 关闭前，先把最终结论同步到档案。

只有本地高强度测试、不需要在线讨论时，可以只创建本地档案。需要协作或排期时，
再补 GitHub Issue。

## 2. 新建议

### 从 GitHub 开始

1. 使用 `.github/ISSUE_TEMPLATE/` 中的问题或增强表单；
2. 初步确认值得跟踪后，从 `templates/` 复制对应模板；
3. 在 `unresolved/` 创建独立档案并分配下一个稳定编号；
4. 在档案的 `github_issue` 字段记录 GitHub Issue 链接；在 GitHub Issue 中
   回链 `https://github.com/AZURE-HUAI/DocAtlas/blob/main/issues/README.md#bug-001`
   这类稳定编号锚点。

### 从本地测试开始

1. 从 `templates/` 复制对应模板；
2. 在 `unresolved/` 创建独立档案并分配下一个稳定编号；
3. 更新 `issues/README.md`；
4. 需要在线协作时再创建 GitHub Issue，并按上面的稳定锚点建立双向链接。

不要在索引中直接追加长正文；一个档案只处理一个可独立关闭的事项。

## 3. 调查与处理

文件仍留在 `unresolved/`，按进度更新 `status`。调查事实、推测、可能方向和验证
结果分开记录。GitHub 可使用标签、负责人或 Project 表达排期，不必把这些在线字段
全部复制到档案。

默认上下文只包括：

1. `issues/README.md`；
2. 当前要处理的一个或少量 `unresolved` 文件；
3. 它们明确引用的必要代码或相关议题。

不要为了了解当前工作而批量读取 `resolved/`。只有发生回归、需要历史决策或核对
旧验证证据时，才精确打开对应封存文件。

## 4. 分支与 Pull Request

从 `main` 创建内容单一的分支。PR 应包含：

1. 改动目的和范围；
2. GitHub Issue 编号和本地档案编号；
3. 实际执行的验证命令与结果；
4. 必要的档案更新。

有 GitHub Issue 时，在 PR 描述中使用 `Closes #编号`。不要提前手工关闭 Issue；
PR 合并到默认分支后由 GitHub 自动关闭。PR 创建后，将其 URL 写入档案的
`fix_pr`；如果创建 PR 前还不知道 URL，就追加一个只更新该字段的提交。

## 5. 完成与封存

完成后先在原文件中：

1. 将 `status` 改为 `resolved`，或在明确不处理时改为 `closed`；
2. 将 `lifecycle` 改为 `resolved`；
3. 填写 `resolved_at`；
4. 写明解决摘要、实际改动和验证结果；
5. 记录 GitHub Issue 和 PR；
6. 更新 `issues/README.md` 的索引。

然后把文件从 `unresolved/` 移入同类的 `resolved/`。不要删除历史议题，也不要只
移动文件而遗漏状态和验证信息。代码修复时，这次移动应放进解决问题的同一个 PR，
让代码、验证和档案一起接受审查。GitHub Issue 始终链接稳定编号锚点，因此移动
档案时不需要修改外部回链。

## 6. 重新打开

如果问题复现，将文件移回 `unresolved/`，把 `lifecycle` 改回 `unresolved`，
状态改为 `open` 或 `investigating`，并追加重新打开的日期和新证据。保留旧解决
记录，不要覆盖历史；同时重新打开原 GitHub Issue，或创建新 Issue 并说明回归关系。

## 7. 状态映射

| 本地目录 | 本地状态 | GitHub 状态 |
|---|---|---|
| `unresolved` | `open` / `investigating` / `in_progress` / `blocked` / `discussion` | Open |
| `resolved` | `resolved` | Closed / completed |
| `resolved` | `closed` | Closed / not planned、duplicate 或不再适用 |

## 8. 提交前检查

```powershell
.\scripts\validate-issues.ps1
```

该检查验证编号唯一性、元数据格式与允许值、目录与生命周期的一致性、关联编号、
索引内容与稳定锚点、解决记录、相对链接和表单基础结构。GitHub Actions 只在相关
协作文件变化时运行同一检查。

## 9. 上下文保护原则

- `unresolved` 是当前工作集，应保持小而清晰。
- `resolved` 是可检索档案，不是默认上下文。
- 索引只提供摘要和链接，不复制议题正文。
- 一个议题只处理一个可独立关闭的事项。
- 相关但不同的问题使用 `related` 连接，不合并成长篇总记录。
- GitHub 评论不整段复制进档案，只沉淀已经验证的事实、决策和必要出处。
