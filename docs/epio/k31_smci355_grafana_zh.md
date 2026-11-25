# smci355 上 EP × KV 实验的监控面板（Grafana）

所有实验的核心数据都推进同一个数据库，在一个 Grafana 面板里看：窗口时间线、decode 每步时长、EP 内核耗时、KV 每次传输的 p50 / p99、真实 PD 的 TTFT，以及每张网卡 2 ms 一个点的收发。跑完自动算一张“每个窗口一行”的汇总表，不同实验之间也能放在一张表里对比。

## 1. 怎么打开

- 在 Cursor 里打开 http://localhost:23300/d/epio （Cursor 会自动把登录节点的 23300 端口转发到本机）。用别的 SSH 客户端时，自己转发：`ssh -L 23300:127.0.0.1:23300 <登录节点>`。
- 不能直接用 IP 打开：公司网络到登录节点只放行 80 端口，80 端口已经被 ditian12 的 netmon Grafana 占用（集群日常监控，每 100 ms 采一次）。
- 面板上方选实验（`yutongwu-<run>`，例如 `yutongwu-e1x_7115`）。decode 节点、KV 来源会按这个实验自动填好。

## 2. 数据从哪来

| 类别 | 指标 | 谁推 | 粒度 |
|---|---|---|---|
| 网卡 | 每张卡的 RDMA 收发字节、CNP、ECN、重传、ACK 超时；PFC pause（ethtool） | ainic-netmon（mori PR #1000），每台节点上 `--push` | 2 ms（ethtool 1 s） |
| decode | 每个 DP rank 的步时 p50 / p90 / p99、每秒序列步数、在跑的序列 | `e2e/loadgen.py --push`（decode-only），`e2e/pd_proxy.py --push`（PD） | 1 s |
| 真实 PD | 每个请求的 TTFT、prefill 耗时、decode 等 KV（TTFT 减 prefill）；每秒 prefill / decode 请求数和 KV 块数 | `e2e/pd_proxy.py --push` | 每个请求 / 1 s |
| EP | MoRI dispatch / combine 每次调用的耗时（均值、p50、p90、p99，每个 rank 的均值） | `tools/ep_push.py`，每次 profiler 采样后解析 trace | 每个窗口一次 |
| KV（注入） | 每次传输的耗时、到达到完成（含排队）、MB | `mb/io_inject.py --push` | 每次传输 |
| 窗口 | 窗口开始 / 结束、IO 开始 / 结束、所有事件 | `e2e/windows.sh` 的 `ev` | 事件 |
| 汇总 | 每个窗口：每步、相对前面空档的变化、dispatch / combine 及相对 base、CNP/s、每卡接收、KV p50 / p99、重传、TTFT 和 decode 等 KV 的 p50 / p99 | `tools/epio_summary.py`，跑完后 | 每个窗口一行 |

- 数据库：我们自己的 VictoriaMetrics，登录节点上 `10.235.192.132:28528`，数据在登录节点本地盘 `/var/tmp/yutongwu_epio/vmdata`，保留 1 年。`tools/monitor_up.sh` 启动它和 Grafana，并核对端口上的进程确实是自己的（登录节点上 8428、18428、38428 是别人的库）。
- 推送都在后台线程里，库慢或连不上时只丢点，不影响实验。ainic-netmon 的推送丢了点时，跑完用记录文件重导一次（启动脚本自动做）。
- 节点推送要能连上登录节点的 28528：`e2e/e1.sh` 开跑前从 C 节点检查一次，连不上会在日志里写出来。

## 3. 面板

从上到下：

1. **窗口与 decode 性能**
   - 窗口时间线：哪个窗口在跑（绿），它的 IO 在跑（橙）。下面所有面板的时间轴都和它对齐。
   - decode 每步时长：每个 DP rank 每秒的 p50 / p99，取 30 秒中位数。profiler 采样的那几秒单步会到约 2.5 s，取中位数是为了不让这些尖峰把纵轴撑开。
   - decode 吞吐：每秒的序列步数和在跑的序列。
   - EP：每次 profiler 采样的 dispatch / combine（每个窗口一个点），以及每个 rank 的 dispatch 均值（看有没有个别 rank 慢）。
2. **KV / IO（注入的 KV）**：每次传输的耗时和“到达到完成”的 p50 / p99（30 秒滑动窗口），KV 吞吐和每秒传输次数。
3. **真实 PD**：TTFT、prefill、decode 等 KV 的 p50 / p99；每秒的 prefill / decode 请求和 KV 块。decode-only 的实验这一行为空。
4. **网卡（ainic-netmon）**：decode 每张卡的接收 / 发送（每个时间点内的平均）、接收在每个时间点内最高的 2 ms 速率（看 KV 尖峰，和 Moonshot 的截图同一种看法）、KV 来源每张卡的发送、decode 收到的 CNP / ECN、重传和 ACK 超时、PFC。缩放到几秒以内能看到每个 2 ms 读数。
5. **汇总**：当前实验每个窗口一行；跨实验对比（上方“对比”里多选实验，每行“实验 / 窗口”）。

## 4. 一次实验怎么接进来

`tools/launch2.sh` 的 `e1x` / `e1g` / `e1w` / `e1i` / `pdk*` 阶段已经默认打开全部推送。自己写的阶段在环境里设：

```bash
export NETMON=1 NETMON_PUSH=http://10.235.192.132:28528 NETMON_EXP_PREFIX=yutongwu-   # ainic-netmon，PD 里可用 NETMON=nat 只录自然流量那段
export EPIO_PUSH=http://10.235.192.132:28528                                       # decode / EP / KV / 窗口
```

跑完：

```bash
python3 tools/epio_summary.py results/e2e/<run> --experiment yutongwu-<run>    # 汇总表，同时写 <run>/epio_summary.md
python3 tools/netmon_report/ainic_netmon_import.py import results/e2e/<run>/netmon/* \
    --url http://127.0.0.1:28528 --experiment yutongwu-<run> --replace           # netmon 精确数据（推送丢点时）
python3 tools/epio_dash.py                                                       # 改了面板定义后重新生成
```

## 5. 注意

- 网卡的 2 ms 读数偶尔会出现超过线速的点（例如 500 Gb/s）：这是读数时间戳的误差，ainic-netmon 用请求和回复的中间时刻估计采样时间，个别读数会偏。
- VictoriaMetrics 删除后马上重导同一批序列，新数据要过几秒才查得到；数值不受影响。
- 汇总表里同名的两个 base 窗口（开头和结尾各一次）合成一行，显示后一次。
