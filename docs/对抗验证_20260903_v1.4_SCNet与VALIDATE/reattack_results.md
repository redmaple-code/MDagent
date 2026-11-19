# 第 19 轮修复后复攻结果(2026-09-03,reattack)

被测: `scripts/scnet_client.py`(第 19 轮修复后版本,未做任何改动)。
环境: 全程沙盒——`MDSIM_DATA_ROOT` 指 /tmp,假凭证,`urllib.request.urlopen` 硬断,
`scnet_client._http` 换计数 fake,真实 md_config.json 不动。零真实外发、零真实凭证。
复跑: `python3.11 r<N>_*.py`(共享骨架 atk_common.py,新增 eff_plan_hash/human_tty/
human_approve/write_eff_approval 适配修复后口径)。退出码 0=全部符合预期,1=有 FINDING。

## 口径注意(原脚本直接复跑的假信号)

修复#2 后 plan_hash 绑定 cluster_id/work_root/文件 sha256,原 s3/s6/s7/s9(两行)/
s12/s13B3 手写批准用的是旧口径哈希 → 批准天然不匹配,直接复跑会出现
"s3b 假 FINDING、s6 assert 失败、s7 假 FINDING、s12 双 0"等**脚本口径陈旧**信号,
非产品回退。另外 s13 用猴补丁整体替换 `_audit`,测不到修复后的真实 `_audit`
(降级逻辑在函数内部)。全部改用修复后口径重写为 r1–r10 裁决。

## 原发现 → 修复验证

| # | 原发现(级别) | 复攻脚本 | 结果 |
|---|---|---|---|
| F1 | 伪造批准文件自批绕过(高) | r1a–r1f | **修复有效**。六变体(缺 plan_hash/ts 未来/字段不符/epoch bool/epoch 字符串/内容非 dict)全拒、零提交、逐条入账。正规人工流(set_pin→dry-run→TTY+YES+PIN approve→submit)走通,批准文件 600、目录 700。同 uid 手写 schema 全合法批准仍被接受=r1g **ACCEPT**(代码注释明示的 E2 信任边界,基线已定稿不翻案)。 |
| F2 | 一次性批准并发双花(中) | r2a–r2d | **修复有效**。胜者占用批准上传期间败者并发发起:恰好 1 次 job、1 个成功,败者干净 refused 无异常;8 线程直击 `_take_approval` 恰好 1 人拿到;串行重放拒;3 批准×2 线程各自恰好 1 次。 |
| F3 | files 只哈希 basename→内容调包(中) | r3a/r3e/r3f | **修复有效**。同 basename 不同内容 hash 必变、调包提交拒;失败恢复后改内容重试也拒,且原批准不被误消耗(改回内容仍成功);文件顺序排序入哈希(正用不退化)。 |
| F4 | cluster_id 不进 plan_hash→挪用他区(中) | r3b/r3c/r3d | **修复有效**。挪区域 B 拒;批准后改 work_root 拒;显式 cluster_id==配置值正用放行。 |
| F5 | case_id `../` 逃逸 work_root(中) | r4 | **修复有效**。19 种脏 case_id(遍历/空格/分号/反引号/$()/中文/控制字符/首字符-/空/None/€)全部干净拒、**零外发**、逐条入账;合法 case_id 正用放行;CLI upload 脏 remote_case rc=3 零外发入账。 |
| F6 | 配置类型混淆 fail-open(中) | r5/r6 | **修复有效**。enabled/auto_submit 非 True 拒;queues 字符串/混非字符串拒;max_nodes 字符串/None/bool/float 拒;max_walltime_h 字符串/bool/inf 拒;全部干净 refused 无裸抛。 |
| F7 | gate 无下界(低) | r5 | **修复有效(nodes/walltime)**。nodes 0/-1 拒;walltime 00:00:00、1:99:99、12345:00:00(5位小时)、1:2:03、24:00:00.5 全拒;边界 24:00:00 含、00:00:01 放行。**但 ppn 下界漏修,见 N1。** |
| F8 | 畸形配置崩溃式拒绝、审计缺口(低) | r6 | **修复有效**。scnet None/字符串/空 dict、whitelist 字符串/列表等 10 种畸形全部干净 refused、零 job、零裸抛、逐条审计。 |
| F9 | 人工闸位于 authenticate 之后(低) | r7 | **修复有效**。无批准/过期批准/批准被取走三种拒绝全部零外发、入账;离线(fake 全炸)下拒绝仍是干净 refused 不裸抛。dry_run 无批准可跑=设计意图(r7d ACCEPT,只读外发 2 次、零 job、审计 dry_run)。 |
| F10 | 审计写崩致"已提交无审计+调用方误判失败"(低) | r8 | **修复有效**。真实 _audit 写盘必败(jsonl 占位为目录)下:拒绝路径干净返回不裸抛;已批准提交照常完成、调用方拿到 submitted=True;stderr 降级告警。盘满时审计缺失=E7 同型接受项。 |

**结论: 原打穿 10 项(F1–F10)9 项完全修复;F7 的 nodes/walltime 部分修复,
ppn 部分漏修 → 新发现 N1。**

## 新攻击结果

| # | 攻击 | 结果 | 级别 |
|---|---|---|---|
| **N1** | **ppn 无下界不入闸**(r5_ppn_0): `submit_job` 只对 ppn 做 int() 收敛,`gate_check` 根本不收 ppn——ppn=0/-3/999999 全放行,带着合法批准提交出 GAP_PPN="0"、cmd `-np 0` 的畸形作业,白白消耗一次性批准。修复清单声称"nodes/ppn 正整数"但 ppn 正整数校验实际缺失。不越 nodes/walltime 资源上限,方向是浪费/畸形 | **FINDING(打穿)** | **低** |
| N2 | PIN 环境变量绕过(r1m): 源码无 env 取 PIN 行,getpass 不读 env;设 SCNET_PIN/PIN 后无 TTY 仍拒 | PASS | — |
| N3 | pin.json 被同 uid 替换成已知哈希→攻击者 PIN approve 成功(r1l) | ACCEPT(E2 同型信任边界,与手写合法批准文件等价,基线已定稿) | — |
| N4 | set_pin 规则(r1k): 无 TTY CLI 拒/短 PIN 拒/换 PIN 无旧拒/错旧拒/正旧成;畸形 pin.json 四种全 False 不裸抛(r1n);approve 未知 ph 干净拒(r1o) | PASS | — |
| N5 | _take_approval 竞态新变体(r2b/r2d): 8 线程直击恰好 1 人拿;多批准并发各自恰好 1 次 | PASS | — |
| N6 | 失败恢复"批准延期"(r9a): 恢复不刷新 approved_epoch,猴补丁时间前进后 age 仍卡死拒 | PASS | — |
| N7 | 恢复后 plan_hash 漂移(r3e)、取走期间伪造被恢复 rename 原子覆盖(r9d)、连败两次后第三次成功(r9c) | PASS | — |
| N8 | `.taken-*` 孤儿堆积(r9b2): 无 GC,崩溃/攻击可占盘堆积;但 `_valid_approval` 只认 `<ph>.json` 精确名,taken 永不重新生效,无安全语义 | ACCEPT(观察/低) | 观察 |
| N9 | dry_run 零批准可跑(r7d,有意设计)、delete 不走人工闸(r10f2,用户拍板)——均实录确认已纳入各自闸+审计 | ACCEPT | — |
| N10 | token 缓存 600/上传文件名白名单/>100MB 拒/auth+query+region 畸形响应防护/job_id 白名单(含 slurm array 123_4 正用)/delete 总闸+审计(r10 全项) | PASS | — |
| N11 | PIN 暴力破解无节流(静态): PBKDF2 10 万轮 ~0.05s/次,6 位纯数字 PIN 同机穷举约 14h;但每次失败入审计,且同 uid 攻击者本可直接换 pin.json(N3,E2 内)——不扩大攻击面 | ACCEPT(观察) | 观察 |

## 与冻结基线的关系

全部裁决依据 `docs/对抗基线_ADVERSARIAL_BASELINE.md`;E2(同 uid 写状态者信任边界)
适用项(r1g/r1l)按基线判 ACCEPT,未翻任何前轮定案。唯一 FINDING(N1,低)建议修复:
`submit_job` 在 int() 收敛后加 `ppn < 1` 拒绝(与 nodes 同构),或 gate_check 增 ppn 形参。

---

## 收官复攻(二轮修复后)

被测: 二轮修复后 `scripts/scnet_client.py` + kit 侧
`agent_deploy_kit_20260630/agents/runtime/v3.3/{validate_agent.py,orchestrator.py}`
(均未做任何改动)。环境同前: 沙盒零外发零真实凭证。新脚本 `r11_final.py`(逐修复点);
回归抽查: r1–r10 全量复跑(PIN 口径按 HD1 新政策适配为 8 位非纯数字,属测试脚本
适配非产品变更) + 原 s1/s2/s10/s11 直接复跑。

### 新修复点验证(r11_final.py,全 PASS)

| 修复点 | 验证 | 结果 |
|---|---|---|
| N1 ppn 入闸 | ppn=0/-3/True/'x' 全拒+入账(含 gate_not_armed 复合标记),ppn=32 正用放行;gate_check 直喂 bool 拒 | **PASS,N1 关闭** |
| MD1 四处 int 捕 OverflowError | validate_agent steps/cores/timeout=Infinity 实跑 rc=0 结果落盘无 OverflowError;orchestrator `_handle_validate` validate_timeout=inf → 回退 300、钳 [30,3600](外层 360)、fail-closed 分支健全、phase 正常推进 | PASS |
| MD2 work_root 逐段白名单 | `../escape`、`a/../../b`、`a b`、`-lead` 拒(零外发);`ok-root/sub.dir_2` 正用放行;CLI upload 脏 work_root rc=3 | PASS |
| LD1 fchmod 兜底 | 预置 644 的 token 缓存/pin.json/批准文件,写后实测全 600 | PASS |
| LD2 恢复不覆盖新批准 | 取走期间人工重批(目标存在)→ 恢复删 taken 不覆盖,新批准 epoch 不变、无孤儿、后续提交成功 | PASS |
| LD3 dry_run_failed | dry_run 中途(center 炸)异常上抛且审计落 `dry_run_failed` | PASS |
| LD4 _approval_path 16-hex 白名单 | 穿越/大写/超长/空全 param 拒;`_valid_approval(脏)` 返回 False 不裸抛 | PASS |
| LD5 validate 历史文件清理 | 预置 validate_99999/88888.* 四件 → 实跑后全清,本次 pid 三件保留,报告落盘,无关文件不动(本机 lmp 真实跑通 5 步) | PASS |
| LD7 query list=None | `query_jobs` 返回 [] | PASS |
| HD1 代码侧 | set_pin: 纯数字 8 位拒、7 位拒、8 位混合/纯字母放行;pin.json 缺失时拒绝原因与审计双带 `gate_not_armed`,设 PIN 后标记消失 | PASS |

### 回归抽查(无回退)

- r1–r10 全量复跑: **exit 全 0、FINDING 全 0**(r1 P5 原本借用 `write_approval_file("pin",…)`
  写 pin.json,被 LD4 白名单拦下——恰为 LD4 生效的旁证,已改直写)。
- 原 s1/s2/s10/s11 直接复跑: exit 全 0。
- 回归门: **dsh `Ran 207 OK`;kit `Ran 128 OK`;node(dsh-files,node v22.19 `node --test`)
  57/57 全绿**(用户口径"node 24"无对应套件,实测该面 57 全绿;tools/archify 的
  npm test 引用缺失脚本属陈旧目录,与本轮修复面无关,记录在案)。

### 结论

二轮修复 10 项全部验证有效,无新 FINDING;一轮 F1–F10 + N1 全部关闭。
残余接受项(E2 同 uid 信任边界、dry_run 零批准、delete 无人工闸、孤儿 taken 无 GC、
PIN 无节流)均维持前轮 ACCEPT 定级,未翻基线。
