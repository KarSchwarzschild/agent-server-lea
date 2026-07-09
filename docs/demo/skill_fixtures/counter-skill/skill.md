---
name: demo-counter
description: "用 scripts/counter.py 里的 count_chars 函数做字符统计。Triggers: 字符统计, counter"
license: MIT
metadata:
  display_name: 演示 counter 脚本
  category: demo
  version: 1.0.0
  model_tier: mid
  required_tools:
    - sandbox_exec
---
你有一个专属 script 挂在沙箱里：`scripts/counter.py`，其中定义了函数 `count_chars(text: str) -> dict`。

当用户让你数字符时，**必须**走 sandbox_exec 调用 counter，绝不心算：

```python
import sys
sys.path.insert(0, 'scripts')
from counter import count_chars
result = count_chars('hello world')
print(f"COUNTER_OUTPUT={result}")
```

拿到 stdout 里的 `COUNTER_OUTPUT=...` 之后，用自然语言告诉用户 length / alpha / digits 三个数。
