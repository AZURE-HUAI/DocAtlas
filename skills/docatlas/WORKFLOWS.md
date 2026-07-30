# DocAtlas 建库与维护

程序位置：`{{DOCATLAS_ROOT}}`。所有命令在该目录下运行。

## 用途

| 场景 | 去哪一节 |
|---|---|
| 同一站点加新版本 | [加新版本](#加新版本) |
| 接入一个新站点 | [接入新站点](#接入新站点) |
| 已有库收得不全 | [扩大收录范围](#扩大收录范围) |
| 改了切分或关系规则 | [重加工](#重加工) |
| 想知道库现在什么状态 | [体检](#体检) |
| 项目挪了位置，或改了本手册 | [重新安装](#重新安装) |

## 原则

1. 新站先做每类 20 页小样，验收通过再全量。
2. 每一步跑 `validate` 并**看实际数字**，不只看命令有没有报错。
3. 新版本用新的数据集 id，不要复制旧数据库。
4. 只报告实测结果；失败和覆盖缺口如实保留，不要掩盖。

## 加新版本

同一个站点、换一个版本号时用这个流程。

```powershell
Copy-Item datasets/<现有 id>.toml datasets/<新 id>.toml
$env:DOCATLAS_DATASET='<新 id>'
python -m docatlas crawl --discovery-only
python -m docatlas validate --phase inventory
```

新 TOML 里只改数据集身份、版本号和与版本相关的来源地址。清单建好后即可按需
查询；用户明确要求完整离线库时才跑全量 `crawl`。

清单为空或页数明显偏少，说明来源规则对不上，按[接入新站点](#接入新站点)排查。

## 接入新站点

先确认这个站点的页面清单从哪来、正文什么格式、正式 URL 长什么样、语言和版本
怎么标。然后新增三样：

- `datasets/<id>.toml`
- `docatlas/sources/<source>.py`
- 该适配器的测试

适配器必须实现：

| 能力 | 接口 |
|---|---|
| 枚举页面 | sitemap 接口，或 `inventory_feeds` + `read_feed` |
| URL 统一 | `normalize_location`、`canonical_url`、`document_request_url` |
| 正文与链接 | `parse_document`、`normalize_link_target` |
| 边界判断 | `is_official_url` |
| 实体归属 | `entity_placement` |
| 摘要 | `parse_document` 的 `description` 用 `htmlmd.lead_sentence(markdown)` 取，不要自己写 |

按需实现：`document_locale`、`page_members`、`version_marks`、`version_sort_key`、
`categorize_path`。

TOML 至少声明数据集身份、语言、来源适配器、分类、实体类型和触发词。

三条容易写错的：

- "是否官方地址"和"是否纳入本数据集"是两件事，不能共用一个判断。
- 正文里的站内链接必须统一成固定版本的正式地址，否则关系对不上清单。
- 类型页表格里的成员可以通过 `page_members` 提升为独立实体。

### 领域关系（可选）

通用的官方链接和页面归属关系默认就有。只有关系依赖该产品专有语义时，才新增
`docatlas/knowledge/<name>.py`：

```python
def relation_rules(graph):
    for source, target, _ in graph.name_matches("<类型A>", "<类型B>"):
        yield RelationCandidate(source=source, target=target,
                                relation_type="<关系名>",
                                evidence_kind="exact_name", confidence=0.9)
```

领域包只产出候选，不写 SQL、不碰数据库 ID；验证、去重、存储由通用核心负责。
没有领域包时通用关系照常工作。只有双方实体和证据都真实存在时才建立关系。

## 扩大收录范围

先分清缺的是哪一种：

| 现象 | 处理 |
|---|---|
| 清单里有，正文没抓 | `ask` / `get` 按需补抓即可，不算缺页 |
| 已收正文链过去，清单里没有 | 配 `[inventory].referenced_category`，引用闭包自动收 |
| 站点从不链过去 | 闭包够不到，只能在数据集里显式声明目录 |

判断属于第三种前，**先用实测数字确认**该目录确实没有被引用到，不要凭感觉扩范围。

声明时用最小范围。一个分类可以写多个目录，用来收"分散在别处但属于同一件事"的
少量页面，而不是把整个上级目录一锅端：

```toml
[categories]
<分类名> = "<目录前缀>/"
<另一个分类> = ["<目录A>", "<目录B>"]
```

`referenced_category` 声明的那一类**不要**同时写进 `[categories]`。那张表是
"分类 → 路径前缀"的枚举规则，而引用闭包收的正是声明目录之外的页面，没有前缀可
写；写空串会让前缀匹配恒真，整个库都被判成这一类。它只需要在 `[category_labels]`
里有一个显示名，就是能过滤、能抽样、能导出的正式分类。

**改完范围必须加 `--refresh-sitemaps` 重跑发现**，否则已成功的清单入口不会重读：

```powershell
python -m docatlas crawl --discovery-only --refresh-sitemaps
```

## 小样验收

新站点或大改之后，先跑小样再全量。

```powershell
$env:DOCATLAS_DATASET='<id>'
python -m docatlas crawl --discovery-only
python -m docatlas validate --phase inventory
python -m docatlas crawl --skip-discovery --sample-per-category 20
python -m docatlas validate --phase content
python -m docatlas ask "<确定存在的官方术语>" --token-budget 1500
```

逐项检查：正文、标题、原出处地址、分类、语言、关系、缺页报告。

新站点还要通过同一个 MCP 连接验证 `dataset_id` 能路由过去——接入一个新站点
不应该需要改 MCP 或通用关系核心。

## 重加工

用已保存的原文重新加工，不联网，可续传。

| 改了什么 | 跑什么 |
|---|---|
| 切分规则 | 先给 `CHUNKER_VERSION` 加一，再 `python -m docatlas reprocess` |
| 只改了关系规则 | `python -m docatlas cross-index` |

两种情况都要接着跑 `python -m docatlas validate --phase content`，重点看
`relation_evidence_coverage` 和 `inventory_link_coverage`。

`reprocess` 默认只处理还没升级到当前规则版本的页面，所以中断后重跑就是续传；
要全部重做加 `--force`。

## 体检

```powershell
python -m docatlas validate --phase content
```

`validate` 报切分规则版本不符，说明程序更新过而本地库还是按旧规则切的，跑一次
`python -m docatlas reprocess` 即可。

## 重新安装

项目移动、改名，或本手册与 `SKILL.md` 内容变化后，重新运行安装脚本：

```powershell
python install.py
```

脚本会**自己检测本机装了哪些支持的客户端**，只往检测到的那些里写技能副本和
MCP 配置，然后自检一遍。装到了哪几个以脚本的输出为准——不要预先假设某个客户端
一定在、也不要手动往客户端目录里拷文件。

技能副本和 MCP 配置里写着仓库的实际路径，都由脚本生成。**路径不要手写。**

常用开关：`--dataset <id>` 指定默认数据集，`--data-dir <路径>` 指定数据位置，
`--print` 只打印 MCP 配置片段不改任何文件。
