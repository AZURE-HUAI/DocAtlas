# DocAtlas 建库与维护

用于新增版本、接入新站点、扩大收录范围、重加工和体检。程序位置：`{{DOCATLAS_ROOT}}`。

## 原则

1. 新站先做每类 20 页小样，验收后再全量。
2. 每步运行 `validate` 并检查实际数字，不只看退出码。
3. 新版本使用新 `id`，不复制旧数据库。
4. 测试与回归数据库默认保留，除非用户明确要求删除。
5. 只报告实测结果；失败和覆盖缺口要如实保留。

## 流程 A：加同站新版本

```powershell
Copy-Item datasets/{{DATASET_ID}}.toml datasets/<新 id>.toml
$env:DOCATLAS_DATASET='<新 id>'
python -m docatlas crawl --discovery-only
python -m docatlas validate --phase inventory
```

只改数据集身份、版本和版本相关来源地址。清单建好后即可按需使用；用户明确要求
完整离线库时才运行全量 `crawl`。清单为空或明显偏少时，按流程 B 处理。

## 流程 B：加新站点，或扩大收录范围

先确认页面清单、正文格式、正式 URL、语言和版本来源，再新增：

- `datasets/<id>.toml`
- `docatlas/sources/<source>.py`
- 来源适配器测试

适配器最少要完成：

| 能力 | 接口 |
|---|---|
| 枚举页面 | sitemap 接口，或 `inventory_feeds` + `read_feed` |
| URL 统一 | `normalize_location`、`canonical_url`、`document_request_url` |
| 正文与链接 | `parse_document`、`normalize_link_target` |
| 边界与分类 | `is_official_url`、`categorize_path` |
| 实体归属 | `entity_placement` |

按需实现 `document_locale`、`page_members`、`version_marks` 和
`version_sort_key`。配置至少声明数据集身份、语言、来源、分类、实体类型和
Skill 触发词。

注意：

- "是否官方地址"和"是否纳入本数据集"是两件事，不能共用一个判断。
- 正文站内链接必须统一成固定版本正式地址，否则关系无法对上清单。
- 类页面内的成员可通过 `page_members` 提升为独立实体。

### 缺页时，先分清是哪一种缺

| 现象 | 处理 |
|---|---|
| 清单里有，正文没抓 | `ask` / `get` 按需补抓即可，不是缺页 |
| 已收正文链过去，清单里没有 | 配 `[inventory].referenced_category`，一跳引用闭包自动收 |
| 站点从不链过去 | 闭包够不到，只能由数据集显式声明目录 |

第三种要先用实测数字确认，不要凭感觉扩范围。例：Blender 的
`editors/shader_editor` 在 126 个 shader 节点页里被引用 **0** 次，所以闭包永远
到不了它。

声明时用最小范围。一个分类可以写多个目录，专门用于收"分散在别处、但属于同一件
事"的少量页面，而不是把整个上级目录一锅端：

```toml
[categories]
shader_nodes = "render/shader_nodes/"
# 只要这两个编辑器；整个 editors/ 有 201 页，含视频序列器等无关内容。
node_editors = ["editors/shader_editor", "editors/geometry_node"]
```

改完范围要 `crawl --discovery-only --refresh-sitemaps`——不加这个参数，
已成功的清单入口不会重读，新目录进不来。

## 领域关系

先使用通用官方链接和页面归属关系。只有关系依赖产品专属语义时，才新增
`docatlas/knowledge/<name>.py`：

```python
def relation_rules(graph):
    for source, target, _ in graph.name_matches("ui_node", "api_symbol"):
        yield RelationCandidate(
            source=source,
            target=target,
            relation_type="node_api",
            evidence_kind="exact_name",
            confidence=0.9,
        )
```

领域包只生成候选，不写 SQL、不依赖数据库 ID。通用核心负责验证、去重、存储、
全量与增量更新。没有领域包时，通用关系仍应可用。

没有官方独立目标的自动生成节点只做检索别名；只有双方实体和证据真实存在时才建关系。

## 小样验收

```powershell
$env:DOCATLAS_DATASET='<id>'
python -m docatlas crawl --discovery-only
python -m docatlas validate --phase inventory
python -m docatlas crawl --skip-discovery --sample-per-category 20
python -m docatlas validate --phase content
python -m docatlas ask "<明确存在的官方术语>" --token-budget 1500
```

检查正文、标题、原出处、分类、语言、关系和缺页报告。新领域还要通过同一 MCP
连接验证 `dataset_id` 路由，不得修改 MCP 或通用关系核心才能接入。

## 重加工

- 切分规则变化：增加 `CHUNKER_VERSION`，运行 `python -m docatlas reprocess`。
- 仅关系规则变化：运行 `python -m docatlas cross-index`。
- 两者都要运行 `python -m docatlas validate --phase content`，重点检查
  `relation_evidence_coverage` 和 `inventory_link_coverage`。

## 体检与迁移

```powershell
.\docatlas.ps1 status
python -m docatlas validate --phase content
```

项目移动或 Skill 内容变化后重新安装：

```powershell
.\scripts\install-skill.ps1
```
