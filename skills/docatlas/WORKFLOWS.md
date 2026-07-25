# DocAtlas 建库与维护流程

**什么时候读这份**：用户想加一个新版本、加一个新文档站、改了加工规则要重来、
或者要做体检。日常查资料不需要读这份，`SKILL.md` 就够了。

**为什么要有这份**：用户不该需要知道 TOML 长什么样、`reprocess` 和 `cross-index`
谁先谁后。他只说"帮我加个 UE 5.9 的库"，剩下的照下面做。

程序位置：`{{DOCATLAS_ROOT}}`。所有命令在这个目录下执行。

---

## 四条铁律

**1. 先小样，再全量。** 全站可能有几十万页，抓错了要重来一整天。
新站点一律先 `crawl --sample-per-category 20`，验收过了再放开。

**2. 每一步都验收，而且要看数字。** "命令跑完没报错"在这个项目里骗过三次了。
`validate` 输出的是 JSON，逐项看 `status`，别只看退出码。

**3. 不动用户已有的数据。** 新数据集用新的 `id`，它自动就是新目录、新数据库。
**永远不要**把旧库的数据目录复制过去当新版本用——里面的正文是旧版本的。

**4. 如实报告。** 抓了多少页、验收哪几项通过，报实测数字。
抓失败了就说失败了，不要说"应该没问题"。

---

## 流程 A：加同一个站点的新版本

**触发**：用户说"加个 UE 5.9 的库""升到新版本"。

这是最省事的一种，**不用写任何代码**。

1. 复制现成的配置，只改 4 个地方：

   ```bash
   cp datasets/epic-ue-5.8.toml datasets/epic-ue-5.9.toml
   ```

   改 `id`、`name`、`version`，以及 `[source_options]` 里的 `home_path`
   （文档首页路径通常带版本号）。**其余一行都别动。**

2. 枚举全站清单（只读站点地图，不抓正文，几分钟）：

   ```powershell
   $env:DOCATLAS_DATASET='epic-ue-5.9'; python -m docatlas crawl --discovery-only
   ```

3. 验收清单阶段：

   ```powershell
   $env:DOCATLAS_DATASET='epic-ue-5.9'; python -m docatlas validate --phase inventory
   ```

4. **到这里就能用了。** 查到本地没有的页面会当场补抓，不必先下载全站。
   告诉用户切换方式：`$env:DOCATLAS_DATASET='epic-ue-5.9'`。

5. 用户明确要求"全都下下来"时才跑全量 `crawl`（很慢，建议后台跑）。

**如果新版本的页面路径规则变了**（清单枚举出来是 0 页或少得离谱），
说明站点改版了，那就不是流程 A，转流程 B 改适配器。

---

## 流程 B：加一个新的文档站点

**触发**：用户说"把 Godot / Unity / React 的文档也收进来"。

**先跟用户说清楚：这一个不是一条命令的事**，需要先摸清那个站怎么组织，
再写一个适配器模块。摸不清就先别动手，不要瞎猜着写。

### B1. 先侦查，再动手

要弄明白三件事，弄不明白就问用户或直接去看那个站：

- **怎么知道有哪些页面**：有没有 `sitemap.xml`？没有的话有没有目录页可以爬？
- **正文怎么拿**：有没有像 Epic 那样的 JSON 接口？没有就得解析 HTML。
- **语言怎么选**：URL 里带 `lang=`？还是路径前缀 `/zh-cn/`？还是压根只有一种语言？

### B2. 写适配器

新建 `docatlas/sources/<名字>.py`，核心会调这些函数（照 `epic_ue.py` 抄结构）：

| 函数 | 干什么 |
|---|---|
| `sitemap_index_url(dataset)` | 站点地图总入口在哪 |
| `categorize_sitemap(dataset, url)` | 这份子地图属于哪一类；不要的返回 `None` |
| `normalize_location(dataset, location)` | 一条 URL → `(标准路径, 正式地址)`；滤掉别的语言和非文档页 |
| `canonical_url(dataset, path)` | 给人看、给引用用的正式地址 |
| `document_request_url(dataset, path)` | 真正去要内容的地址 |
| `parse_document(dataset, path, body)` | 拿回来的东西 → 标题、正文 Markdown、小节 |
| `normalize_link_target(dataset, url)` | 正文里的链接 → 本站路径（用于建交叉关系） |
| `is_official_url(dataset, url)` | 是不是官方地址（影响内容质量分） |
| `entity_placement(dataset, category, segments)` | 路径片段 → 模块 / 归属类型 |
| `document_locale(payload)` | **服务器实际给的是哪个语言**，没有就返回 `None` |

最后一个别省。`language` 是"我要哪一版"的指令，站点没有那个语言时多半
不报错、只默默回默认语言——不对一遍，就会得到一个标着德语的英文库。

### B3. 写配置

新建 `datasets/<id>.toml`，照 `epic-ue-5.8.toml` 的结构。必填：
`id` / `name` / `product` / `version` / `language` / `source`，
以及 `[categories]`（站点地图 URL 片段 → 分类）和 `[entity_types]`。

`knowledge` 可以留空——**没有领域知识包一样能抓能搜**，只是少了该领域特有的
线索（比如 Unreal 的 `K2_` 前缀、蓝图↔C++ 对应）。先跑通再考虑要不要加。

### B4. 小样验收（这一步不能跳）

```powershell
$env:DOCATLAS_DATASET='<id>'
python -m docatlas crawl --discovery-only
python -m docatlas validate --phase inventory
python -m docatlas crawl --skip-discovery --sample-per-category 20
python -m docatlas validate --phase content
```

然后**自己实际查几条**，看正文是不是干净的：

```powershell
python -m docatlas ask "<那个站里一定有的东西>"
```

要看的是：正文有没有 HTML 残留、标题层级对不对、原出处 URL 点开是不是那一页。
不对就回去改适配器，**别带着脏数据跑全量**。

### B5. 放开全量

小样干净了才 `crawl`。页数多就后台跑，跑完再 `validate --phase content`。

---

## 流程 C：改了加工规则，要重来

**触发**：改了切分逻辑、别名规则、关系推导。

1. 改了**切分**（`chunking.py`）：把 `docatlas/constants.py` 里的
   `CHUNKER_VERSION` 加一，然后：

   ```bash
   python -m docatlas reprocess
   ```

   它只做还没升级到当前版本的页，**断了再跑就是接着做**。
   跑完会自动重建交叉关系，不用再单独跑 `cross-index`。

2. 只改了**关系推导**（`crossindex.py` 或知识包里的 `build_relations`），
   切分没动：不用 `reprocess`，单跑这个就够：

   ```bash
   python -m docatlas cross-index
   ```

3. 两种情况都要验收：

   ```bash
   python -m docatlas validate --phase content
   ```

   **重点看 `relation_evidence_coverage`。** 它专门抓"某类关系被整类做没"——
   这种错不会报任何异常，所有其他检查都会通过。已经抓到过两次了。

---

## 流程 D：例行体检

```powershell
.\docatlas.ps1 status                          # 抓了多少、失败多少
python -m docatlas validate --phase content    # 12 项数据合同
```

`validate` 有任何一项 `fail`，把那一项的 `requirement` 原文念给用户听——
它写的就是哪里不对。

---

## 流程 E：项目搬了地方 / 改了名

技能文件里写着程序的绝对路径，搬完必须重装一次，否则 AI 会照旧路径去找：

```powershell
.\scripts\install-skill.ps1
```

---

## 常见岔路

| 现象 | 多半是 | 怎么办 |
|---|---|---|
| 清单枚举出 0 页 | 站点地图地址或分类片段不对 | 手动打开 `sitemap_index` 看结构，对 `[categories]` |
| 抓回来正文是空的 | `parse_document` 没认出正文结构 | 存一份原始返回下来看 |
| `validate` 报语言不符 | `language` 那个站不支持，被默默换了 | 改成站点真有的语言，或确认它只有一种 |
| 查什么都查不到 | 清单没冻结，或者用错了数据集 | `python -m docatlas paths` 看当前是哪个 |
| 某类关系突然归零 | 加工规则改动打断了别处的假设 | 看 `relation_evidence_coverage` 说缺哪一类 |
