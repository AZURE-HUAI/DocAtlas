# DocAtlas Issues

这里保存问题与增强的长期记录。当前事项只读 `unresolved`；历史证据按编号查
`resolved`。操作规则见 [`WORKFLOW.md`](WORKFLOW.md)。

## 状态

| 目录 | 状态 | 含义 |
|---|---|---|
| `unresolved` | `open` / `investigating` / `in_progress` / `blocked` / `discussion` | 待处理 |
| `resolved` | `resolved` | 已完成并验证 |
| `resolved` | `closed` | 重复、不处理或不再适用 |

## 未解决问题

| 编号 | 标题 | 状态 | 优先级 |
|---|---|---|---|
| — | 暂无 | — | — |

## 已解决问题

| 编号 | 标题 | 状态 | 优先级 |
|---|---|---|---|
| <a id="bug-001"></a>[BUG-001](bugs/resolved/0001-ask-overview-timeout.md) | `ask` 查询宽泛版本概览时超时且没有进度输出 | resolved | high |
| <a id="bug-002"></a>[BUG-002](bugs/resolved/0002-ask-exact-query-slow-low-relevance.md) | 精确 `ask` 查询曾出现性能与排序问题 | resolved | high |
| <a id="bug-003"></a>[BUG-003](bugs/resolved/0003-set-field-of-view-not-found.md) | 本地搜索没有命中已存在的 `Set Field Of View` 蓝图 API 页面 | resolved | high |
| <a id="bug-004"></a>[BUG-004](bugs/resolved/0004-blueprint-property-setter-discovery.md) | 蓝图属性 Setter 难以按节点显示名检索和关联 | resolved | medium |
| <a id="bug-005"></a>[BUG-005](bugs/resolved/0005-related-empty-result-ambiguous.md) | `related` 用空数组同时表示多种失败状态 | resolved | medium |
| <a id="bug-006"></a>[BUG-006](bugs/resolved/0006-inventory-validation-allows-empty-datasets.md) | inventory 验收会把空数据集和空分类判为通过 | resolved | high |
| <a id="bug-007"></a>[BUG-007](bugs/resolved/0007-sample-per-category-redistributes-quota.md) | `sample-per-category` 会把分类缺额补抓到其他分类 | resolved | medium |
| <a id="bug-008"></a>[BUG-008](bugs/resolved/0008-ask-pending-pages-require-route-slug.md) | `ask` 无法按官方页面名补抓 route slug 不完全相同的 pending 页面 | resolved | high |
| <a id="bug-009"></a>[BUG-009](bugs/resolved/0009-non-ue-datasets-mislabeled-as-ue.md) | 非 Unreal 数据集仍被标记为 UE 和 ue_version | resolved | medium |
| <a id="bug-010"></a>[BUG-010](bugs/resolved/0010-linked-headings-generate-broken-source-anchors.md) | 含 Markdown 链接的标题会生成不可跳转的来源锚点 | resolved | medium |
| <a id="bug-011"></a>[BUG-011](bugs/resolved/0011-blender-inventory-misses-node-workflow-pages.md) | Blender 数据集清单遗漏节点工作流所需的跨目录基础页 | resolved | high |
| <a id="bug-012"></a>[BUG-012](bugs/resolved/0012-class-members-are-not-entities.md) | 类页面成员表里的属性和方法没有成为实体 | resolved | high |
| <a id="bug-013"></a>[BUG-013](bugs/resolved/0013-linked-pages-never-enter-the-inventory.md) | 范围内正文引用到的页面永远进不了清单 | resolved | high |

## 未解决增强建议

| 编号 | 标题 | 状态 | 优先级 |
|---|---|---|---|
| — | 暂无 | — | — |

## 已解决增强建议

| 编号 | 标题 | 状态 | 优先级 |
|---|---|---|---|
| <a id="enh-001"></a>[ENH-001](enhancements/resolved/0001-skill-installer-portability.md) | Skill 安装器支持多个客户端 | resolved | medium |
| <a id="enh-002"></a>[ENH-002](enhancements/resolved/0002-skill-mcp-integration.md) | Skill 与 MCP 形成明确的组合入口 | resolved | medium |
| <a id="enh-003"></a>[ENH-003](enhancements/resolved/0003-generic-relations-extensible-domains.md) | 关系能力通用化并允许领域独立扩展 | resolved | low |
| <a id="enh-004"></a>[ENH-004](enhancements/resolved/0004-non-sitemap-inventory-sources.md) | 来源适配器支持非 sitemap 的页面清单 | resolved | medium |
| <a id="enh-005"></a>[ENH-005](enhancements/resolved/0005-cross-language-query-guidance.md) | AI/Skill 将用户语言转换为数据集语言后查询 | resolved | medium |
| <a id="enh-006"></a>[ENH-006](enhancements/resolved/0006-neutral-mcp-multi-dataset-contract.md) | 为 MCP 提供中立的多数据集路由与结构化交换合同 | resolved | high |
| <a id="enh-007"></a>[ENH-007](enhancements/resolved/0007-natural-language-candidate-fetch.md) | AI/Skill 将自然语言问题落成官方术语后查询 | resolved | low |
| <a id="enh-008"></a>[ENH-008](enhancements/resolved/0008-structured-version-intent-contract.md) | 版本意图的跨层结构化合同 | resolved | high |
| <a id="enh-009"></a>[ENH-009](enhancements/resolved/0009-fourth-domain-generalisation-acceptance.md) | 用陌生领域验收分层架构：Roblox Creator Hub | resolved | high |
| <a id="enh-010"></a>[ENH-010](enhancements/resolved/0010-related-safe-fetch-pending-targets.md) | `related` 安全补抓 pending 官方目标并增量建立关系 | closed | medium |

## 维护

- 新事项从 [`templates/`](templates/) 复制，放入对应 `unresolved`。
- 一个文件只处理一个可独立关闭的事项；建议不是强制实现方案。
- 完成后写明验证与解决记录，再移入 `resolved`。
- 提交前运行 `.\scripts\validate-issues.ps1`。
