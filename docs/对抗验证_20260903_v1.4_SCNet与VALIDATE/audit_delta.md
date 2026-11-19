# 第 19 轮修复 delta 复审报告(audit_delta,2026-09-03)

> 范围:**只审第 19 轮修复引入的改动(delta)**——scnet_client.py 加固、kit validate_agent
> 热力学复核/报告扩编/pid 文件名、orchestrator._handle_validate 防护。原发现不重复报,
> 只判"修复是否真闭合"与"修复本身的新 bug"。基线 `docs/对抗基线_ADVERSARIAL_BASELINE.md`
> 已定稿决策零冲突。
>
> 工作方式:只读审计 + /tmp PoC(mock,urlopen 硬断,零外发,不改被测代码)。
> PoC: `/tmp/delta19/poc_scnet_delta.py`、`/tmp/delta19/poc_validate_delta.py`、
> `/tmp/delta19/poc_pin_bootstrap.py`,全部可复跑。
>
> 结果总览:**高 1 / 中 2 / 低 7**;修复点闭合核销 14 项;无发现面 13 项;回归门全绿。

---

## 高危

### HD1 PIN bootstrap 窗口:pin.json 未设时人工闸可被同 uid agent 经 pty 自铸根信任(H1/H2 修复未真闭合)

- 位置:`scripts/scnet_client.py` `set_pin()` :440-451 + CLI `--set-pin` :851-865;
  `_require_pin` :463-472。
- 场景:H1(伪造批准)/H2(pty 绕 TTY)的修复把 PIN 设为人工闸的根信任——"agent 不知
  PIN"。但**首次设置 PIN 无需任何旧凭证**:CLI 只查 `sys.stdin.isatty()`,而 pty 下
  isatty 为真、getpass 从 pty 读输入正常。同 uid agent 因此可以:
  ① pty 跑 `approve --set-pin` 自设一个自己知道的 PIN;② 正常 `submit --dry-run`
  拿 plan_hash;③ pty 跑 `approve --plan-hash`(YES+自设 PIN)落批准文件;
  ④ pty 或批准文件通道 submit。**人工确认闸全链自批,零真人参与**。
  当前生产即处窗口内:`mdsim-data/state/scnet_approve/` 为空目录(pin.json 从未设置),
  而 `md_config.json` 的 scnet.enabled/auto_submit 已双 true。
- 证据:`/tmp/delta19/poc_pin_bootstrap.py`——pty 起真实 CLI `approve --set-pin`,
  两次喂入 `agent1337`,退出码 0、输出"PIN 已设置"、`scnet_client._verify_pin("agent1337")`
  返回 True(沙盒数据根,零外发)。生产侧证据:`ls state/scnet_approve/` 为空;
  md_config scnet 段双 true(真机联调已开)。
- 修复建议:
  1. **运维立即闭环(先决)**:人工在真终端跑 `approve --set-pin`,并把"启用 scnet 前
     必先设 PIN"写进 setup/部署清单;PIN 未设期间 enabled=true 等于闸未就位。
  2. 代码侧抬高可见性:`set_pin`/`_audit({"action":"pin_set"})` 已有审计,建议 pin_set
     事件同步飞书告警(agent 自设会被立刻发现);`submit` 在 pin.json 缺失的拒绝理由与
     审计中显式标 `gate_not_armed`,便于巡检钉住该状态。
  3. 提高自铸/爆破成本:`PIN_MIN_LEN` 6 → ≥10 且禁纯数字(pin.json 同 uid 可读,
     PBKDF2-100k 下 6 位纯数字离线爆破 ~1 天量级,见 LD 附注)。
  4. 根因说明:同 uid 下代码无法区分 pty 后是人还是 agent,**纯代码无法彻底闭合**;
     须用户裁决:接受(靠①+②运营闭环)或引入带外因子(如 PIN 只存用户私机、
     approve 改在私机签名)。此条不违基线 E2——E2 接受的是"信任边界内伪造台账",
     而本闸的声明威胁模型恰恰包含同 uid agent(用户拍板"人工确认代码强制")。

## 中危

### MD1 `_handle_validate` 的 int() 防护漏 OverflowError:`validate_timeout=Infinity` 仍永卡 VALIDATE(M1 未闭全)

- 位置:`agents/runtime/v3.3/orchestrator.py` :384-390;同型三处在
  `validate_agent.py` :139/:144/:148(见下,影响不同)。
- 场景:修复用 `except (TypeError, ValueError)` 兜畸形 timeout。但 `int(float("inf"))`
  抛 **OverflowError**,不在捕获列。而 Python `json.dump` 默认 `allow_nan=True` 会把
  `inf` 写成 `Infinity`,`json.load` 默认又读回 `inf`——orchestrator `_save_state`
  正是裸 json.dump。只要 task 的 validate_timeout 以任何途径(手工编辑 state、上游
  SDK 写入)落进 ±inf,每次 tick 都在 :385 抛 OverflowError → 相位永卡 VALIDATE、
  无 FAILED、无飞书——正是 19 轮 M1 的原始症状,对该输入子集未闭合。
- 证据:`/tmp/delta19/poc_validate_delta.py` V1——json 往返 `Infinity` 成立;
  stub 调 `OrchestratorAgent._handle_validate`,inf → OverflowError 逃逸,
  phase 停 VALIDATE、bus/feishu 皆空;对照 nan → ValueError 被兜住、正常进 LAUNCH。
- 修复建议:`except (TypeError, ValueError, OverflowError)`,或先
  `isinstance(t,(int,float)) and math.isfinite(t)` 再 int。
  validate_agent 内三处同型 int() 建议一并统一修——那边崩溃→非零退出→
  orchestrator 收 error 字典→FAILED,fail-closed 成立,故仅一致性问题(V2 注记)。

### MD2 `work_root` 未过 `_check_safe_name`:静态畸形值令 remote_dir 逃逸 homePath

- 位置:`scripts/scnet_client.py` :592/:632-633(submit)、:826-828(CLI upload)。
- 场景:19 轮 M1 修复把 work_root 绑进 plan_hash,**批准后漂移**已闭合;但
  `work_root` 本身从无字符校验。`md_config` 写入 `".../attacker"`(手滑/误配)时,
  `remote_dir = homePath + "/../../attacker/" + case_id`,规范化后落在 homePath 之外
  (PoC:`/public/home/fake/../../attacker/d2case` → `/public/attacker/d2case`),
  mkdir(createParents)/上传/作业工作目录全部越界。case_id/remote_case/上传文件名
  都有白名单,独独拼路径的第二段没有。当前唯一防线是 approve/TTY 展示 remote_dir
  靠人眼认出 `..`——19 轮 H2 的教训正是"人不能当主要防线"。
- 证据:`/tmp/delta19/poc_scnet_delta.py` D2——合法配置仅改 work_root,dry_run 放行
  并展示逃逸后的 remote_dir;plan_hash 正常计算(绑定生效,但绑的是畸形值本身)。
- 修复建议:`work_root` 入 `_check_safe_name`(submit 与 CLI upload 两处);若需多层
  目录,逐段 split("/") 校验、禁 `.`/`..` 段。

## 低危

- **LD1 token 缓存 0600 仅在创建时生效**(:190-192)。`os.open(O_CREAT, 0o600)` 的 mode
  不作用于已存在文件;旧版竞态窗口/手工 touch 留下的 0644 缓存被 O_TRUNC 重写后仍 0644。
  证据:PoC D1(预置 0644 → authenticate 重写后仍 644)。生产现状 scnet_tokens.json
  已是 600(未爆雷),属升级路径残留。修复:写后 `os.fchmod(fd, 0o600)` 兜底一行。
  附:生产 `state/scnet_approve/` 目录现状 0755(旧建遗留),`_approval_dir()` 每次调用
  会 chmod 700 纠正,下次任意 approve/submit 调用即自愈——注记。
- **LD2 失败恢复 rename 覆盖并发新批准**(:679-683)。提交在途时人工对同 plan 重新批准
  (epoch 刷新),失败恢复 `os.rename(taken, path)` 静默覆盖新批准,旧 epoch 盖新 epoch。
  plan_hash 相同 ⇒ 参数同源,无安全后果,仅丢失刷新的 30 分钟窗。证据:PoC D4。
  修复:恢复前读新旧 approved_epoch 比较,或恢复目标不存在才 rename。
- **LD3 dry_run 网络异常被误记 `submit_failed` 且异常上抛**(:678-685)。dry-run 也需要
  authenticate/get_center 拼 remote_dir;离线时 except 分支对 dry_run 同样记
  `submit_failed` 并 raise → CLI traceback,审计统计被污染。证据:PoC D5
  (actions=['submit_failed'])。修复:except 内按 dry_run 分流,记 `dry_run_failed`
  或干净返回 refused。
- **LD4 `approve --plan-hash` 未做格式校验,`_approval_path` 可目录穿越**(:427-428,
  :520/:555)。ph 直接拼路径,`../../tmp/x` 逃逸批准目录(PoC D3)。写路径需
  TTY+YES+PIN+对应 dry_run 审计行,实际只能人踩自己脚;一行
  `re.fullmatch(r"[0-9a-f]{16}", ph)` 焊死。
- **LD5 试运行产物不清理 + 报告文件无 pid**。`validate_<pid>.{in,stdout,err}` 每次试跑
  留 3 个文件于 prod_dir 积灰;`validate_report.json`(无 pid)可被前次崩溃遗留的孤儿
  进程晚写覆盖——orchestrator 以 `_run_agent` 的 pid+uuid result 文件为准,影响仅限
  人读报告。修复:报告亦带 pid,或写前 fsync+写后清理历史 validate_* 临时文件。
- **LD6 bus case status 枚举缺 "VALIDATING"**(`harness/schemas/status_bus.schema.json` :26,
  orchestrator :413)。枚举亦无既有的 "DONE"(:1063)——同型既有漂移;且 case schema
  required 六字段意味着 jsonschema 在场时整个 bus 写入早已降级,本机无 jsonschema
  故惰性。仅口径注记:枚举补 VALIDATING/DONE。
- **LD7 `query_jobs` 的 `(data or {}).get("list", [])`**(:728):响应 `"list": null` 时
  返回 None,调用方迭代即 TypeError。一行 `or []`。
- 附注(HD1 关联):pin.json 同 uid 可读 → PBKDF2 盐化哈希可离线爆破,`PIN_MIN_LEN=6`
  纯数字约 10^6×0.1s ≈ 1 天;提高最小强度见 HD1 建议 3。

---

## 修复点闭合核销(逐项)

| 修复点 | 判定 |
|---|---|
| PIN 闸(set_pin/_verify_pin/_require_pin,PBKDF2-100k,pin.json 0600) | 机制本身正确,但根信任可自铸 → **HD1 未真闭合**;pin.json 0600 仅创建时生效同 LD1 类(父目录 700 兜底) |
| approve 流程 TTY+YES+PIN、`_tty_confirm` 加 PIN | TTY 闸在 pty 下为真属已知(H2),PIN 依赖"未自铸"前提 → 并入 HD1 |
| `_valid_approval` 强 schema | **闭合**。True/"123"/nan/inf/负数/未来戳/缺字段全拒(_is_int 排 bool;nan/inf 比较恒假;float epoch 年龄计算正确) |
| `_take_approval` 原子 rename、并发干净拒 | **闭合**(rename 原子,败者 fail-closed 有审计);恢复覆盖并发新批准 → LD2 |
| submit_job 重构(闸前置/dry_run/失败恢复) | 主链**闭合**;dry_run 异常误标 → LD3 |
| plan_hash 新签名(文件 sha256+cluster_id+work_root) | **闭合**(漂移攻击 s4/M1 类全堵);OSError→ScnetError→submit_job 捕获干净 refused;work_root 静态畸形 → MD2 |
| `_check_safe_name` 白名单(case_id/remote_case/上传文件名) | **闭合**;漏 work_root → MD2 |
| gate_check 全类型 fail-closed | **闭合**;queues 空表=全放行系基线钉死语义(audit_scnet :110),不报;queue 非 str 在非空白名单下 fail-closed |
| `_parse_walltime_h` 严格正则 / `_check_job_id` / urlencode / delete+upload 纳闸 | **闭合** |
| `_audit` 吞 OSError 降级 stderr | **闭合** |
| authenticate/_platform_token/region_token/query_jobs 畸形防护 | **闭合**(L5 三处均类型检查);query list:null → LD7 |
| token 缓存 os.open 600 | 部分闭合 → LD1 |
| `_parse_thermo` 词表+逐值 isfinite(H1/M2) | **闭合**。PoC V3:1e999/Infinity/+inf/-nan/nan/-infinity 全判 bad;`1e-9999`→0.0 为有限值正确放行(0K 物理合理性不在 no_nan 口径) |
| timeout try/except+钳 [30,3600](M1) | **未闭全** → MD1(OverflowError 逃逸) |
| 失败分支 bus FAILED + 飞书(L1) | **闭合**(两分支均 _update_bus FAILED;_feishu/_log_strategy/_update_bus 内部全 try/except 不级联) |
| 报告 coverage/warning(L2/L3)、pid 文件名(L4) | **闭合**(见无发现面) |

## 无发现面(核销)

1. `_require_pin` 的 getpass 无 TTY fallback 读管道:三个调用点(_tty_confirm/approve/
   --set-pin)均先过 isatty 闸,fallback 路径不可达。
2. `_take_approval` dst 冲突:uuid4 后缀,可忽略;dst 已存在的 rename 覆盖语义不构成问题。
3. epoch 类型矩阵:bool/str/nan/±inf/负值/超窗/未来全拒(代码走查+类型推演)。
4. nodes/ppn 收敛:int() 截断 float(1.9→1)只往小走不越权;int(True)=1 无害;非数值
   字符串 ValueError 干净拒。
5. 恢复/审计顺序:先 rename 恢复再 _audit,_audit 自身吞 OSError,无审计断链新口。
6. 成功路径一次性语义:提交成功后 os.remove(taken),复用需重新批准;失败恢复可重试。
7. coverage 计数 `ln.split()[:1]==["run"]` 与 build_validate_input 的 `tokens[0]=="run"`
   同对象同语义,缩进/注释(`# run`)两两侧口径一致。
8. pid 文件名无旧名引用:kit 全仓 + dsh-agent 全仓 grep `validate.in`/`validate.stdout`/
   `validate_report` 零读者(dashboard/web_monitor/claude_interface/launch_agent/脚本均无)。
9. `_run_agent` 五路径全返回 dict,`result.get` 不崩;agent 结果文件被写成 `null` 的极端
   情形也被 _handle_validate 的 except Exception 兜进 FAILED(fail-closed)。
10. except Exception 不吞 KeyboardInterrupt(非 Exception 子类);SIGKILL 孤儿由 pid 文件名
    + start_new_session + killpg 组合缓解(19 轮 L4 已修)。
11. 上传 100MB 上限:超限干净拒;getsize→read 间 TOCTOU 仅内存放大,同 uid 边界内,注记。
12. 审计注入:case_id/cmd 任意字符经 json.dumps 落 jsonl 不可伪造行(沿用 19 轮结论,
    本轮改动未新开审计面)。
13. 凭证/token 泄漏面:ScnetError 不带 SK/token;CLI auth 掩码;PIN 不落任何日志/审计。

## 回归门(本机复跑)

| 套件 | 命令 | 结果 |
|---|---|---|
| dsh-agent python | `MDSIM_KIT_BASE=<kit> python3.11 -m unittest discover -s tests` | **202 OK** |
| kit python | `python3.11 -m unittest discover -s tests`(kit 根) | **127 OK** |
| node | `node --test tests/test_flow_gate.mjs tests/test_wechat_push.mjs` | **24 pass / 0 fail** |

## PoC 清单(均可复跑,零外发)

- `/tmp/delta19/poc_pin_bootstrap.py` — HD1:pty 下真实 CLI 自设 PIN 成功(退出 0)。
- `/tmp/delta19/poc_scnet_delta.py` — D1 token 0644 不收紧(LD1)、D2 work_root 逃逸(MD2)、
  D3 plan-hash 穿越(LD4)、D4 恢复覆盖新批准(LD2)、D5 dry_run 误标(LD3)。
- `/tmp/delta19/poc_validate_delta.py` — V1 inf timeout OverflowError 逃逸(MD1,nan 对照
  已闭合)、V2 agent 侧 fail-closed 成立注记、V3 isfinite 复核不可绕(负向验证)。

## 修复优先级建议

1. **HD1 立即**:人工设 PIN(运维一步) + pin_set 飞书告警 + PIN 最小强度上调;纯代码
   无法区分同 uid 人与 agent 的根信任自铸问题,报用户裁决是否引入带外因子。
2. MD1(OverflowError 一网打尽,两侧四处)、MD2(work_root 入白名单)——一行级修复。
3. LD1-LD7 随修;LD6 为口径注记。
