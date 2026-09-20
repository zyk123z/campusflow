---
name: staff_handoff_summary
description: CampusFlow创建校园服务工单时使用，将事件报告转换成值班人员可快速接手的结构化摘要。
---

## Workflow

使用下列模板生成工单交接摘要，不添加报告中没有的事实。

```text
应用 skill: staff_handoff_summary
报告ID：{{report_id}}
学生：{{student}}
事件分类：{{category}}
紧急程度：{{urgency}}
负责部门：{{department}}
风险等级：{{risk_level}}
置信度：{{confidence}}
摘要：{{summary}}
学生原始描述：{{content_excerpt}}
建议后续动作：
{{next_steps}}
```
