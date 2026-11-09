# 起势资产 A股擂台 v3.0

五个工作室都只交易董事长提供的股票池。每日计划、池子维护、评分记录、每周进化都由程序完成，人负责审批。

## 目录
```
RULES.md               共用规则（唯一来源）
EVOLUTION.md           自动进化协议
AUDIT-20261109.md      本次审计：四周成绩、亏损原因、回测结论、旧文件处置
studios/<A-E>/
  STUDIO.md            本组定位、硬门、两名研究员评分、开仓与退出
  genome.json          生产参数、上一版参数（用于回滚）、错题本、在测挑战者
  ledger.md / .json    现金、持仓、待执行、净值
  evolution_log.md     进化记录
pools/<A-E>.csv        当前股票池（≤50只）；removed.csv 记录删除明细；original_20261109/ 为整理前的全量池
data/real_trades.csv   四周真实成交（错题本的来源）
engine/                data / features / studios / sim / shadow / daily / evolve
reports/               每日计划、全池评分、影子测试、进化周报
site/index.html        看板（engine/site.py 生成，单文件，可直接用浏览器打开）
deploy/                服务器部署：install.sh、定时任务、nginx 配置，见 deploy/DEPLOY.md
```

## 运行（只需 Python 3 标准库）
```
cd engine
python3 run_day.py   # 每个交易日 15:30 后：拉行情 → 结算当日成交（开盘卖、14:30 分时买）→ 删池 → 退出检查 → 评分 → 次日计划
python3 evolve.py    # 每周五 run_day 之后：影子测试 → 自动晋级/回滚 → 错题本
python3 site.py      # 重建看板 site/index.html
QISHI_TOKEN=口令 python3 server.py   # 看板服务 + 录入接口（127.0.0.1:8080）
```

## 新股票池怎么加
在 `pools/<组>.csv` 末尾追加一行：`代码,名称,批次号,入池日(YYYYMMDD)`。下次运行 daily.py 时，程序会自动拉取行情并纳入评分。

## 本资料仅供研究，不构成投资建议。
