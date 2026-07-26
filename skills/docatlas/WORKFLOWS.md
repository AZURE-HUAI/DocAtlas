# DocAtlas 建库与维护流程

**什么时候读这份**：加新版本、加新文档站、改了加工规则要重来、或要做体检。
日常查资料不需要，`SKILL.md` 就够了。

当前装着《{{DATASET_NAME}}》（id `{{DATASET_ID}}`），下面拿它当参照，流程本身
跟具体产品无关。程序位置：`{{DOCATLAS_ROOT}}`，所有命令在这个目录下执行。

---

## 四条铁律

1. **先小样，再全量。** 全站可能有几十万页，抓错了要重来一整天。新站点一律先
   `crawl --sample-per-category 20`，验收过了再放开。`--sample-per-category N`
   是**每一类最多 N 页**，不是总共 N × 类数；已成功的页面算在额度里，同一条
   命令跑第二遍不会越抓越多。

2. **每一步都验收，看数字，不看退出码。**"跑完没报错"骗过人。`validate` 输出
   JSON，逐项看 `status`。重点看两项，专门抓"空得很整齐"的库：
   - `inventory_not_empty` —— 一页都没有的空库不算通过。
   - `declared_categories_have_pages` —— 配置声明了某一类却一页都没枚举到，
     几乎总是 `[categories]` 匹配片段写错了。确实可能为空的分类写进
     `optional_categories`，别为了变绿把检查删掉。

3. **不动用户已有数据。** 新数据集用新 `id`，自动生成新目录、新数据库。
   **永远不要**把旧库数据目录复制过去当新版本用——里面的正文是旧版本的。

4. **如实报告。** 抓了多少页、验收哪几项通过，报实测数字；失败就说失败，
   不说"应该没问题"。

---

## 流程 A：加同一个站点的新版本

触发：用户说"升到新版本""再加一个 X.Y 版的库"。**不用写代码**——同一站点新
版本页面结构一般不变。

1. 复制配置，只改 `id`、`name`、`version`、`[source_options].home_path`
   （文档首页路径通常带版本号），其余别动：
   ```powershell
   Copy-Item datasets/{{DATASET_ID}}.toml datasets/<新 id>.toml
   ```
2. 枚举全站清单（只读站点地图，不抓正文，几分钟）：
   ```powershell
   $env:DOCATLAS_DATASET='<新 id>'; python -m docatlas crawl --discovery-only
   ```
3. 验收清单阶段：
   ```powershell
   $env:DOCATLAS_DATASET='<新 id>'; python -m docatlas validate --phase inventory
   ```
4. **到这里就能用了**——查到本地没有的页面会当场补抓，不必先下载全站。告诉
   用户切换方式：`$env:DOCATLAS_DATASET='<新 id>'`。
5. 用户明确要"全都下下来"才跑全量 `crawl`（慢，建议后台跑）。

清单枚举出 0 页或少得离谱，说明站点改版了、路径规则变了——不是流程 A，转
流程 B 改适配器。

---

## 流程 B：加一个新的文档站点

触发：用户说"把某某站的文档也收进来"（另一个产品、另一个官网）。**不是一条
命令的事**，先摸清那个站怎么组织，再写适配器；摸不清就先问用户，不要瞎猜。

### B1. 先侦查

- **怎么知道有哪些页面**：`sitemap.xml`？没有的话分页 API、目录页，或
  Sphinx `searchindex.js` 这类静态索引？
- **正文怎么拿**：有结构化数据接口最省事，没有就得解析 HTML。
- **语言怎么选**：URL 带 `lang=`？路径前缀？还是只有一种语言？

### B2. 写适配器

新建 `docatlas/sources/<名字>.py`（照现成的抄结构最快），核心函数：

| 函数 | 干什么 |
|---|---|
| `sitemap_index_url(dataset)` | 站点地图总入口（没有站点地图就不写，见下） |
| `categorize_sitemap(dataset, url)` | 子地图属于哪一类；不要的返回 `None` |
| `normalize_location(dataset, location)` | URL → `(标准路径, 正式地址)`；滤掉别的语言和非文档页 |
| `canonical_url(dataset, path)` | 给人看、给引用用的正式地址 |
| `document_request_url(dataset, path)` | 真正去要内容的地址 |
| `parse_document(dataset, path, body)` | 内容 → 标题、正文 Markdown、小节 |
| `normalize_link_target(dataset, url)` | 正文链接 → 本站路径（建交叉关系用） |
| `is_official_url(dataset, url)` | 是不是官方地址（影响内容质量分） |
| `entity_placement(dataset, category, segments)` | 路径片段 → 模块 / 归属类型 |
| `document_locale(payload)` | **服务器实际给的语言**，没有返回 `None`——别省，站点没那个语言时通常不报错、默默给默认语言，不判断就会得到一个标着甲语言、装着乙语言的库 |

站点没有 sitemap 时，改实现这两个，上表前两行整个不用写：

| 函数 | 干什么 |
|---|---|
| `inventory_feeds(dataset)` | 返回 `[(清单入口地址, 分类或 None)]` |
| `read_feed(dataset, url)` | 一个入口 → `[(分类或 None, 页面地址)]`；翻页/限流/重试自己处理 |

并发、写库、失败诊断、`inventory` 验收一律复用核心，不用动。分类优先取条目
自己给的，其次才是入口所属的。

### B3. 写配置

新建 `datasets/<id>.toml`（照 `datasets/{{DATASET_ID}}.toml` 结构）。必填
`id`/`name`/`product`/`version`/`language`/`source`、`[categories]`（站点地图
URL 片段 → 分类）、`[entity_types]`，别忘了 `[skill] triggers`（AI 靠它判断该
不该唤起这个知识库）。

`knowledge` 可留空——没有领域知识包一样能抓能搜，只是少了该领域特有的线索
（同义词归并、"作用在什么类型上"这类推断）。先跑通再考虑加，参照
`docatlas/knowledge/` 下现成的。

### B4. 小样验收（不能跳）

```powershell
$env:DOCATLAS_DATASET='<id>'
python -m docatlas crawl --discovery-only
python -m docatlas validate --phase inventory
python -m docatlas crawl --skip-discovery --sample-per-category 20
python -m docatlas validate --phase content
python -m docatlas ask "<那个站里一定有的东西>"
```

`ask` 这步自己实际看几条：正文有没有 HTML 残留、标题层级对不对、原出处 URL
点开是不是那一页。不对就回去改适配器，**别带着脏数据跑全量**。

### B5. 放开全量

小样干净了才 `crawl`。页数多就后台跑，跑完再 `validate --phase content`。

---

## 流程 C：改了加工规则，要重来

触发：改了切分逻辑、别名规则、关系推导。

- 改了**切分**（`chunking.py`）：`docatlas/constants.py` 里
  `CHUNKER_VERSION` 加一，然后 `python -m docatlas reprocess`——只处理还没
  升级到当前版本的页，断了重跑接着做，跑完自动重建交叉关系。
- 只改了**关系推导**（`crossindex.py` 或知识包的 `build_relations`），切分没
  动：单跑 `python -m docatlas cross-index` 就够。
- 两种情况都要 `python -m docatlas validate --phase content`，**重点看
  `relation_evidence_coverage`**——它专抓"某类关系被整类做没"，这种错不会报
  任何异常，其余检查照样全过。

---

## 流程 D：例行体检

```powershell
.\docatlas.ps1 status                          # 抓了多少、失败多少
python -m docatlas validate --phase content    # 逐项过数据合同
```

任何一项 `fail`，把那一项的 `requirement` 原文念给用户听——写的就是哪里不对。

---

## 流程 E：项目搬了地方 / 改了名

技能文件里写着程序的绝对路径，搬完必须重装：

```powershell
.\scripts\install-skill.ps1
```

---

## 常见岔路

| 现象 | 多半是 | 怎么办 |
|---|---|---|
| 清单枚举出 0 页 | 清单入口地址或分类片段不对 | `inventory_not_empty` 会直接报出来；打开入口看结构，对 `[categories]` |
| 某一类是 0 页 | 那一类的匹配片段写错了 | 看 `declared_categories_have_pages` 说的是哪一类 |
| 抓回来正文是空的 | `parse_document` 没认出正文结构 | 存一份原始返回下来看 |
| `validate` 报语言不符 | `language` 那个站不支持，被默默换了 | 改成站点真有的语言，或确认它只有一种 |
| 查什么都查不到 | 清单没冻结，或用错了数据集 | `python -m docatlas paths` 看当前是哪个 |
| 某类关系突然归零 | 加工规则改动打断了别处的假设 | 看 `relation_evidence_coverage` 说缺哪一类 |
