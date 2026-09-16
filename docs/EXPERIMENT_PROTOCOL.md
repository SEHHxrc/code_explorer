# 依赖图增强 A/B 实验协议

## 目标

比较同一个模型在“获得依赖图增强上下文”和“不获得依赖图材料”两种条件下回答仓库问题的质量。这是原型评估基础设施，不是两个长期产品模式。

## 受控变量

两条通道接收相同的用户问题、Provider/模型、最大 Agent 步数、项目快照、中性 Manifest 事实，以及不按图中心性排序而重新生成的仓库地图。执行顺序和前端左右展示顺序分别独立随机化。

有图通道额外获得有界依赖图上下文和依赖邻居工具。临时对照通道既不获得图数据，也不获得图工具。估算 Token 指标基于字符数换算，必须标记为估算值，不能当作 Provider 账单 Token。

在两条通道都完成并提交盲评之前，用户只能看到左右答案和指标。盲评记录正确性、完整性、证据质量、幻觉控制、偏好和可选备注；之后 API 才揭示组别映射。

## 临时对照组删除清单

如果有图通道获胜，先用以下命令定位所有边界标记：

```powershell
rg -n "TEMPORARY CONTROL GROUP|临时对照组" backend frontend test docs
```

然后：

1. 删除 `backend/app/experiments/baseline/`。
2. 删除 `backend/app/experiments/service.py` 中的 baseline 策略分支和对照运行创建逻辑。
3. 如果不再保留历史实验记录，通过显式数据库迁移移除 `baseline_run_id` 和盲态比较持久化字段。
4. 如果实验工作整体结束，而不是更换另一种对照，则删除比较 API/路由、比较界面和 `frontend/src/services/experimentApi.js`。
5. 删除 `test/test_experiment_isolation.py`，或只保留针对有图路径的断言。
6. 保留普通 `/api/agent` 路径和依赖图增强的正式实现；该路径从不导入临时对照包。

如需历史分析，应在删除表之前导出实验结果。从 Git 删除的源文件仍可通过仓库历史恢复。
