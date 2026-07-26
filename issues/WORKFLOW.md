# Issue 维护流程

## 1. 新建

从 `templates/` 复制问题或增强模板，分配下一个编号，放入 `unresolved`，并更新
`issues/README.md`。一个文件只处理一个可独立关闭的事项。

## 2. 调查与开发

- 只记录已验证事实；建议方案仅供参考。
- 状态按进度使用 `open`、`investigating`、`in_progress`、`blocked` 或
  `discussion`。
- 需要在线协作时再建立 GitHub Issue/PR，并在档案中记录链接。

默认上下文只读索引和当前相关的少量 `unresolved` 文件，不批量读取历史档案。

## 3. 完成

1. 写明实际验证和解决结果。
2. 将 `status`、`lifecycle`、`resolved_at` 更新为完成状态。
3. 移入对应的 `resolved` 目录并更新索引。

`resolved` 表示已完成；`closed` 只用于重复、不处理或不再适用。

## 4. 重新打开

问题复现时移回 `unresolved`，保留原解决记录，并补充新的日期与证据。

## 5. 校验

```powershell
.\scripts\validate-issues.ps1
```

该命令检查编号、元数据、目录、状态、关联、索引、链接和解决记录。
