# DocAtlas Issues

这里用独立 Markdown 文档保存问题和增强建议的长期技术记录。GitHub Issues 提供
在线提交、讨论、分工和开关状态；本目录保存稳定编号、复现证据、设计背景、
验证结果和解决档案。

为保护工作上下文，未解决与已解决内容物理分开。日常排查默认只读取本索引和
相关的 `unresolved` 文件；`resolved` 是历史证据库，不应整批加入当前上下文。

完整迁移规则见 [`WORKFLOW.md`](WORKFLOW.md)。

## 从哪里开始

- 在线报告或讨论：在 GitHub 的 **New issue** 页面选择“报告问题”或“提议增强”。
- 本地高强度测试：直接从 [`templates/`](templates/) 复制模板，写入对应的
  `unresolved/`。
- 开始开发：阅读仓库根目录的 [`CONTRIBUTING.md`](../CONTRIBUTING.md)。
- 提交改动前：运行 `.\scripts\validate-issues.ps1`。

## GitHub 与本目录的分工

| 内容 | 主要位置 |
|---|---|
| 在线讨论、负责人、标签、开放或关闭状态 | GitHub Issue |
| 完整复现、调查证据、设计上下文、验证与解决记录 | 本目录的独立文档 |
| 代码改动、审查和自动关闭 Issue | Pull Request |
| 已完成事项的长期历史 | `resolved/` |

实时协作状态以 GitHub Issue 为准，技术事实和历史证据以本目录档案为准。进入开发
阶段的事项应互相链接；GitHub Issue 应链接到本索引中对应编号的稳定锚点，而不是
会在封存时移动的档案路径。关闭前要同步解决结论和验证结果，避免两边内容冲突。

## 状态约定

| 生命周期目录 | 可用状态 | 含义 |
|---|---|---|
| `unresolved` | `open` | 已确认或待处理 |
| `unresolved` | `investigating` | 正在定位原因 |
| `unresolved` | `in_progress` | 已开始修改 |
| `unresolved` | `blocked` | 等待外部条件或决策 |
| `unresolved` | `discussion` | 设计讨论，尚未形成方案 |
| `resolved` | `resolved` | 已完成并验证 |
| `resolved` | `closed` | 重复、不处理或不再适用，原因已记录 |

## 未解决问题

| 编号 | 标题 | 状态 | 优先级 |
|---|---|---|---|
| <a id="bug-011"></a>[BUG-011](bugs/unresolved/0011-blender-inventory-misses-node-workflow-pages.md) | Blender 数据集清单遗漏节点工作流所需的跨目录基础页 | in_progress | high |

## 已解决问题

| 编号 | 标题 | 状态 | 优先级 |
|---|---|---|---|
| <a id="bug-001"></a>[BUG-001](bugs/resolved/0001-ask-overview-timeout.md) | `ask` 查询宽泛版本概览时超时且没有进度输出 | resolved | high |
| <a id="bug-002"></a>[BUG-002](bugs/resolved/0002-ask-exact-query-slow-low-relevance.md) | 精确 `ask` 查询曾出现性能与排序问题 | closed | high |
| <a id="bug-003"></a>[BUG-003](bugs/resolved/0003-set-field-of-view-not-found.md) | 本地搜索没有命中已存在的 `Set Field Of View` 蓝图 API 页面 | resolved | high |
| <a id="bug-004"></a>[BUG-004](bugs/resolved/0004-blueprint-property-setter-discovery.md) | 蓝图属性 Setter 难以按节点显示名检索和关联 | resolved | medium |
| <a id="bug-005"></a>[BUG-005](bugs/resolved/0005-related-empty-result-ambiguous.md) | `related` 用空数组同时表示多种失败状态 | resolved | medium |
| <a id="bug-006"></a>[BUG-006](bugs/resolved/0006-inventory-validation-allows-empty-datasets.md) | inventory 验收会把空数据集和空分类判为通过 | resolved | high |
| <a id="bug-007"></a>[BUG-007](bugs/resolved/0007-sample-per-category-redistributes-quota.md) | `sample-per-category` 会把分类缺额补抓到其他分类 | resolved | medium |
| <a id="bug-008"></a>[BUG-008](bugs/resolved/0008-ask-pending-pages-require-route-slug.md) | `ask` 无法按官方页面名补抓 route slug 不完全相同的 pending 页面 | closed | high |
| <a id="bug-009"></a>[BUG-009](bugs/resolved/0009-non-ue-datasets-mislabeled-as-ue.md) | 非 Unreal 数据集仍被标记为 UE 和 ue_version | resolved | medium |
| <a id="bug-010"></a>[BUG-010](bugs/resolved/0010-linked-headings-generate-broken-source-anchors.md) | 含 Markdown 链接的标题会生成不可跳转的来源锚点 | resolved | medium |

封存说明见 [`bugs/resolved/README.md`](bugs/resolved/README.md)。

## 未解决增强建议

| 编号 | 标题 | 状态 | 优先级 |
|---|---|---|---|
| <a id="enh-003"></a>[ENH-003](enhancements/unresolved/0003-generic-relations-extensible-domains.md) | 关系能力通用化并允许领域独立扩展 | discussion | low |
| <a id="enh-005"></a>[ENH-005](enhancements/unresolved/0005-cross-language-query-guidance.md) | AI/Skill 将用户语言转换为数据集语言后查询 | discussion | medium |
| <a id="enh-006"></a>[ENH-006](enhancements/unresolved/0006-neutral-mcp-multi-dataset-contract.md) | 为 MCP 提供中立的多数据集路由与结构化交换合同 | in_progress | high |
| <a id="enh-007"></a>[ENH-007](enhancements/unresolved/0007-natural-language-candidate-fetch.md) | AI/Skill 将自然语言问题落成官方术语后查询 | discussion | low |

## 已解决增强建议

| 编号 | 标题 | 状态 | 优先级 |
|---|---|---|---|
| <a id="enh-001"></a>[ENH-001](enhancements/resolved/0001-skill-installer-portability.md) | Skill 安装器支持多个客户端 | resolved | medium |
| <a id="enh-002"></a>[ENH-002](enhancements/resolved/0002-skill-mcp-integration.md) | Skill 与 MCP 形成明确的组合入口 | resolved | medium |
| <a id="enh-004"></a>[ENH-004](enhancements/resolved/0004-non-sitemap-inventory-sources.md) | 来源适配器支持非 sitemap 的页面清单 | resolved | medium |
| <a id="enh-008"></a>[ENH-008](enhancements/resolved/0008-structured-version-intent-contract.md) | 版本意图的跨层结构化合同 | resolved | high |

封存说明见 [`enhancements/resolved/README.md`](enhancements/resolved/README.md)。

## 新建议

- 问题使用 [`templates/bug-report.md`](templates/bug-report.md)。
- 增强建议使用 [`templates/enhancement.md`](templates/enhancement.md)。
- 文件名使用四位序号和简短英文描述，例如
  `0006-related-on-demand-fetch.md`。
- 新议题先放入对应的 `unresolved` 目录。
- 索引中的编号使用稳定锚点，例如 `<a id="bug-999"></a>`；GitHub Issue
  回链该锚点，档案封存或重新打开时不需要修改外部链接。
- 一个文件只讨论一个可独立关闭的事项；相关事项通过 `related` 字段连接。
- 完成后补充解决记录和验证结果，再移动到对应的 `resolved` 目录。
- “期望结果”描述用户能够验证的结果，不预先规定内部实现。
- 具体实现想法放在“可能方向”中，并明确它们只是讨论参考。
- 有 GitHub Issue 或修复 PR 时，在档案中记录对应链接。
