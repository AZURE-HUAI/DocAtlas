---
id: BUG-004
title: "蓝图属性 Setter 难以按节点显示名检索和关联"
type: bug
status: resolved
lifecycle: resolved
priority: medium
area: unreal-knowledge
labels: [search, related, unreal, blueprint, aliases]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: https://github.com/AZURE-HUAI/DocAtlas/pull/2
related: [BUG-005, BUG-012, ENH-003]
---

# 问题

用户按蓝图界面中的 Setter 名称搜索时，难以发现对应的
`BlueprintReadWrite` C++ 属性，关系层也无法表达二者的生成关系。

## 复现

```powershell
python -m docatlas search "SetTargetArmLength" --limit 20
python -m docatlas search "TargetArmLength" --limit 20
python -m docatlas related "TargetArmLength"
python -m docatlas related "Set Target Arm Length"
```

## 实际结果

- `SetTargetArmLength` 没有搜索结果。
- `TargetArmLength` 只能从 C++ 教程中找到使用示例。
- 两条 `related` 查询分别约 286 毫秒和 283 毫秒返回 `[]`。
- 当前无法表达 `BlueprintReadWrite` 属性与自动生成的蓝图 Getter/Setter
  之间的关系。

Epic 官方 API 将 `USpringArmComponent::TargetArmLength` 标记为
`BlueprintReadWrite`，因此它可以在蓝图中以属性 Setter 的形式使用。

## 期望结果

- 用户使用蓝图界面名称、紧凑代码名或原始属性名时，都有合理路径发现同一属性。
- 没有独立节点页面时，至少返回对应属性的官方 API 定义，并说明蓝图节点来自
  可读写属性。
- 查询结果能够解释 C++ 属性与蓝图自动访问器之间的联系及其证据。

## 可能方向

一种参考方向是根据官方 `BlueprintReadWrite` 元数据补充检索别名或生成关系，
关系可以类似 `blueprint_property_accessor`。也可以选择不创建虚拟蓝图实体，
而是在属性结果中动态解释访问器。

应先确认官方文档实际提供了哪些元数据和独立页面，再决定使用实体、别名、关系
还是查询时展开；关系名称和具体实现均未确定。

## 调查记录

不确定官方是否为此类自动生成 Setter 提供独立页面。缺口可能位于实体模型、
自动别名或 Unreal 知识包，而不一定只是搜索排序。

## 验证

先核对官方到底有没有这些页面（在 199,883 页的冻结清单里查）：

```text
SELECT path,category,status FROM pages WHERE path LIKE '%SpringArm%';
/documentation/unreal-engine/BlueprintAPI/SpringArm                    blueprint_api  pending
/documentation/unreal-engine/BlueprintAPI/SpringArm/GetTargetRotation  blueprint_api  pending
/documentation/unreal-engine/API/Runtime/Engine/USpringArmComponent    cpp_api        pending
…
SELECT path FROM pages WHERE normalized_slug LIKE '%targetarmlength%';
（0 行）
```

**结论：Epic 不为 `BlueprintReadWrite` 属性、也不为它自动生成的 Getter/Setter
出独立页面。** `TargetArmLength` 只记在所属类页 `USpringArmComponent` 的成员表里。
因此"给 Setter 建一条指向属性页的关系"这条路在官方证据层面不成立。

抓回该类页后实测（真实库）：

```powershell
python -m docatlas get "USpringArmComponent" --limit 1
# 成功 1；新建关系 9
```

属性确实在里面，而且带着官方元数据：

```text
K179789 [details] USpringArmComponent > Variables > Public
  … - EditAnywhere - BlueprintReadWrite - Category=Camera
    TargetArmLength  float  Natural length of the spring arm when there are no collisions …
```

```powershell
python -m docatlas ask "TargetArmLength" --token-budget 3000 --no-fetch
# 1. Player-Controlled Cameras > 3. Write C++ Code…（教程里的用法示例）
# 2. Player-Controlled Cameras > Finished Code
# 3. USpringArmComponent > Variables > Public   ← 官方 API 定义，带 BlueprintReadWrite
```

排序调整前后（`search.search_chunks` 实测得分）：K179789 从 43.7 升到 51.7，
在 8 条候选里从落榜升到第 4，默认预算下进入返回结果的第 3 条。

名称扩展（`QualifierAndAliasTests`）：

```python
unreal.query_aliases("SetTargetArmLength")   # → ['TargetArmLength', 'Target Arm Length', …]
search.query_names("Set Target Arm Length")  # → [..., 'targetarmlength', ...]
```

回归测试：128 用例全过，其中
`test_member_listings_are_pushed_back_only_for_concept_questions` 钉死排序改动，
概念查询（`Nanite`、`how do I set up virtual shadow maps`）的首三位实测未受影响。

## 解决记录

**根因分三层，只有前两层是 DocAtlas 能修的。**

1. **查询名和官方名对不上**。用户敲的是蓝图界面里的 `SetTargetArmLength`，
   官方文字里只有属性名 `TargetArmLength`。核心不该认识 Get/Set 这种约定
   （不同产品的访问器命名并不一致），所以做成领域知识包的钩子：
   `knowledge/unreal.py::query_aliases()` 脱掉 `Set`/`Get` 前缀、补 `K2_` 变体；
   `search.query_names()` 负责按顺序去试，检索和按需抓取共用。核心另外补了一条
   与产品无关的规则：限定名只取末段（`std::from_chars` → `from_chars`），
   写在 `text.qualifier_tail()`。

2. **官方定义被排序压掉了**。属性写在类页的成员表里，而成员表是
   `details` 类型、又落在 `verbose_categories`（`cpp_api`），原来一律扣 8 分。
   那个惩罚是为了"问概念时别把大段罗列塞进来"，用在**问一个具体符号**上正好
   相反——答案就在那张表里。改成只在 `concept` 形状的查询上生效。
   实测：K179789 得分 43.7 → 51.7，默认预算下进入结果第 3 条。

3. **官方没有这一页**（不能修）。清单核对确认 Epic 既没有属性页也没有访问器页。
   没有页面就没有实体，没有实体就没有可带证据的关系——硬造一条
   "蓝图 Setter ↔ C++ 属性"的关系等于把推测冒充官方声明，与本项目
   "证据和置信度不许注水"的原则直接冲突。

**因此本议题按"达到官方证据允许的上限"处理**：

- 用界面名、紧凑代码名、属性本名都能走到同一份官方定义（议题期望的第 1 条）；
- 没有独立节点页面时，返回的正是属性的官方 API 定义，正文里带着
  `BlueprintReadWrite` 元数据（期望的第 2 条）；
- 剩下的缺口用**如实说明**覆盖：`describe_lookup()` 区分"清单里有、没抓"和
  "官方确实没有这一页"，AI 不会再拿空结果自由发挥（期望的第 3 条的一部分）。

**已知遗留（可接受，已实测）**：`ask "TargetArmLength"` 的首位仍是教程里的用法
示例，官方定义排第 3。原因是教程那一块被判为 `parameters`（+7.0）而成员表是
`details`（+0.5）。要把它顶到第一，需要引入"API 分类在标识符查询上的加权"这类
新配置，属于没有证据支撑的调参，本轮不做。官方定义已在默认预算内返回并带原出处，
满足议题的期望结果。

**若将来 Epic 开始为属性出独立页面**，ENH-003 讨论的领域关系扩展点可以直接接上，
届时才值得引入 `blueprint_property_accessor` 这类关系。

**后续（`BUG-012`，2026-07-26）**：上面第 3 条的推理没有错，但它的前提被换掉了。
"没有页面就没有实体"当时是核心的硬规定（一页只能有一个实体），不是官方证据的
限制。`BUG-012` 把成员表里的成员提升成了实体，于是 `related "TargetArmLength"`
现在能答出它属于 `USpringArmComponent`；显式声明了访问器的属性（如
`CrouchedHalfHeight`）也有了带证据的 Getter/Setter 关系。**自动生成**的访问器
仍然只给别名不给关系，理由与这里第 3 条完全相同。

## 外部关联

- GitHub Issue：
- 修复 PR：
