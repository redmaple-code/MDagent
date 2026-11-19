# 第 19 轮对抗审计 — kit VALIDATE 面(validate_agent + orchestrator VALIDATE 相位)

- 日期: 2026-09-03
- 审计对象:
  - `<kit>/agents/runtime/v3.3/validate_agent.py`(新增)
  - `<kit>/agents/runtime/v3.3/orchestrator.py`(`_handle_preflight` VALIDATE 分支 / `_handle_validate` / dispatch)
  - `<kit>/tests/test_validate_agent.py`(22 用例,口径参考)
- kit 根 = `/opt/mdagent/kit`
- 裁决标准: `docs/对抗基线_ADVERSARIAL_BASELINE.md`(第 18 轮冻结);本轮为 VALIDATE 新面,基线未覆盖,无翻案项。
- 工作方式: 只读审计 + /tmp PoC(含真实 lmp_mpi 2Aug2023 E2E 试跑);未改任何代码、未碰在役服务。
- 回归门: `python3.11 -m unittest discover -s tests`(kit 根)= **Ran 120, OK**(含新增 22 用例)。

结果总览: **高危 1 / 中危 2 / 低危 6**;已验证无发现面 12 项(见文末)。

---

## 高危

### H1 `-nan`(负 NaN)逃逸 no_nan;落在热力学末行时 temp_sane 亦被 `max()` 跳过 → 6 项验收全过

**位置**: `validate_agent.py:59`(非有限值词表 `("nan","inf","-inf")` 缺 `"-nan"`)、`:108`(`tmax = max(r[1] for r in rows)`)。

**场景**: LAMMPS 在 glibc 上以 `%g` 打印负 quiet NaN 时输出 `-nan`(体系爆炸时 temp/energy 由负动能开方产生,常见)。该 token:
1. 不在词表 → `bad` 不计数;
2. `float("-nan")` 合法解析为 NaN,不触发 ValueError → 该行被当作正常行入 `rows`;
3. `no_nan` 通过;若 `-nan` 位于**最后一行**,`max([300.0, nan])` 返回 300.0(nan 比较恒 False 被跳过)→ `temp_sane` 也通过;
4. `steps_done` 用末行 step(正常递增)→ 通过。

**证据**(/tmp/validate_audit/poc2.py,python3.11 与 python3.6 下一致):

```
text = "Step Temp PotEng TotEng Press Density\n"
     + "0 300.0 -15234.5 -15200.1 120 1.234\n"
     + "5 310.0 -15235 -15201 119 1.234\n"
     + "10 -nan -15235 -15201 119 1.234\n"
     + "Loop time of ...\nTotal wall time: 0:00:01\n"
check_run(text, 10, 4800.0) → 六项全 pass=True, OVERALL PASS: True
```

对比: 同为末行 `-nan` 在首行时仅靠 `max` 排序偶然性被 temp_sane 拦下(`max([nan,300])=nan`,`nan<=cap` 为 False)——拦截与否取决于 nan 出现的位置,属未定义行为兜底,不是防线。

**后果**: 末段跑飞的体系(温度 NaN)试运行"验收通过",放行 LAUNCH 跑生产——test-run-first 的核心拦截目标漏网。

**修复建议**: 解析后逐值 `math.isfinite` 复核(py3.2+,3.6 可用)——`rows` 入列前对所有 6 列做 isfinite,非有限计入 `bad`;同时把 `"-nan"` 补进词表做双保险;tmax 计算前丢弃/判败含非有限值的行。回归测试: 末行 `-nan` 必须整体 FAIL。

---

## 中危

### M1 orchestrator 侧 `int(task["validate_timeout"])` 无防护: 畸形值令任务永久卡 VALIDATE(每 10s 静默抛错循环,无飞书无 FAILED)

**位置**: `orchestrator.py:382` `timeout = int(task.get("validate_timeout", 300))`。

**场景**: task JSON 中 `validate_timeout` 为 `"abc"`、`None`、或对象等非法值时,`int()` 抛 ValueError/TypeError,逃逸出 `_handle_validate`。`AgentBase._run_loop`(harness/core/agent_base.py:74-79)捕获异常仅记日志,相位不变、状态不推进、无飞书。每 10s 一个 tick 重复抛错——任务**永久卡死 VALIDATE**,操作员无任何通知;且 validate_agent 自身的 timeout 钳位(`max(30, ...)`)永远到不了。
同位置另一半: timeout **只钳下限 30 不钳上限**,`validate_timeout=10**8` 时 `_run_agent(timeout=timeout+60)` 与 agent 内 `proc.wait(timeout)` 同量级,主循环单 tick 挂死该时长。

**证据**(/tmp/validate_audit/poc_orch_timeout.py):

```
ValueError: invalid literal for int() ... | phase stays: VALIDATE
-> 每 tick 重复抛错,任务永久卡 VALIDATE,无飞书无 FAILED
```

**修复建议**: orchestrator 侧与 agent 同口径 try/except 回退 300,并加上限钳(如 `min(timeout, 3600)`);或干脆把字符串原样透传由 agent 统一钳位(agent 已有防护)。

### M2 能量列非有限值逃逸: `1e999`/`infinity` 经 `float()` 静默得 inf,no_nan/temp_sane 均不拦

**位置**: `validate_agent.py:59-67`。

**场景**: `float("1e999")` 溢出返回 `inf` 不抛错;`float("infinity")`/`float("-infinity")` 同样是合法词。这些 token 不在 `("nan","inf","-inf")` 词表 → 行被当正常行。PoC 实证:

```
overflow_1e999_pe   overall_pass=True   (PotEng=inf 全过)
infinity_word       overall_pass=True   (PotEng=infinity 全过)
```

temp_sane 只看 Temp 列,能量列 inf(物理上即体系爆炸)完全无防线。注意 Temp 列的 `1e999` 会被 temp_sane 拦住(已 PoC 验证),所以危害限于能量/压力/密度列。

**修复建议**: 同 H1——入列前全列 `math.isfinite` 复核,一处修复两案并案。

---

## 低危

### L1 VALIDATE 失败时 bus status 永留 "VALIDATING"

**位置**: `orchestrator.py:396` 写 `status: VALIDATING`;失败分支(404-414)只改 `_state["phase"]=FAILED`,不回写 bus。

**场景**: 看板对该 case 永久显示 "VALIDATING",实际已终态 FAILED。飞书有通知(不致失声),且与既存 launch 失败留 "RUNNING"(:431)同型——属 kit 既有模式的新实例,非本特性独有问题。

**修复建议**: `_handle_validate` 失败分支 `_update_bus(case_id, {"status": "FAILED"})`;是否回溯修 launch 同款另案评估。

### L2 只截首个 run——分段升温程序后续段缺陷不在试跑覆盖内

**位置**: `validate_agent.py:25-41`(设计既定)。

**场景**: 生产 input 多 run 分段(升温→平台→采样),第二段起的 fix/unfix/变量引用错误试跑不暴露,仍会在 LAUNCH 后 MONITOR 阶段才崩。属 test-run-first 预算约束下的既定取舍,但 `validate_report.json` 与飞书文案未向用户明示"仅覆盖首段"这一边界,易给操作员"整脚本已验证"的错觉。

**修复建议**: 报告与通知中加 `coverage: "截断至首个 run(共 N 个 run)"` 字段;后续可考虑按段数加权步数预算,另案。

### L3 lmp 退出码 rc 不参与验收

**位置**: `validate_agent.py:190-216`(rc 仅入报告,不进 checks)。

**场景**: lmp 文本输出全绿但 rc≠0(如 MPI finalize 告警、信号收尾)判 pass。现实中主要失败模式(崩溃/ERROR/lost atoms/nan)均有文本特征,方向可接受;且若强卡 rc 反而引入 finalize 类假阳性。留痕备查。

**修复建议**: 报告中把 `rc != 0` 折成 `warning` 字段提示人工,不卡验收。

### L4 orchestrator 被 SIGKILL 于 VALIDATE 中途 → validate_agent 孤儿化 + 结果文件残留 + 重跑并发写

**位置**: `orchestrator.py:141` `_run_agent` 的 `subprocess.run` 无 `start_new_session`;`validate_agent.py:181-186` validate.in/stdout 固定名覆盖写。

**场景**: orchestrator 被 kill -9(SIGTERM 有 atexit/signal 链, SIGKILL 无)时, validate_agent 子进程孤儿化继续跑至自身 timeout, `.agent_validate_agent_*_result.json` 残留 BASE 无人清理(O1 的 finally 随主进程死亡失效);orchestrator 重启后相位仍是 VALIDATE 幂等重跑,与孤儿并发写同一 `validate.in`/`validate.stdout`,输出交错→解析混乱。方向 fail-closed(交错输出几乎必触发某项 FAIL),概率低(orchestrator SIGKILL 且恰好 VALIDATE 中途)。

**修复建议**: `_run_agent` 对长时 agent 加 `start_new_session=True` 并在 timeout/异常路径 killpg;或 validate 输出文件名带 uid。

### L5 存量 task 无 `validate` 键默认 True → 升级后一律强制试跑

**位置**: `orchestrator.py:278` `task.get("validate", True)`。

**场景**: 状态文件/队列中在飞的旧 task(无该键)升级后走 PREFLIGHT→VALIDATE→LAUNCH。这是 test-run-first 的既定设计意图(默认开启、显式 `validate=False` 旁路),不算破坏兼容;但运维需知晓: 试跑假阴性(如 H1 修复前的边界情形)会**整体阻断且无自动重试**,唯一旁路是人工编辑 task。建议在部署说明/变更日志中明示。

### L6 task 路径注入面与 launch_agent 同级(信任边界内,留痕)

**位置**: `validate_agent.py:157-176`。

**场景**: `data_file`/`ff_file` 可为任意可读路径(读侧,拷入 prod_dir 固定名 `heat_final.data`/`ffield.reax.cand`,**无写穿越**);`input_file` 为绝对路径时 `os.path.join(prod_dir, ...)` 逃逸 prod_dir 读任意文件作输入源。task JSON 由操作员/自有链路产出,与 launch_agent 既存信任边界一致(R2/E2 同类: 能写 task 者已在边界内)。留痕,不建议本轮动作。

---

## 已验证无发现的面

1. **进程树清理**: `start_new_session=True` + `os.killpg(pid, SIGKILL)` PoC 实证嵌套进程树(shell 下三层 sleep)全部杀净,无残留(`poc_killpg.py`)。Intel MPI hydra 本地派生同组,语义成立。
2. **Lost atoms 解析**: 畸形行(缺 current/非数字)走 ValueError/IndexError → `lost_ok=False` fail-closed;`original==current` 正确放行(测试套件 + PoC)。
3. **thermo 列错位**: 截断尾部强制 `thermo_style custom step temp pe etotal press density`,覆盖生产 input 的任何 thermo_style(LAMMPS 后设置生效),列序固定;真实 lmp_mpi 2Aug2023 E2E 试跑 10 步,头部逐字匹配、6 项验收全过、`steps_done` 正确(=10)。
4. **echo 注入**: 实测该 LAMMPS 构建 `-in` 默认不回显输入行(注释中 "Total wall time" grep 计数 0);`print` 伪造须 lmp 真实执行,伪造行先于 run,真 run 崩溃必带 ERROR/缺 thermo 行——无法整体伪造 PASS。`completed` 单项理论可经 echo 注释抬过,但其余五项兜住。
5. **steps_done 只验末行**: 单 run + `thermo 1` 每步必打印,中途崩溃即缺行 fail-closed;跳行伪造与现实崩溃不可兼得。
6. **temp_cap 畸形 schedule**: 非数值/短段/非列表全被 `(TypeError, ValueError, IndexError)` 捕 → 兜底 10000K(合理,ReaxFF 高温亦不过 3000K);schedule 含 nan → cap=nan → temp_sane 恒 False(fail-closed)。
7. **build_validate_input 截断**: `run_style`/`rerun`/注释/缩进/Tab 均不误判(测试 + PoC);`run ${var}` 正常截断且变量定义保留;仅 if-guard run 时返回 None → orchestrator 落 FAILED + 飞书(fail-closed,测试钉死)。
8. **cores/steps 钳位**: 0/负/非数值/超大字符串全部落 [1,4]/[1,10000](agent 侧 try/except,py3.6 int 无位数限制)。
9. **Beta 后验污染(学习链)**: `_settle_strategy_attempts` 仅在 MONITOR 完成(:790)与 RESEARCH 路径(:969/:1034)调用,VALIDATE FAILED 不触发;`recover_agent` 不消费 `strategy_decisions.jsonl`——试跑失败不进学习、不污染后验。
10. **崩溃恢复/状态机接缝**: 相位 "VALIDATE" 在进入时即持久化(前置 tick 末尾 `_save_state`),orchestrator 重启后幂等重跑(copyfile/validate.in/stdout 全覆盖写);VALIDATE 路径不触碰 backoff/cooldown/monitor 计数;FAILED 为终态,与 preflight/launch 失败同语义。
11. **脏文件复用**: launch_agent.py:72-75 在源≠宿时**总是 cp 覆盖**,validate 半途遗留的 `heat_final.data` 会被后续 LAUNCH 刷新;仅当 task.data_file 恰为 `prod_dir/heat_final.data` 本体时跳过(同一文件,无脏读)。validate_agent 注释"幂等跳过复制"措辞与 launch 行为略有不符,但行为安全。
12. **py3.6 兼容**: 两文件 `python3.6 -m py_compile` 通过;全部 PoC 在 `/usr/bin/python3.6`(3.6.8,即在役解释器)下复跑结果一致;无 f-string `=`/dataclass/walrus/`capture_output` 等 3.7+ 用法;`max(default=)`/`wait(timeout)`/`start_new_session`/`errors="replace"` 均 3.6 可用。

---

## 复跑脚本

均在 `/tmp/validate_audit/`(只读审计,未改 kit):
- `poc_checks.py` / `poc2.py`: check_run/_parse_thermo/build_validate_input 攻击面(H1/M2 实证)
- `poc_killpg.py`: 进程组清理
- `poc_orch_timeout.py`: M1 卡死实证
- `lj/` + `e2e/`: 真实 lmp_mpi E2E(10 步全过 + echo 验证)

## 与基线的关系

本轮全部发现落在基线未覆盖的 VALIDATE 新面;已定稿决策零翻案、零冲突;七条红线(R1-R7)不涉。回归门 kit 120 绿(基线文档记录的第 18 轮值 84 已被本特性扩至 120,本文档执行记录待修复轮更新)。
