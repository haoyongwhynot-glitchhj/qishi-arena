# 其他 Kiro Web 会话的导出方法

每个 Kiro Web 会话有独立的沙箱，这个会话看不到其他会话的文件和对话。请逐个打开下面的会话，把框里的提示词原样发给它，它会把自己的资料整理后推到本仓库的 `sessions/<名称>/` 目录（推送需要一个仓库令牌，用完即删）。

| 会话 | 导出目录建议 |
|---|---|
| 底部横盘长上影反包选股策略 | `sessions/bottom-reversal/` |
| 本地使用订阅套餐和模型配置 | `sessions/local-model-config/` |
| fad27e73 | `sessions/fad27e73/` |
| 美国期权BS逻辑改进报告 | `sessions/us-options-bs/` |

## 发给每个会话的提示词

```
请把本会话的全部资料交接到 GitHub 仓库 haoyongwhynot-glitchhj/qishi-arena 的 sessions/<目录名>/ 下，不要改动仓库其他目录：
1. HANDOFF.md：会话目标、时间线、做过的决定及原因、最终结论、未完成事项、已知局限、我的偏好。
2. 工作区里所有有用的代码、数据、报告原样复制进来（大于 50MB 的数据只写获取方法）。
3. 如果有长期规则，写成 sessions/<目录名>/steering.md。
4. 不要写入任何密码、令牌、API 密钥。
推送用我给你的令牌，推完告诉我提交链接。
```

全部导出后，Kiro Crew 在本机 `git pull` 即可拿到所有会话的资料。
