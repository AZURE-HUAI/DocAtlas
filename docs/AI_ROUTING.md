# AI 检索规则

本地知识库用于回答技术文档问题。所有事实优先从本地库取回，
并把结果里的 `source_url` 作为原出处引用给用户。

当前数据集是 Unreal Engine 5.8 官方文档，下面的分类名以它为例；
换数据集时分类名会变，但规则本身通用。

Skill 原本在项目的 `skills/docatlas/`（装到 Claude Code 和 Codex），
下面是它遵循的完整规则。**客户端接了 MCP 服务器时，同名的 `docatlas_*` 工具
优先于命令行**——两者调用的是同一套检索代码，结果一致。

---

## 一、默认路径：`ask`

```powershell
python -m docatlas ask "Set Timer by Function Name" --token-budget 3000
```

`ask` 一步到位地返回**已裁剪、已去重、已排序**的 Markdown：命中的知识块正文、
每块的原出处、以及一跳交叉关系的指针。绝大多数问题到这里就结束了。

**本地没有的页面 `ask` 会自动补抓**（全站清单早已冻结，所以知道去哪取），
通常一两秒。所以不要因为"可能还没抓到"就绕开它，也不要自己去联网找官方文档。

- `--no-fetch`：禁止联网，只用本地已有内容
- `--fetch-limit N`：补抓上限，默认 5 页
- `get "<名称>"`：显式把某一页（或某几页）抓到本地，适合要连着查一个类的成员

只有在 `ask` 不够时才继续：

| 情况 | 用什么 |
|---|---|
| 不确定该看哪条，想先扫目录 | `search "<关键词>" --limit 10` |
| 要展开某一条完整正文 | `show K<编号>` |
| 要看蓝图/C++/类型对应关系的全部证据 | `related "<名称>"` |
| 要程序可解析的结构 | `ask ... --json` 或 `context ...` |

对应的 MCP 工具依次是 `docatlas_ask` / `docatlas_search` / `docatlas_show` /
`docatlas_related` / `docatlas_list_datasets`；参数名去掉 `--` 并把连字符换成
下划线（`--token-budget` → `token_budget`）。

---

## 二、上下文预算

`--token-budget` 是**硬上限**，不是建议值。选值参考：

| 预算 | 适用 |
|---|---|
| 1500 | 单个节点/函数的参数或返回值 |
| 3000 | 默认。一个功能点的完整说明 |
| 6000 | 需要通读一个体系（GAS、Niagara、Chaos） |

预算内的取舍规则（`context.py` 实现）：

1. 累计超预算就停，不存在"最后一条超一点没关系"
2. 同一页面最多贡献 2 个知识块
3. 内容哈希相同的块只保留一份
4. 一跳关系**只给指针**，不展开正文
5. 查询精确命中实体时，只从该实体自己的页面取正文——
   **不会把同一个 C++ 类里的兄弟函数整批塞进来**
6. 庞大的成员罗列（数据集里标为 `verbose_categories` 的分类）会被压到最后

---

## 三、绝对不要做的事

这些做法会瞬间吃光上下文：

- ❌ 直接读 `exports/` 里的 Markdown 分片（单片 8 MB）
- ❌ 用 Read 打开 `knowledge.sqlite3` / `manifest.jsonl` / `site_inventory.jsonl`
- ❌ 用 grep 扫整个知识库目录
- ❌ 不带 `--token-budget` 就大幅提高 `--limit`

---

## 四、检索顺序建议

按问题类型选 `--category`，结果会明显更干净：

1. 概念、工作流、配置步骤 → `guides`、`community_docs`
2. 蓝图节点的输入/输出/行为 → `blueprint_api`
3. C++ 类、函数、模块、声明 → `cpp_api`
4. 编辑器图表 / Rig / Dataflow 节点 → `node_reference`
5. Python 自动化 → `python_api`

不确定时不加 `--category`，让五档回退自己找。

---

## 五、关系的可信度

`related` 和 `ask` 给出的每条关系都带 `evidence_kind` 与 `confidence`：

| 证据 | 置信度 | 转述时怎么说 |
|---|---|---|
| `official_link` | 1.0 | "官方文档中直接链接到" |
| `unreal_display_name_metadata` | 1.0 | "C++ 侧 DisplayName 元数据与该蓝图节点完全一致" |
| `document_statement` | 0.92 | "文档正文写明 Target is X" |
| `exact_normalized_name` | 0.82~0.9 | **"候选对应，需核对签名"**——不能说成官方等价 |

蓝图与 C++ 的映射优先采用 `unreal_display_name_metadata`；
正文里的 `Target is ...` 单独记为 `targets_type`，不会和具体函数映射混为一谈。

上下文包只纳入置信度 ≥ 0.8 的关系。

`related` 的返回带 `status`，**不要把三种情况当成同一件事**：

| `status` | 含义 | 下一步 |
|---|---|---|
| `ok` | 找到实体也有关系 | 直接读 |
| `entity_found_but_no_relations` | 实体在库里，一条关系都没有 | 多半是它指向的页面还没抓 |
| `entity_not_found` | 没有这个实体 | 看 `lookup.pending_pages` 决定补抓还是如实说没有 |

---

## 六、回答规则

- 明确限定当前数据集的版本（`docatlas paths` 能看到是哪个），
  不要把其他版本的行为混进来。
- 每个关键结论旁边附对应的 DOC 原出处 URL，不要自己拼 URL。
- **查不到就说查不到，但先看清是哪一种"没有"。** 三种情况的下一步完全不同：
  清单里有这一页只是没抓（照提示 `get`）、页面已在本地只是词没对上（换写法）、
  清单里也没有（官方确实没有）。工具输出会明说是哪一种。
  用户用别的语言提问时，先把说法落成原文里的官方写法再查。
  确认真的没有之后如实说，不要改用记忆作答，也不要假装库里有。
- 教程文档与 API 表面冲突时，先核对更新时间与具体类型，再解释适用范围。
- 蓝图 API 官方自身也可能不完整，回答时保留这个限制。

---

## 七、数据位置

全都在数据目录下（`docatlas paths` 会告诉你具体在哪）：

| 用途 | 文件 |
|---|---|
| 覆盖率总路由 | `ROUTER.md` |
| 全部数据 | `knowledge.sqlite3` |
| 抓取质量报告 | `report.json` |
| 冻结的全站清单 | `site_inventory.jsonl`（+ `.sha256`） |
| 逐页清单（需手动生成） | `manifest.jsonl` |
| 整本 Markdown | `exports/`（**AI 不要整篇读**） |
