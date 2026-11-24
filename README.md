# LGS Paintboard 2027 客户端（Python）

对接 [LGS Paintboard 2027](https://pb.ika.zip) 的绘图 / 守图客户端。协议权威文档：
[`Ikaleio/ikapb@2027:docs/protocol.md`](https://github.com/Ikaleio/ikapb/blob/2027/docs/protocol.md)。

核心思路：**一次落笔 = 13 个像素的实时视野**（声呐半径 2 的菱形），所以「巡检」不应该
逐像素重涂，而应该用菱形密铺把整块地盘的落笔数压到 面积/13。守图 = 密铺巡检 + 差量回填。

## 策略

### 1. 绘制：分块蛇形，零跳跃

目标区域按 token 数切成连续的带状/块状切片，每片内部按行蛇形（boustrophedon）遍历，
相邻两笔曼哈顿距离恒为 1 → 冷却 `c0 + k·1` 最小。首遍 120×120 实测 **2400 px/min @ 64 token**。

### 2. 巡检：菱形密铺（相对逐像素重扫省 12.2×）

半径 `R` 的菱形面积是 `2R²+2R+1`（R=2 时 13），而格基

```
e1 = (R+1, R) = (3, 2)
e2 = (-R, R+1) = (-2, 3)
```

的行列式恰好也是 13，所以这组格可以**无重叠铺满**平面。120×120 = 14400 像素，
逐像素重扫要 14400 笔；密铺只要 1108 个中心 + 边缘补射 72 = **1180 笔**。

路线顺序也影响冷却：格点在「画布行」内间距是 13（差一个 `e1+2e2`），按 y 排序每笔都要
跨 13 格（2.3 s）；改成**沿格基 (a,b) 蛇形**，相邻两笔恒为 `e1` 或 `e2`（距离 5）。
再加上**往返蛇形**（一轮画到头就反着画回来，切片首尾重合），省掉每轮 64 次回程长跳。

### 3. 冷却：乐观调度 + COOLING 兜底

不等 `TICK` 回执再排下一笔：发送时就把「现在」当作服务器接受时间，本地立刻算出下一笔的
`ready = t + c0 + k·d`。客户端时钟偏移本身就含单程延迟，这样推算出的到达时刻正好落在
`ready` 上；万一算早了，服务器返回的 `COOLING` 自带精确剩余等待，按它回滚重排即可。

### 4. 守图：巡检 + 差量回填

- 巡检（上面的密铺）负责**发现**：每笔带回 13 像素实时颜色，与目标色比对，不一致的进
  `suspects`。
- 任何 token 冷却好了先看最近的 suspect：距离 ≤ 96 格直接跳过去补，否则继续巡检自己的片。
  可疑像素 > 512（还在首遍绘制）时自动退回纯绘制，避免每笔做全表最近邻。
- 观察连接单独收公共画面（晚 `public_delay`），公共画面里被改过的像素也会进 suspects；
  但实时性靠声呐，不靠它。

## 实测（2026-11-24，测试服，64 token）

| 阶段 | 一轮巡检 1180 笔 | 说明 |
| --- | --- | --- |
| 逐像素重扫 14400 笔 | ≈ 6 min | 最初的版本，纯靠重涂覆盖 |
| 密铺（画布行排序） | 50.4 s | 平均跳跃 11.9 格 |
| 密铺（格基蛇形） | 43.1 s | 平均跳跃 7.3 格 |
| + 乐观调度 | 41.3 s | 少等一个 TICK 往返 |
| + 往返蛇形 | **36.5 s** | 去掉每轮回程长跳 |

其他数据：

| 指标 | 值 |
| --- | --- |
| 首遍绘制（120×120 = 14400 px） | 6 min 06 s，2400 px/min |
| 单 token 产率 | 37.5 px/min（理论 54.5，受 RTT 与 tick 影响） |
| 巡检等效验证速率 | 14400 px / 36.5 s ≈ **24k px/min** |
| CPU / 内存 | 64 token 两连接 ≈ 2–3% 单核 / ~70 MB |
| 破坏测试（10×10 涂黑） | 同一轮巡检内发现（≤36.5 s），发现后 ~9 s 补完；旧版逐像素重扫是 87 s 上界 |

## 与参考实现的差异

参考：[`HDS-Sgt/LGS-Paintboard-2027-C-client`](https://github.com/HDS-Sgt/LGS-Paintboard-2027-C-client)（C++20）

| 维度 | 参考实现 | 本实现 |
| --- | --- | --- |
| 语言 / 并发 | C++20 + 多线程 + POSIX socket | Python 3.14 + asyncio + numpy |
| 巡检 | 菱形密铺（同样的格基思路） | 同 |
| token 分组 | paint / repair / sonar 三组，按角色调度 | 单池，按 suspects 动态插队 |
| 绘制 | bbox 分块，一块一个 token | 蛇形路线切片，一片一个 token |
| 冷却 | 维护 brush 状态 | 乐观调度 + COOLING 回滚 |
| 观察流 | 可选 `--observe` | 常驻独立连接 |

结论：**策略层面两者一致**（密铺是这类迷雾规则下的最优巡检），差别在工程：C++ 版单笔
延迟更低、能开更多槽位；Python 版胜在改策略快、解码向量化后 CPU 只有个位数百分比。

## 安装

```powershell
python -m pip install numpy websockets pillow   # Python 3.14 自带 compression.zstd
```

## 用法

```powershell
# 服务器参数
python -m pb meta

# 只读探测：公共画面 + 活跃度热力图 + 候选落点
python -m pb probe --seconds 45 --size 120 --out out

# 首遍绘制 + 自动切换巡检守图（--place 留空则先探测选址）
python -m pb draw --image assets/target.webp --size 120 --place 440,1 --tokens 64

# 已经画好，直接开始守图
python -m pb draw --size 120 --place 440,1 --tokens 64 --guard-only
```

测试服（`issuance.access_mode = "none"`）可以直接用任意 uid 换 token：
脚本首次运行会自动 `POST /api/paint-token {"uid": N}` 领一批并存进 `out/tokens.json`。
正式服要用 access_key 换（`--server` + 自己的 token 文件）。

## 结构

```
pb/codec.py     BATCH 编码 + RULES/SNAPSHOT/PUBLIC/TICK 解码（列式、小端、4 字节对齐）
pb/world.py     board + l1/l2 历史层 + 声呐表(颜色,tick) + 画笔表
pb/tiling.py    菱形密铺：格基 (R+1,R)/(-R,R+1)，覆盖校验 + 边缘补射
pb/place.py     热力图窗口搜索（活跃度 + 边缘密度 + 居中）
pb/runner.py    槽位绑定 / 分块蛇形 / 密铺巡检 / 乐观冷却 / 差量回填
pb/conn.py      zstd 帧收发 + 断线重连（since 续传）
tools/          board(HTTP 快照) / preview / fill / attack / activity
selftest.py     编解码、历史层、声呐几何、密铺覆盖率
```

## 测试

```powershell
python selftest.py
```

覆盖：BATCH 字节布局、RULES 解析、PUBLIC 的 0/1/2 颜色码与 l1/l2 历史层语义、声呐菱形
顺序与越界、密铺对 120×120 的 100% 覆盖。

## 合规

- 不进行任何 RMB 交易；不绘制违规内容。
- token / access_key 是凭据，不要提交进仓库（`out/` 已在 `.gitignore`）。
- 测试服与正式服数据不互通。

## License

MIT
