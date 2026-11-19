# SCNet 面独立审计报告(第 19 轮,audit_scnet)

- 审计对象:`scripts/scnet_client.py`(619 行,纯 stdlib);口径参考 `tests/test_scnet_client.py`(31 用例)。
- 裁决标准:`docs/对抗基线_ADVERSARIAL_BASELINE.md`(冻结版,不含 SCNet 面条目,故本面全部为"未覆盖行为"审查;R3 凭证红线适用)。
- 方法:只读审计 + /tmp 沙盒 PoC(`MDSIM_DATA_ROOT=/tmp/scnet-audit/data`,monkeypatch `_http`/`authenticate`/`_config`,urllib 零真实外发,零真实凭证)。同目录 s1-s4 实弹脚本结果已并入本报告并注明。
- PoC 产物:/tmp/scnet-audit/poc1_forge_approval.py、poc2_pty_child.py、poc3_binding_gap.py、poc4_double_spend.py、poc5_misc.py、poc6_upload_gate.py(均可独立复跑)。

## 结果总览

| 级别 | 数量 | 条目 |
|---|---|---|
| 高 | 2 | H1 伪造批准文件整体绕过人工闸;H2 TTY 闸被 pty 绕过,"agent 无 TTY"假设不成立 |
| 中 | 5 | M1 plan_hash 绑定不全(批准后漂移仍放行);M2 一次性批准并发双花;M3 delete/query job_id 注入;M4 upload/delete 全程无闸;M5 case_id 无字符白名单(远端路径穿越) |
| 低 | 12 | L1-L12 见下文 |

---

## 高危

### H1 一次性批准文件可被同 uid 进程伪造,人工确认闸整体失效

- 位置:`scripts/scnet_client.py:337-369`(`_approval_path`/`_valid_approval`/`_consume_approval`)、`:451-459`(submit 闸)、`:403-407`(approve 落盘)。
- 攻击场景:agent 与真人同用户运行,对数据根有写权限。`submit` 被拒时拒绝理由**直接打印 plan_hash**(`:457-458`);agent 据此向可预测路径 `<数据根>/state/scnet_approve/<ph>.json` 手写任意 JSON——`_valid_approval` 只校验文件能解析且 `approved_epoch` 距今 ≤1800s:
  1. 不校验文件内 `plan_hash` 字段与文件名一致(字段可整体缺失);
  2. 不校验文件内 `plan` 与本次提交参数一致(字段可整体缺失);
  3. 无 MAC/签名,批准文件与 `approve()` 之间无任何密码学绑定;
  4. `approved_epoch` 取未来值时 `time.time()-x` 为负 ≤1800,永久有效至时钟追上;
  5. `approve` 目录 775、批准文件 664(默认 umask,PoC 实测),同机其他用户可读可替换目录为 symlink。
- 证据:poc1_forge_approval.py——无 TTY、无人工,仅写 `{"approved_epoch": now}`(无 plan_hash、无 plan 字段)→ `submitted=True, approved_via='approval_file', job_id=8663185`。同目录 s2_forged_approval.py 独立复现(exit=1,`s2a_minimal_forged` FINDING,字段缺失/ts 伪造两变体均打穿)。
- 修复建议:
  1. `_valid_approval` 强校验:文件内 `plan_hash==文件名`、`plan` 与 submit 实时重算计划逐字段相等(含 remote_dir,见 M1);
  2. 引入仅人知的批准口令:`approve` 交互输入 PIN,批准文件落 `HMAC(K=sha256(PIN), plan_hash|epoch)`;PIN 哈希存凭证侧文件。同 uid agent 不知 PIN,无法伪造,也无法经 pty 喂给 `approve`(与 H2 联动);
  3. `_approval_path` 目录 chmod 700、文件 600(os.open 带 mode 落盘);`approved_epoch` 未来值一律拒(`0 <= now-epoch <= 1800`)。

### H2 TTY 内联确认被伪终端(pty)绕过,"agent 子进程无 TTY 无法自批"不成立

- 位置:`scripts/scnet_client.py:343-352`(`_tty_confirm`)、`:375`(`approve` 的 isatty 闸)。
- 攻击场景:闸的安全性依赖"`sys.stdin.isatty()` 为真 ⇒ 有真人在终端"。但任何无特权进程可用 `script(1)`、`python -c 'import pty; pty.spawn(...)'`、tmux 等分配伪终端,子进程 isatty() 即为 True;`script` 还把自身 stdin 转发进 pty,管道里的 `YES` 直接送达 `input()`。agent 一条 `printf 'YES\n' | script -qec 'submit ...' /dev/null` 即无人过闸。
- 证据:`printf 'YES\n' | script -qec "python3 /tmp/scnet-audit/poc2_pty_child.py" /dev/null` → 子进程 `stdin.isatty()=True`,`submitted=True, approved_via='tty', job_id=8663185`(mock 环境,零外发)。
- 修复建议:isatty 不构成人类证明,TTY 通道必须叠加仅人知因子——与 H1 共用批准 PIN(TTY 通道输入 YES 后再验 PIN,或干脆砍掉 TTY 通道只留 PIN 批准文件)。若不引 PIN,应在文档中把 TTY 通道降级标注为"防误触不防对抗",不能写进"代码强制人工确认"的安全声明。

---

## 中危

### M1 plan_hash 绑定不全:批准后 config/文件内容漂移,批准仍有效

- 位置:`scripts/scnet_client.py:329-334`(`plan_hash`)、`:427-445`(remote_dir/plan 组装)、`:355-362`(校验时不复核批准文件内 plan)。
- 攻击场景:plan_hash 只含 case_id/queue/nodes/ppn/walltime/cmd/文件**basename 列表**。以下均不进哈希:① `work_root`、`cluster_id`(来自 md_config,提交时实时读);② 由此推出的 `remote_dir`/区域;③ **文件内容本身**;④ `input_file` 全路径(仅 basename 经 cmd 间接入哈希)。攻击时序:agent dry-run(配置干净)→ 人跑 approve 看过 plan → agent 改 md_config 的 `work_root`/`cluster_id` 或替换同名文件内容 → submit 哈希不变、批准照用,作业与数据落到与批准时不同的目标。approve() 展示的 plan 来自 dry-run 审计记录,submit 用的是当前配置,两侧无一致性校验。
- 证据:poc3_binding_gap.py——dry-run remote_dir=`/public/home/user/YMY-AI/driftCase`,合法批准后将 `work_root` 改为 `../../attacker` 并改写 production.in 内容,plan_hash 不变,提交成功,实际 `GAP_WORK_DIR=/public/home/user/../../attacker/driftCase`。
- 修复建议:plan_hash 纳入 `cluster_id`、`work_root`、解析后的 `remote_dir`、`input_file` 及每个文件的 `size+mtime`(输入文件小,可直接 sha256);`_valid_approval` 通过时把批准文件内 plan 与实时 plan 逐字段比对,不等即拒。

### M2 一次性批准"校验-消费"不原子,并发双花

- 位置:`scripts/scnet_client.py:451`(`_valid_approval` 通过)→ `:494-495`(POST 成功后才 `_consume_approval`)。
- 攻击场景:一份批准在有效期内可被 N 个并发 submit 同时通过校验(消费发生在各自网络往返之后),"一次性"语义在并发下失效;中途失败不重批的设计与双花防护不可兼得,当前选择了后者都丢。
- 证据:poc4_double_spend.py——两线程共用一份批准,barrier 对齐在校验后销毁前,`submitted=[True,True]`,实际 POST 提交 2 次,批准文件最终才被删。
- 修复建议:闸口通过即刻 `os.rename` 批准文件为 `<ph>.consumed-<uuid>`(原子取走,仅一个线程成功);提交失败时按策略恢复(rename 回去)或强制重批——无论哪种都消灭双花窗口。

### M3 delete_job / query_jobs 的 job_id 无校验、不 URL 编码,query 参数污染

- 位置:`scripts/scnet_client.py:517-531`(`delete_job` 的 `info_map` 与 URL 裸拼)、`:510-513`(`query_jobs` 的 `strJobId=%s` 裸拼)。对照 `:254` mkdir 是用了 `urlencode` 的,口径不一。
- 攻击场景:`--job-id` 来自 CLI/未来 agent 调用,无任何字符白名单。`job_id="8663185&jobMethod=9"` → URL 出现第二个 `jobMethod`,服务端取首/取尾均不可控;`job_id="1;2,victim:99999:;x"` → `strJobInfoMap` 被拼入额外 `调度器,用户:作业:` 条目,一次 DELETE 波及他人/其他作业(真机档案同集群多用户)。空格、`#`、非 ASCII 进一步扭曲 query 解析。
- 证据:poc5_misc.py 5a——`delete_job("8663185&jobMethod=9")` 实得 URL `...jobs?jobMethod=5&strJobInfoMap=1697599991,user:8663185&jobMethod=9:;`;`delete_job("1;2,victim:99999:;x")` 实得 `strJobInfoMap=1697599991,user:1;2,victim:99999:;x:;`。
- 修复建议:job_id 入口白名单 `^[0-9_]+$`(兼容 slurm array,与 R7 SAFE_NAME_RE 同思路);拼 URL 一律 `urllib.parse.urlencode`/`quote`。

### M4 upload 与 delete 全程无闸:enabled=false 照传,外部写操作无人工确认

- 位置:`scripts/scnet_client.py:580-589`(upload 分支无 `gate_check`)、`:517-531`(delete 无闸,注释自述"不走人工批准")、`:253-279`(`upload_file` 本体也无任何校验)。
- 攻击场景:用户既定红线(见记忆/设计口径)是"花钱/外部写操作的确认用硬机制"。但:① `scnet.enabled=false` 时 submit 被拒而 upload 照常外发写远端存储;② `upload --remote-case` 无白名单无确认,且 remote_case 直接拼远端路径(见 M5);③ `delete_job` 一句话即可停掉在役生产作业(如真机 8663185),无 dry-run、无确认、无白名单。agent 幻觉/被诱导时,上传覆盖同名文件(`cover=cover` 固定,`:267`)或删作业都是不可远端撤销的写操作。
- 证据:poc6_upload_gate.py 6a——`enabled=false` 下 `gate_check` 拒 submit,但 `upload_file` 直接发出 `POST /efile/openapi/v2/file/upload`。
- 修复建议:upload/delete 至少纳入 `scnet.enabled` 总闸;upload 校验 remote_case 白名单字符(M5);delete 增加与 submit 同级别的人工确认(或最少 `--confirm JOB_ID` 双录 + 审计已有)。若用户拍板"delete 是幂等运维不要闸",需写进基线决策条目,不能只留在代码注释里。

### M5 case_id / remote_case 无字符白名单,远端路径穿越

- 位置:`scripts/scnet_client.py:418`(`job_name = case_id`)、`:440-441` 与 `:584-586`(`strip("/")` 拼接,不挡 `..` 与内部斜杠)。
- 攻击场景:R6 对 registry/pipeline_check 的 case_id 有 `^[\w.-]+$` 白名单,本面没有。`case_id="../../escape"` → remote_dir=`<home>/YMY-AI/../../escape`;`case_id=".."` → 直接指 home 根;含空格/`$(...)`/换行的 case_id 进 `GAP_JOB_NAME`、远端路径与审计。submit 路径有人工目检 plan 兜底(故不定高),upload 路径(M4)无任何兜底。
- 证据:poc6_upload_gate.py 6b——`../../escape`、`..`、`a/../../b`、`x y;$(id)` 全部原样进入 remote_dir。
- 修复建议:submit/upload 入口统一 SAFE_NAME_RE(同 R6/R7 口径,禁首字符 `-`、单独拒绝 `..`);拒绝时记审计。

---

## 低危

- L1 `gate_check` 只卡上限不卡下界:`nodes=-5`、`ppn=-32` 放行(`:301-302`),负数直达 `GAP_NNODE`/lmp_cmd 的 `{nproc}`。证据:poc5_misc.py 5b。修复:`1 <= nodes`、`1 <= ppn`。
- L2 `max_walltime_h` 可被 JSON `NaN` 字面量污染(Python json 默认接受),`hrs > NaN` 恒假 → 任意墙时穿闸(`:303-307`)。证据:poc5_misc.py 5c(9999h 放行)。修复:`math.isfinite` 钳制(与 14 轮 nan 修复同口径)。
- L3 缓存与 /center 错位时 `region_token` 抛裸 `KeyError`(`:232-233`):例如 config cluster_id=20027、缓存是旧账户只有区域 0/11112。证据:poc5_misc.py 5d。修复:缺区域抛 `ScnetError("config", ...)` 提示重认证。
- L4 token 缓存权限竞态:`authenticate` 先 `open(path,"w")`(umask 默认 644)再 `os.chmod(0o600)`(`:183-185`),窗口内 token 可被同机其他用户读。修复:`os.open(path, O_WRONLY|O_CREAT|O_TRUNC, 0o600)`。批准文件同病(见 H1 第 5 点)。
- L5 畸形响应鲁棒性三处:`authenticate` 的 `data` 若为 dict 迭代出 str → AttributeError(`:172`);`regions={}` 的缓存被当真 → `_platform_token` StopIteration(`:195`);`query_jobs` 的 `data` 若为 list → `.get` AttributeError(`:514`)。修复:类型检查 + ScnetError。
- L6 人工闸位置在 `authenticate()`/`get_center()` 之后(`:437-459`):无批准拒绝路径仍外发 2 次只读请求;离线环境下拒绝变成异常而非干净 refused。证据:同目录 s1_no_tty_submit.py `s1c_refusal_still_egresses` FINDING。修复:plan_hash/审计/人工闸前置到网络调用之前(dry_run 已如此,正式提交可同样先过闸再认证)。
- L7 multipart 文件名注入:`upload_file` 把 basename 未转义拼进 `Content-Disposition` 头部(`:270-272`),文件名含 `"`/CRLF 可扭曲表单结构。修复:拒绝或 percent-encode 非 SAFE 字符文件名。
- L8 submit 成功后 `_audit` 若写盘失败(磁盘满/权限),作业已提交却零审计且异常上抛(`:496`)。修复:审计写失败降级 stderr 告警不丢提交结果(与 E7 同思路),但审计缺失本身记进返回体。
- L9 `approve` 展示的 plan 来自审计日志(`:377-393`),审计文件同 uid 可写——单独不足以致骗(有 plan_hash 绑定 submit 参数),但与 M1 联动时可展示与实际不一致的 plan。修复:随 M1 的复核一并闭合。
- L10 `upload_file` 整文件读内存(`:264-265`),大 input 文件(GB 级 restart)OOM;注释已自述"面向输入文件",建议加显式大小上限(如 100MB)超限即拒并提示分片接口。
- L11 `_load_cached_tokens` 任一区域临期即整体重认证(`:151-154`):多区域账户下频繁全量续约,无安全问题,仅可用性注记,接受项候选。
- L12 `auth` CLI 掩码输出 token 前 20 字符(`:569`):JWT 前 20 字符落在 header 段(eyJ...),实测不泄 payload 敏感声明;注记接受,若 token 结构变化需复核。

---

## 已查无发现(逐面核销)

- **签名构造**(`:95-100`):字段字典序 accessKey<timestamp<user、无空格、HMAC-SHA256 小写 hex,测试 TestSign 两例钉死;timestamp 为 epoch 秒串,与官方示例一致。重放窗口在服务端,客户端无从验证,注记。
- **TLS**:`urllib.request.urlopen` 默认 `create_default_context`,全代码无 `unverified_context`/verify=False;凭证与 token 只走 https 常量 URL,URL 不接受 config 注入(hpc/efile URL 来自 /center 响应而非 md_config,SSRF 面不成立)。
- **凭证/token 泄漏(R3)**:`ScnetError` 只带 code+msg+url,不含 SK/token;`signature` 不落日志;审计 jsonl 字段为 plan/job_id/action,无敏感项;凭证解析只认平铺 `KEY: value`,不执行 YAML 标签。
- **过期批准拒绝**:s3_expired_approval.py 三变体(1801s/恰边界/未来非法串除外项已并入 H1)PASS,零提交。
- **直接参数篡改**:s4_param_tamper.py 对 nodes/queue/cmd/walltime/case_id/files 逐项改 → plan_hash 全变、提交全拒(注:这只覆盖"直接参数",config 漂移与文件内容见 M1)。
- **无 TTY 无批准拒绝**:s1b PASS,零提交、审计落 refused。
- **plan_hash 截断 16 hex**:64 bit 生日界 ~2^32 次尝试,且每次尝试对应一次真人 approve 流程,实际不可碰撞;真正的绑定缺陷是 M1 而非截断。
- **审计注入**:case_id/cmd 含换行/引号经 `json.dumps` 转义落 jsonl,不可注入伪行;`_audit` 目录自动创建。
- **闸门双开关/队列/上限**:TestGate 8 例覆盖,复核代码与测试口径一致;`queues` 空表=全放行是测试钉死的有意语义(test_empty_queue_whitelist_means_all),不报。
- **py3.11/编码**:无 3.12+ 语法;非 ASCII 文件名/路径经 utf-8 编码传输,功能面正常(L7 仅注入面)。

## 修复优先级建议

1. H1+H2 联动修(批准 PIN + 内容强校验 + 权限收紧)——这是"人工确认代码强制"声明的成立前提;
2. M1(plan_hash 绑定补全 + plan 复核)、M2(原子消费)——一次性批准的语义完整性;
3. M3/M4/M5(注入与无闸写操作)——对外写面收口;
4. L1-L8 随修,L9-L12 注记/接受项交用户裁决。
