---
id: BUG-012
title: "类页面成员表里的属性和方法没有成为实体"
type: bug
status: resolved
lifecycle: resolved
priority: high
area: relations
labels: [entities, relations, unreal, blueprint, contract]
reported_at: 2026-07-26
resolved_at: 2026-07-26
github_issue: null
fix_pr: null
related: [BUG-004, BUG-005, ENH-003]
---

# 问题

`USpringArmComponent::TargetArmLength` 在库里搜得到正文、看得见官方的
`BlueprintReadWrite` 元数据，`related "TargetArmLength"` 却返回
`entity_not_found`。

根因不在 Unreal 那一层：**"一页文档 = 一个实体"是写死在核心里的**。
`store.py` 每页删一次实体再插一条，类页面成员表里那几百个属性和方法
从来没有机会成为实体。没有实体就没有关系，于是"这个属性属于哪个类"
这种官方白纸黑字写着的事实，在库里根本不存在。

## 环境

- 数据集：`epic-ue-5.8`（199,883 页 / 10,766 已抓）
- 入口：CLI 与 MCP 都一样

## 复现

```powershell
$env:DOCATLAS_DATASET='epic-ue-5.8'
python -m docatlas related "TargetArmLength"
python -m docatlas related "Set Target Arm Length"
python -m docatlas related "CrouchedHalfHeight"
```

## 实际结果

三条全是 `entity_not_found`。`Set Target Arm Length` 还会给出一条误导性的
弱候选（`/API/Plugins/HarmonixDsp/FModulator/SetTarget`），因为名字里有 "Set"
和 "Target"。

## 期望结果

- 用蓝图里看到的节点名、C++ 属性名、限定名，都能落到同一个东西上。
- 能说出这个属性属于哪个类，并给出出处。
- 显式声明了访问器的属性，能连到那个访问器，带证据和置信度。
- 不同类的同名属性不能串成一个。

## 可能方向

（原议题记录的参考方向，最终采用见"解决记录"。）

- 把属性识别为独立实体，身份包含所属类型。
- 由 UE 知识包识别 `BlueprintReadWrite` / `BlueprintGetter` 等元数据。
- 通用核心只负责规范化、去重、方向、证据和置信度。

## 调查记录

先在真实库里核对了官方到底给了什么（这决定了能做到哪一步）：

```text
SELECT COUNT(*) FROM entities WHERE normalized_name LIKE '%armlength%';   → 0
SELECT id FROM pages WHERE path LIKE '%/GetCrouchedHalfHeight';           → 0 行
```

已抓的 9 个 C++ 类页里，成员表共 1,206 行，其中 **597 行的 Name 一栏带链接**
（官方给这个成员出了独立页面），609 行不带。属性名 269 个，其中
**30 个在两个类里重名**（`ClientLoc`、`bHasBase` 等同时属于 `ACharacter`
和 `UCharacterMovementComponent`）。

显式访问器声明确实存在，且**被声明的函数就在同一张表里**：

```text
| CrouchedHalfHeight | float | … | - BlueprintReadWrite
                                    - BlueprintSetter=SetCrouchedHalfHeight
                                    - BlueprintGetter=GetCrouchedHalfHeight |
| void SetCrouchedHalfHeight ( const float NewValue ) | … | - BlueprintSetter |
| float GetCrouchedHalfHeight() | … | - BlueprintGetter |
```

## 验证

### 固定回归查询（改动前先记录）

| 查询 | 改前 | 改后 |
|---|---|---|
| `related "TargetArmLength"` | `entity_not_found` | `ok`，`belongs_to → USpringArmComponent` |
| `related "Set Target Arm Length"` | `entity_not_found` + 误导候选 | `ok`，落到同一个属性实体 |
| `related "CrouchedHalfHeight"` | `entity_not_found` | `ok`，`belongs_to` + `blueprint_getter` + `blueprint_setter` |

### 正反向控制组（真实库实测）

| 控制组 | 期望 | 实测 |
|---|---|---|
| `TargetArmLength`（ReadWrite） | Get 和 Set 名字都有 | 两个都有 |
| `JumpCurrentCount` / `bIsCrouched`（ReadOnly） | 只有 Get | 只有 `Get …` |
| `bIsCameraFixed`（无 Blueprint 说明符） | 一个都没有 | 一个都没有 |
| 全库比例 | ReadWrite 数 = Setter 数 | 113 = 113 |
| 全库比例 | ReadWrite+ReadOnly = Getter 数 | 113+26 = 139 |
| `ClientLoc`（两个类都有） | 两个独立实体，互不相连 | `ACharacter::ClientLoc` / `UCharacterMovementComponent::ClientLoc`，两者间关系数 **0** |
| 关系方向 | 属性 → 访问器 | `CrouchedHalfHeight → SetCrouchedHalfHeight`，`outgoing` |

### 关系总量前后对照（`epic-ue-5.8`）

| 项目 | 改前 | 改后 |
|---|---|---|
| 实体 | 10,766 | 11,282（+516 成员） |
| `official_link` 关系 | 17,773 | **17,773**（未变，见下） |
| `belongs_to` / `page_member_table` | 0 | 516 |
| `blueprint_getter` / `blueprint_setter` | 0 | 各 1 |
| `targets_type` / `node_api_candidate` / `blueprint_cpp_api` | 40 / 16 / 1 | **40 / 16 / 1**（无退化） |
| 关系合计 | 17,830 | 18,348 |

`official_link` 一条没多是关键指标：不挡的话，一页 60 个成员 × 20 条链接
会变成 1,200 条重复关系。

### 其它数据集

`cppreference-2026-07-26` 与 `blender-manual-5.2` 的适配器不实现
`page_members`，`members.supported()` 为 False，成员实体 0，页/块/关系/实体
四项计数与改动前逐条相同，一条 SQL 都不会多发。

### 回归与验收

- 单元测试 198 → **213 全过**（新增 15 条）。
- `validate --phase content`：三个数据集各 14 项全 pass。
- MCP 一个连接连续切三个库查同一个名字：UE 命中，cppreference 与 Blender
  都是 `entity_not_found`，没有串库。
- 成员回填对 UE 库耗时 **0.4 秒**，不联网。

## 解决记录

**改的是"一页能有几个实体"，不是给 Unreal 加特例。**

`BUG-004` 当时的结论——"官方没有独立页面，所以没有实体，所以不能有关系"——
在它成立的前提下是对的。那个前提是**核心规定一页只能有一个实体**。前提换掉，
结论就不再适用；官方证据一直都在，只是核心没有地方放它。

### 分四层，每层只干一件事

| 层 | 职责 | 改了什么 |
|---|---|---|
| 来源适配器 | 认出**本站**的成员表长什么样 | `epic_ue.py` 加 `page_members()` |
| 通用核心 | 定身份、去重、存储、建 `belongs_to` | 新增 `docatlas/members.py`；`entities.member_of_id` 一列 |
| 领域知识包 | 成员在领域里还叫什么、凭什么说两个成员有关 | `unreal.py` 加 `member_aliases()` 与 `_property_accessors()` |
| 入口 | 不变 | MCP 工具形状、查询核心、`related` 合同一行未动 |

### 三条不变量

* **成员实体永远和它的所有者同页。** 于是重新加工一页时，
  `DELETE FROM entities WHERE page_id=?` 会连成员一起删干净，
  不需要任何额外的清理代码，也不可能留下孤儿。
* **身份带所有者。** `qualified_name` 是 `USpringArmComponent::ClientLoc`。
  30 个跨类重名的属性因此天然分开，不靠任何"同名要小心"的特判。
* **有自己页面的成员不在这里出现。** 成员表里 Name 一栏带链接的那 597 行
  说明官方给它出了页面，那一页本身就是实体。跳过它们既避免了同一个东西
  存两份，也正好把范围收敛到真正缺的那一批。

### 自动生成的访问器只给名字，不给关系

`TargetArmLength` 是 `BlueprintReadWrite`，蓝图里确实有 `Get Target Arm Length`
和 `Set Target Arm Length` 两个节点——但**官方不给这两个节点出页面**。
没有页面就没有实体，硬造一条指向不存在东西的关系，等于把推测写成官方声明。

所以这里分成两种处理，依据是官方证据的强度：

    显式 BlueprintSetter=SetCrouchedHalfHeight
      → 目标是同一张表里真实存在的函数 → 关系，置信度 1.0，带原文证据

    隐式 BlueprintReadWrite
      → 目标不存在文档实体 → 只给别名，让用户按节点名也能找到这个属性

`ACCESSOR_SPECIFIER_RE` 认出的关系还必须落在**同一个类页**上，否则
`ACharacter` 和 `UCharacterMovementComponent` 的同名属性会互相连到对方的
访问器上去。

### 官方链接必须挡在成员之外

`_official_links` 加了 `member_of_id IS NULL` 两条件。链接是**这一页**指向
另一页，不是页面上每个成员各指一次。实测 `official_link` 关系数在加了 516
个成员实体之后**一条没多**。

### 数据兼容

`entities.member_of_id` 是可空新列，三个已有库开库时自动加，老实体留 NULL
正好是它们的真实身份，不需要回填。成员本身由 `members.backfill()` 从
**已存的小节正文**重算，**不联网、不重抓**；`metadata.page_members` 记规则
版本，规则一变整批重来。适配器不实现 `page_members` 的数据集整个跳过。

### 没有采用参考建议里的一条

议题写的是"由 UE 领域规则生成 Getter/Setter 关系候选"。隐式访问器那一半
没有按这个做，理由见上：目标实体不存在。改成别名之后，用户从
`Set Target Arm Length` 出发同样能到达属性和它的所属类，而不必接受一条
指向虚构实体的关系。

## 外部关联

- GitHub Issue：
- 修复 PR：
