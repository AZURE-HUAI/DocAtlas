# 参与 DocAtlas

感谢你帮助改进 DocAtlas。仓库使用 GitHub Issues 进行在线讨论和分工，同时使用
[`issues/`](issues/README.md) 保存可长期检索的复现证据、设计背景和解决记录。

## 报告问题或提出增强

优先从 GitHub 的 **New issue** 页面选择“报告问题”或“提议增强”。表单会提示需要的
最小信息。

如果问题来自本地高强度测试，也可以直接从
[`issues/templates/`](issues/templates/) 复制模板，在相应的 `unresolved/`
目录创建档案。需要多人讨论或开发排期时，再创建 GitHub Issue 并互相链接。

提交前请：

- 搜索已有 Issue 和本地档案，避免重复；
- 提供最小复现、原始结果、环境和可验证的期望结果；
- 把事实、推测和参考方向分开；
- 删除密钥、Token、账号信息、私人数据和不必要的大型日志。

## 修改代码

1. 从 `main` 创建一个内容单一的分支。
2. 修改代码和对应测试。
3. 运行相关测试；完整离线测试命令是：

   ```powershell
   python -m unittest discover -s tests -v
   ```

4. 如果改动涉及问题档案，再运行：

   ```powershell
   .\scripts\validate-issues.ps1
   ```

5. 创建 Pull Request，填写改动、关联事项和实际验证结果。

有对应 GitHub Issue 时，在 PR 描述中写 `Closes #编号`。PR 合并进默认分支后，
GitHub 会关闭对应 Issue。

## 完成与封存

解决问题的 PR 应同时更新相关本地档案：

- 写明最终采用的解决方式；
- 记录测试命令和实测结果；
- 填写解决日期和 PR；
- 将档案从 `unresolved/` 移入相同类型的 `resolved/`；
- 更新 [`issues/README.md`](issues/README.md) 索引。

完整的状态映射、重新打开和上下文保护规则见
[`issues/WORKFLOW.md`](issues/WORKFLOW.md)。
