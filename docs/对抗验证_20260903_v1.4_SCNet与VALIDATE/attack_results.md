# 第 19 轮 SCNet 人工确认闸/白名单闸 实弹攻击结果(2026-09-03)

被测: `scripts/scnet_client.py`(未做任何改动)。环境: 全程沙盒——`MDSIM_DATA_ROOT` 指 /tmp
临时目录,假凭证自建,`urllib.request.urlopen` 硬断,`scnet_client._http` 换计数 fake,
真实 `md_config.json` 不动(合成配置经 monkeypatch `_config` 注入)。
复跑: `python3.11 s<N>_*.py`(各自独立,共享骨架 `atk_common.py`)。
退出码 0=闸有效/设计意图,1=打穿。

## 打穿(发现,按严重度排序)

| # | 项 | 级别 | 证据 |
|---|---|---|---|
| F1 | **伪造批准文件被接受,人工闸可自批绕过**(s2) | **高** | 手写 `state/scnet_approve/<ph>.json`,内容仅 `{"approved_epoch": now}`(缺 plan 字段)/ts 伪造未来,`_valid_approval` 只验 `approved_epoch`(scnet_client.py:355-362),无签名、不复核 plan 内容 → 无 TTY 下 submitted=True、job 提交 1 次。agent 与真人同用户、对数据根有写权限,"agent 开不出批准"在代码级不成立——绕过 `approve()` 直接写文件即可。 |
| F2 | **一次性批准并发双花**(s12) | 中 | 两线程 barrier 卡在上传:检查(`_valid_approval`,:451)与销毁(`_consume_approval`,:494)之间隔整个上传+提交窗口 → job 提交 2 次、两线程同报成功。一次性承诺在并发下失效。 |
| F3 | **plan_hash 只绑 files 的 basename → 内容调包**(s5b) | 中 | 批准 `/inbox/good/production.in`,提交 `/inbox/evil/production.in`(同 basename 不同内容):hash 相同,上传 evil 内容、提交成功。人批的是 A 文件,实际跑的是 B 内容。 |
| F4 | **cluster_id 不进 plan_hash → 批准挪用他区域**(s5c) | 中 | 批准时区域A(cluster_id=11112),提交时 `cluster_id="22223"`:hash 不变,提交到"区域B-未批准"成功(remote_dir=/homeB/...)。`input_file`(显式 cmd 时)/`prod_dir` 同样不进哈希(静态:scnet_client.py:329-334)。 |
| F5 | **case_id `../` 远端目录逃逸 work_root**(s10) | 中 | `case_id="../../escape"` → remote_dir=`/public/home/fake/YMY-AI/../../escape`,远端 `mkdir createParents=true` 与 GAP_WORK_DIR 均在 YMY-AI 之外。case_id 全链无白名单校验(:418,440 仅 strip("/"));`;`、反引号、`$()`、空格、中文原样进 GAP_JOB_NAME/GAP_WORK_DIR(未逃逸,记录)。 |
| F6 | **配置类型混淆 fail-open**(s9) | 中 | `"enabled": "false"`(字符串 truthy)→ 放行提交;`"auto_submit": "no"` → 放行;`"queues": "debug"`(字符串)→ `queue="bug"` 子串命中放行。布尔/列表键类型错时闸门方向是"开"而非"关"。 |
| F7 | **gate 无下界**(s9) | 低 | `nodes=0`、`nodes=-1`、`walltime="00:00:00"`、畸形 `"1:99:99"` 全部放行提交(只查 `>` 上限)。 |
| F8 | **畸形配置崩溃式 fail-closed,拒绝不入审计**(s11) | 低 | `whitelist` 为字符串/列表 → AttributeError;`max_nodes: None` → TypeError;`max_walltime_h: "abc"` → ValueError。均未提交(fail-closed 成立)但异常在 `_audit` 之前抛出,审计零记录,调用方看到 traceback 而非干净 refused。 |
| F9 | **人工闸位于 authenticate/get_center 之后**(s1c) | 低 | 无批准无 TTY 的拒绝仍先产生 2 次只读外发(token 认证 + center 发现);离线环境下该拒绝路径变成 ScnetError 异常而非干净 refused。白名单拒绝路径(:421)在认证之前,实证零外发。 |
| F10 | **审计在提交之后:写崩则"已提交、无审计、调用方误判失败"**(s13b3) | 低 | `_audit` 抛 OSError 时:已批准路径作业已提交(job=1)但调用方收到 OSError 且无审计记录;拒绝路径零提交、异常上抛(fail-closed 成立)。 |

## 通过(闸有效)

- s1a/b/d: 无 TTY 直接 submit(管道 stdin)→ refused、零作业提交、审计 refused 入账;白名单拒绝路径零外发。
- s2(反向对照): 无合法批准时同参数必拒(s1b)。
- s3: 过期 31 分钟拒;边界钉死 1799s 放行/1801s 拒(`<= APPROVAL_MAX_AGE`)。
- s4: 批准后改 nodes/ppn/queue/walltime/case_id/cmd/files 任一项 → hash 全变、全部拒、零提交(7/7)。
- s5a: plan_hash 七字段逐一 diff 全部改 hash;16 hex=64bit,生日界 ~2^32,30 分钟窗内不可利用,仅理论标注。
- s7: 重放——首次成功+用后销毁,第二次 refused、零新增提交。
- s8b:  dangling symlink → fail-closed 拒。
- s13a/B1: 三种拒绝(白名单/无批准/过期)各落一条 refused;审计写崩时白名单拒绝仍零提交。

## 接受(设计意图/信任边界)

- s6: 提交中途失败(upload 断网)批准文件保留,30 分钟内同参数可重试、成功后销毁。注释明示"提交成功后才销毁"(:356,494);异参数重试由 s4 证必拒,不构成绕过。
- s8a: open/remove 跟随 symlink;但写 state 目录权限 ≡ 直接写批准文件(F1),symlink 不扩大攻击面,与基线 E2 同型(写状态者已在信任边界内)。**注意:F1 本身说明该信任边界假设对"agent 同用户"不成立,s8a 的接受不覆盖 F1。**
- s9: `queues: None`/whitelist 整缺 = 全队列放行——tests 钉死的设计(test_empty_queue_whitelist_means_all),仍受 nodes/walltime 上限约束。

## 与冻结基线的关系

全部发现落在 SCNet 新面,基线"已定稿决策"无一条覆盖;未翻案。F8 与 E7(写崩不得阻断入账/还原)精神同族,建议修复时参照:畸形配置一律转 refused+审计,不裸抛。
