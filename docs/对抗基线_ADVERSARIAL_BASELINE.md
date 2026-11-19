# 多轮对抗验证统一基线(ADVERSARIAL BASELINE)

> 本文档是多轮多智能体对抗验证的**唯一裁决标准**,2026-08-29 第五轮收尾时冻结。
> 起因:第 3、4 轮曾出现后轮推翻前轮已接受决策("3 改了 4 又改回来")的振荡。
>
> **规则**:
> 1. 下文"已定稿决策"后续轮次**不得翻案、不得重复报为发现**。
> 2. 唯一例外:某决策与更高优先级不变量直接冲突(安全红线 > 数据安全 > 功能正确 > 口径一致 > 性能)。
>    此时代理必须报"**翻案申请**"(引用决策编号 + 冲突的不变量 + 证据),由用户裁决,**不得自行改回**。
> 3. 新发现必须落在本文档未覆盖的行为上;对已覆盖行为只能验证"决策仍被正确执行"。
> 4. 每轮修复后更新文末"执行记录",文档本身随代码走。

---

## 一、安全红线(最高优先级,永不翻案)

- R1 `evolution/POLICY.json` 红线定义本体不动;apply_proposal 任何路径不得绕过红线闸。
- R2 `hpc_submit.py` 永远 dry_run 渲染,提交永远人工;`md_config.json` 的 `hpc.enabled` 默认 false,开启属人工决策。
- R3 凭证只落 `mdsim-data/dsh-home/.credentials.yaml`(chmod 600);代码目录/快照/发布件零凭证零数据件。
- R4 `/api/auto-approve` Origin 防线:`Origin: null`(file:// 打开)与同源放行,异源 403 且不得改动开关文件;ACAO `*` 仅为 file:// workbench 而开。
- R5 看板 `/pages/` 静态服务:编码穿越、伪装扩展名、`.py.html` 一律 400/404。
- R6 存储型 XSS:`case_id` 白名单 `^[\w.-]+$`(registry 与 pipeline_check 同口径)在入口拦截;渲染侧 index.html/simple.html 用 `esc()` 或 DOM API,禁止裸拼进 onclick/innerHTML。
- R7 HPC 采集器防注入(六轮修订):jobid/集群名走 SAFE_NAME_RE 白名单字符集(不含任何 shell 元字符、首字符禁 `-`,兼容 pbs `123.host` 与 slurm array `123_4` 合法格式)+ shlex.quote 纵深 + collect_one 白名单复核(paramiko exec_command 远端必经登录 shell,"数组式无 shell"不可实现,白名单+quote 等价满足防注入);ssh 失败静默降级"远端不可达",绝不影响本地条目。

## 二、已定稿决策(按面分组,编号供引用)

### 路径与数据根
- P1 数据根解析三级:`MDSIM_DATA_ROOT` env > `md_config.json["data_root"]` > 代码父目录 `/mdsim-data`;env 动态读不缓存(tests 靠 env 重定向)。
- P2 JS 侧(flow-gate index.js、wechat_push.mjs)与 Python `paths.py` 同语义:去空白、空串→null、`~` 展开、相对路径锚定**代码父目录**而非 cwd。env 缺失时 flow-gate 落 cwd 并告警——这是有意兜底,不是漂移病灶(run-dsh.sh 保证 env 恒存在)。
- P3 `pipeline/_atomic_write_json` tmp 名带 pid+uuid;snap_ 快照属可重建缓存,落 `mdsim-data/scratch/pipeline/`。
- P5 `DSH_HOME` 与 `MDSIM_DATA_ROOT` 过同款 normalizeEnvPath(wechat_push.mjs):~ 展开、相对锚定代码父目录、空白→null(六轮,paths 面低危修复)。
- P4 kit `config/env.sh` 必须 export `MDSIM_DATA_ROOT`(F5-R 接缝);orchestrator 注入失败时 `logger.warning` + monitor 结果带 `schedule_source` 字段。

### 温度程序(SCHEDULE)
- S1 `target_temp` 越界一律取 `sched[-1][3]`(注入短程序末段终温),web_monitor 与 monitor_agent 同口径——**已定稿,四轮 N1 结案**。
- S2 内置兜底分裂属**有意**:web 内置=升温平台程序,kit 内置=平台程序;仅当 md_config 损坏/缺失时出现,配置健康时经注入统一。kit 独立运行语义保留。——**已定稿(三轮 M3)**。
- S3 monitor_agent ≥8 列位置兜底保留历史 press/vol 错标(仅显示用途,不修正)——**已定稿(三轮 M2)**。

### 看板与注册表
- B1 看板数据源 = registry 优先 + 目录扫描兜底合并(按 prod_dir 去重);未注册旧作业经扫描仍可见是**兼容特性**。
- B2 存活判定按 `pid_source` 分派:proc-cwd(/proc 匹配)> pids-file(.pids 兜底)> registered(登记 pids);HPC 条目本地不做 /proc 探测。
- B3 注册表原子写 = flock + tmp + os.replace + 加载失败静默降级(仿 kit bus.py);registry 损坏不误杀扫描兜底。
- B4 HPC 条目看板显示"远端作业"徽标 + 调度器状态;log_tail 按 `log_format` 解析(7 列映射逐字对齐 board_feed.py:7-29)。
- B5 board_feed 常驻已退役;cpcm symlink 已删——不得复活双份数据源。
- B6 渲染侧凡 registry/远端/日志解析来源字段一律 esc()(case_id/scheduler_state/cluster/jobid/kinetic_stage/last_modified/monitor_log/system_log/recovery/strategy_log 全部 sink,test_workbench_api 静态断言钉死);入口白名单只管 API 写入,load 不复核,渲染必须兜底。

### 演化层
- E1 `apply_proposal` 两阶段:先备份全部 originals 再逐条 apply;`created` 去重(`dict.fromkeys`);同文件 create-then-modify 的红路径回滚=文件不残留,绿路径回滚=删除新建。——**五轮定稿**。
- E2 LEDGER 写侧伪造需要 ledger 写权限——**自二轮起接受**(能写 ledger 者已在信任边界内)。
- E3 `record_atom` 跨进程 fcntl flock;原子 id 强制 str;`observe --days` 钳位 ≥1;损坏 scenarios/snapshot 静默跳过不崩。
- E4 `POLICY.json` 留代码库(配置非数据);`backups_dir` 值为相对 memory_dir 的 `"backups"`,红线语义不动。
- E5 `record_atom` O(n·m) 全量扫描性能问题**缓议**(当前规模无感),不报。
- E6 apply **备份阶段**失败=未做任何改动,直接 fail 绝不可走 `_restore`(无备份条目会被当"新建"误删原文件,六轮高危修复);`_restore` 删文件后必须清理空父目录(六轮低危修复)。
- E7 test-results.txt 留证写入必须 try/except OSError——写崩不得阻断还原与入账,否则补丁已应用却零入账零还原(七轮中危修复)。
- E8 `draft_proposal` 同日去重禁 exists 预检:一律 try makedirs / except FileExistsError 返回 None(八轮低危,exists 与 makedirs 间竞态曾可打崩并发起草)。
- E9 `memory.rebuild` 的 SCENARIOS/SOP 全量写一律 `_atomic_write`(tmp 名带 pid+uuid + os.replace,与 P3 同口径)——崩溃/并发读者只见完整旧版或完整新版(八轮低危)。

### flow-gate / JS 插件
- F1 flow-gate 配置只许趋严(降级开关已在三轮焊死);cordis.patch.yml `logPath` 用相对值 `events/flowgate.jsonl`。
- F2 web profile bundles 规范清单 = `["@deepseek-ai/dsh-base","@deepseek-ai/dsh-web-app","dsh-web-search-pro","dsh-files","dsh-md-ops-gate","dsh-md-auto-approve","dsh-md-flow-gate"]`;**@vendor/dsh-browser 只作 dependency link,永不进 bundles**(web-search-pro 已插入 `id: browser` loader 行,双插必崩)——五轮 dsh web 启动事故定案。
- F3 tui profile(2026-08-30 终端模式):bundles 规范清单 = `["@deepseek-ai/dsh-base","deepseek-harness-tui","dsh-md-ops-gate","dsh-md-auto-approve","dsh-md-flow-gate"]`;deepseek-harness-tui 钉 0.3.0(唯一 peer 匹配 rc.6 的终端 bundle,omc/lk251066 要 rc.2/rc.8 不可用)。上游两缺口由启动器兜底,**不得靠改 persona 文本绕**:--model 缺省时 agent.options.model 不落致 {{model}} 装配崩;交互路径 preset 硬默认 "standard" 不读 settings。run-dsh.sh/ps1 对 tui 自动补 `--model`/`--preset`(解析 settings.yaml 的 agent-default-model 与 agent-presets.default,用户显式给的不覆盖,app 参数必须插在 --profile 之后)。

### kit / 部署
- K1 kit v3.4 `web_monitor.py` 是 LEGACY 副本(硬编码 SCHEDULE,会漂移);`deploy.sh` 指引指向在役 `dsh-agent/dashboard/web_monitor.py`。不得再给 v3.4 加功能。
- K2 register_job.py 调用点在 **launch_agent.py** 启动处(orchestrator 链),失败只 warn 不阻断——**kit 侧唯一新写点**(六轮措辞修订)。
- K2b launch 契约含可选 `project` 字段(三处 persona 副本同步);**两处接线缺一不可**:launch_agent.py 透传 `--project`(六轮)+ orchestrator._handle_launch 把 `task.get("project")` 组进 launch args(七轮补接线,kit test 钉死)——缺后者 kit 自治链登记仍落 default,与按 task 查 schedules 口径分裂。
- K3 monitor 结果带 `schedule_source: injected|builtin`,供口径审计。
- K4 setup.sh 收敛为:写 md_config.json **四字段**(local_base / ssh.remote_base / data_root / run_case_base,六轮文本修订——第 1-5 轮演练验证的既定口径)+ 凭证复制 + persona 内嵌路径改写。
- K5 nodeLinker 多重命中:改写首个、**删除其余**(全改写产生重复键,pnpm duplicated mapping key 且复跑不自愈,六轮 setup 面低危修复);无冒号裸行同样命中改写(五轮)。

### SCNet 超算提交(19 轮定稿)
- C1 人工闸 **PIN 制**:approve(TTY 打印计划→YES→PIN)与 TTY 内联提交同口径;PIN ≥8 位非纯数字,PBKDF2-100k 加盐存 `state/scnet_approve/pin.json`(0600),改 PIN 须验旧 PIN;**agent 不知 PIN,pty 管道无法自批**(HD1 残余:PIN 未设的引导窗口内同 uid 可自铸 PIN——收敛条件=人工已 set-pin + `gate_not_armed` 审计标记;运维立即闭环)。
- C2 plan_hash 全绑定:sha256{case_id, cluster_id(生效值), work_root, queue, nodes, ppn, walltime, cmd, files:[{name, sha256}]}[:16]——文件内容哈希防同名换内容;cluster/work_root 绑定防批准后配置漂移。
- C3 一次性批准原子语义:`_take_approval`=os.rename→`.taken-<uuid>`(并发只有一个赢家);提交成功删 taken,失败仅当目标不存在时恢复(**不覆盖更新的人工批准**);批准 30 分钟有效,schema 强校验(plan_hash 匹配+epoch 数值+0≤age≤1800)。
- C4 delete 不走人工闸(用户拍板),但要 enabled 总闸 + job_id 白名单(`^[0-9_]+$`,slurm 数组兼容)+ 审计(refused/deleted)。
- C5 ASCII 白名单 `^[A-Za-z0-9._-]+$`:case_id/remote_case/上传文件名/work_root 逐段(拒 `.`/`..`/前导 `-`/中文——`\w` 匹配 Unicode 教训);上传 ≤100MB。
- C6 配置畸形一律 fail-closed + 审计:enabled/auto_submit 须 `is True`、白名单须 dict、数值正有限(math.isfinite 防 JSON NaN);人工闸检查**先于网络**(拒绝不产生任何外呼);审计写崩降级 stderr 不阻断主流程(E7 同型)。
- C7 接受项:同 uid 手工伪造批准文件=E2 同型信任边界(有写权限即蓄意攻击);PIN 爆破属蓄意攻击不在威胁模型。

### VALIDATE 相位(kit v3.3,19 轮定稿)
- V1 热力学数值双保险:字面词表(nan/-nan/inf/-inf/infinity/-infinity)+ float 解析后逐值 `math.isfinite`——glibc 打 `-nan`、`float("1e999")` 静默溢出 inf,词表盖不全。
- V2 试运行只截**首个 run**(首词恰为 run 才截,run_style/注释不误判),report 带 `coverage` 声明覆盖范围;lmp 退出码非零仅 `warning` 提示人工复核不卡验收。
- V3 orchestrator 侧:validate_timeout 畸形(含 inf→OverflowError)兜底 300 + 钳位 [30,3600];`_run_agent` 异常→FAILED+飞书+bus FAILED(不得静默卡相位);验收不过落 FAILED **不放行生产**且 bus 不得永留 VALIDATING。
- V4 接受项:存量 task 默认 validate=True;task 文件路径属信任边界(同 uid 写权限)。

### L2 结构自进化·证据晋升(20 轮定稿)
- L2-1 证据门**类触发、无豁免**:`cls in evidence_classes` 即过证据门,与 tier 无关;approver 仅作审计记录,不豁免任何证据检查。
- L2-2 **门重执行**纪律:install 必过沙盒试跑且由门子进程重跑(考题出自模板代码);提案自附 sandbox_report.json 只作审计佐证,绝不作门输入(杀自证)。
- L2-3 **血统校验**:install/promote/demote 三操作均要求条目 `_LINEAGE_KEYS`(name/desc/for/canary/rule/template/params)与模板 instantiate 产物一致(success_rate/uses 合法漂移除外);off-template 一律拒。
- L2-4 **内容禁闭**:auto 类 `_check_auto_db_confinement`(strategies/history 逐字节不动、仅 strategy_adjustments 可动);evidence install 同纪律(单写者)。21 轮修订:**顶层键集合同样锁死**(auto 唯一豁免=创建缺失的 strategy_adjustments 键),比对用 `_canon` 规范化文本(True≠1,防 bool/int 混淆穿透)。
- L2-5 **TOCTOU 双闸**:门前内存模拟断言 + 落盘后按(备份, 盘上)重放断言,不过→ROLLED_BACK 非 REJECTED;promote 统计按当前盘新读 db 重算,不信 meta/evidence 携带数。
- L2-6 **demote 优先**:横扫与门两侧对称——连败尾≥demote_fail_streak 的 canary 只可走 demote;3W→3F 反转序列永不起草 promote。
- L2-7 **僵尸锚点**:零结账 canary 饿死计时锚=history 首账 ts 或 LEDGER 最早 install 记录 ts;LEDGER 行带 op/specialist_id;超 canary_max_age_days(30d) 饿死降级。
- L2-8 **墓碑 fail-closed**:墓碑 ts 解析失败/未来 ts 一律按冷却内处理;demote 配套 auto 类墓碑提案(penalty 封顶+tombstone),冷却 7d 防立即重起草。
- L2-9 **no-op 判失败**:apply 零改动=策略失败记账(防 Beta 后验投毒),备份延迟到有真实改动才建;测试/驱动每跑刷新 production.in fixture。
- L2-10 **DSL 规则安全**:白名单键/flags⊆{I,M,S,X}/count∈[1,20]/长度上限/嵌套量词(含 `{m,n}` 形态,21 轮补强)+交替量词+dotstar≤2 启发式/`\N`+`\g<>` 反引用全审计/version bool 拒/apply 异常包装 ValueError;规则只出自人工模板骨架——执行器/模板/沙盒列 forbidden_paths(通道永不可碰);沙盒网络封堵含 socket 层(21 轮)。
- L2-11 **canary 语义**:truthy 即 canary(畸形真值 fail-closed 按 canary 拦);两轮选择(非 canary 优先,尽没才看 canary;无 canary 库时行为与接入前逐字节等价);研究推荐 canary 一律压制回退打分;legacy 名+rule 拒、未知策略名显式 False。
- L2-12 **R6 时限**(2026-09-03 用户拍板):rollback 对含 `.v3.3_strategy_db.json` 且条目 ts>24h 拒整文件还原,引导正向修正提案;≤24h 维持现状;ts 解析失败/未来 ts(负年龄)fail-closed 同拒(21 轮)。
- L2-15 **接受项**(21 轮):① JSON 0.5/0.50 语义等价不可区分,不构成"逐字节"泄漏;② 沙盒越界写快照仅覆盖 tmp 树,绝对路径写出不可见——recover_agent 无绝对路径写面,tmp 树覆盖其全部合法写,属纵深防御残余。测试纪律:legacy 六策略黄金输出逐字节锚全覆盖。
- L2-13 **总开关**:POLICY.json 删除 evidence_classes 条目通道即刻全关;已装策略人工 demote;recover_agent.py 不进 forbidden(human-tier 修复通道保留)。
- L2-14 口径裁决:SCNet 面 s3/s6/s7/s9/s12/s13B3 系修复前旧口径脚本,19 轮起**以 r1–r10 为裁决**,直接复跑的失败属脚本口径陈旧假信号,非产品回退。
- L2-15(21 轮)沙盒写限制为接受项(绝对路径写未封,socket 已封)。
- L2-16 **蜂群红线(用户 2026-09-05 拍板)**:蜂群产物只能在模板注册表内做选择(模板 id+网格参数),证据门"条目==instantiate 产物"逐字节比对不松动;选择函数=蜂群输出×人工 match_terms/param_patterns 表的确定性映射,AI 判断咨询化可审计;全模板零命中=新机制→observe 转 human_review 提案,模板永远人工交付;蜂群任何失败→None,起草流程永不被阻塞(回退盲选,与接入前逐字节等价)。
- L2-17 **熔断纪律**:耗尽熔断只压恢复动作(PREEMPTIVE_RECOVER/RESEARCH/RECOVER 转移),**不压检测**(OBSERVE/ANALYZE/DETECT 照常,死进程判定照常);退避 30min×2^(strikes-1) 上限 6h,strikes>5→FAILED 转人工,修复成功即清零;`exhausted` 判定严格 `is True`(非布尔不跳闸)。
- L2-18 **bus 诚实性**:注册 pids 全死+monitor_status≠DONE → bus status "DEAD"+一次性飞书(`_dead_notified` 防重发,复活清旗);熔断停泊期间死跑同样如实显示 DEAD。
- L2-19 **recover 输入源贯通**:recover_agent 的 input_file/output_log 为可选参数(默认 production.in/production.stdout 逐字节锚定),basename 校验拒遍历;orchestrator 从 task 透传(adopt case 修复打对文件);output_log 非默认时 `>>` 追加保住被接管跑日志。
- L2-20 **should-run 纯咨询纪律**(24 轮定稿):orchestrator `_should_run()` 每 tick 输出 verdict(ACT/IDLE/WAIT_HUMAN/SUPPRESSED)+理由+证据,节流入策略账本(迁移即记,稳态 1/hr);**永不 block dispatch**——熔断与状态机逻辑原样不动,SUPPRESSED 时检测子相位照常(L2-17 不松)。dsh observe `_should_observe` 同纪律:间隔闸(POLICY `observe_gate.min_interval_minutes` 默认 30)+kit FAILED 且有 pending 用户闸等人工不空转;kit state 缺失放行、损坏 fail-closed 跳过;`--force` 人工越过;裁决行追加 events/pipeline.jsonl;闸任何异常不阻断主链。
- L2-21 **用户闸队列单写者**(24 轮定稿):`.v3.3_user_gates.json` 仅 kit orchestrator 写(enqueue/resolve/sweep_requests);dsh 侧(起草桥/批准回执)只向 append-only 收件箱 `logs/user_gate_requests.jsonl` 投请求行,orchestrator 每 tick 扫箱——(kind,ref) 精确去重入队、销项标记不删除、**rename 原子接管后批量消费**(无读-截断窗,单次≤200 行剩余回投,load/save 各一次,新闸聚合一条飞书);**收件箱是不可信源**:入队 source 强制 "dsh",收件箱 resolve 只许销 evolution_proposal(kit 原生闸只能走 kit 代码路径);队列损坏先隔离 `.corrupt.<ts>` 再回退,不得静默抹审计史;dsh/看板/推送对队列**只读**;已告警入队点不重复飞书;`_user_gates_enabled:false` 整关回退;批准仍只走 --approver 人工通道,桥不新增任何批准逻辑。
- L2-22 **实现期自查两条(24 轮复盘同类错立规,实现阶段自查+审计阶段必查)**:①**竞态窗**——凡"读文件→处理→写回/截断"序列,必须先答"另一进程在读写之间追加/修改怎么办";正解=rename 原子接管、tmp+replace、或锁,禁止裸 readlines→truncate(前科:15 轮 E8 起草竞态、24 轮收件箱读-截断)。②**禁隐式 fail-open**——读外部文件/状态失败(损坏/瞬断/缺席)必须显式三选一:fail-closed 跳过、沿用上一轮已知值、或确认"空值即安全"并注释理由;禁止静默回退零值/空表继续走(前科:24 轮 push known_gates 清基线假爆发、observe 闸 FAILED 态闸损坏放行)。

### 运维规则(代理执行纪律)
- O1 禁 `pkill -f`;一律 `ps -p` 核实 + 显式 pid kill + `setsid nohup` 起服。
- O2 数据迁移/清理:复制优先、删除最后;删除在役服务相关文件前必须确认。
- O3 在役端口:web_monitor :8080、dsh web :3091;重启必须带 `MDSIM_DATA_ROOT`/`DSH_HOME` env。
- O4 `~/.dsh` 2026-08-19 迁移前残留(sessions/profiles/headless/node_modules)已识别,清理由用户确认后执行,不得擅删。

## 三、回归门(每轮必须全绿)

| 套件 | 命令 | 基线 |
|---|---|---|
| dsh-agent python | `MDSIM_KIT_BASE=<kit> python3.11 -m unittest discover -s tests` | 423 通过(v1.5.14 封版实测) |
| node 全部 | `node --test tests/test_flow_gate.mjs tests/test_wechat_push.mjs` | 30 通过(v1.5.14 封版实测) |
| kit python | `python3.11 -m unittest discover -s tests`(kit 根) | 244 通过(交付快照实测;生产树 2026-09-13 已补 ADOPT_QUEUE 抬常量,与快照等价) |

任何一轮修复后三门必须全绿;新增发现修复必须带回归测试(复现→红→修→绿)。

## 四、执行记录

| 轮次 | 日期 | 结果 |
|---|---|---|
| 1 | 2026-08-26 | 高危 3(看板 500/nan 白屏/错列)+ 中危若干,全修 |
| 2 | 2026-08-27 | 致命:HPC 注入/路径逃逸/循环连坐/apply 红线绕过,全修;接受 E2 |
| 3 | 2026-08-27 | 接受 S2/M3、S3/M2;flow-gate 降级焊死;record_atom 加锁 |
| 4 | 2026-08-28 | 统一 SCHEDULE 配置驱动;N1 口径定稿 S1;接受 jobid=0 边界 |
| 5 | 2026-08-29 | 中 4(XSS/JS 数据根语义/schedule 接缝/apply 同文件双条)+ 低 8,全修;F2 bundles 事故定案;**本文档冻结** |
| 6 | 2026-08-29 | 首轮按冻结基线复审:8 面修复验证全过;新发现 高1(apply 备份阶段误删原文件→E6)+ 中1(project 接缝→K2b)+ 低5(渲染 esc×2→B6、DSH_HOME 归一化→P5、nodeLinker 重复键→K5、空目录残留→E6),全修;翻案裁决 2 起均为基线文本修订(R7 白名单+quote 等价防注入、K4 四字段既定口径);回归门刷新 115/24/48 全绿 |
| 7 | 2026-08-29 | 六轮修复复审:5 面(e2e/HPC/paths/setup/管线)零新发现;新发现 中1(test-results 写崩留补丁零入账→E7)+ 低2(B6 遗漏 sink:last_modified/日志流组,全 esc 并由静态断言钉死)+ MD-1 接线漏 orchestrator._handle_launch(→K2b 补全),全修;回归门刷新 117/24(=flow-gate 21+wechat_push 3)/50 全绿 |
| 8 | 2026-08-29 | 子代理群撞 API 5h 配额 403 全灭,**由主会话代行终扫**:三门回归 117/24/50 全绿;穷举 index.html/simple.html 全部 innerHTML sink,补 esc 8 处(agent 卡 name/status/error、恢复历史 strategy、物种 s.name、步骤名/状态/时间、队列 statusText/trigger、执行链 label/statusText/step/detailStr/errStr、模态框根因策略、daemon PID),静态断言扩至 19 个裸 sink 模式;web_monitor 带 env 重启服务八轮静态。配额恢复后**群审补扫**:5 面(e2e/HPC/paths/main-loop/setup/pipeline)零新发现;新发现 低3——注册表面 agent 详情模态框整块裸拼 9 处(data.phase/monitor_subphase、m.status/step/temp/energy/risk_score、phase、agent.status/step/pids/error,全 esc→B6,静态断言扩至 30 模式),演化面 draft_proposal 去重竞态(→E8)与 rebuild 非原子写(→E9),全修;回归门刷新 119/24/50 全绿;web_monitor 带 env 重启服务模态框修复 |
| 9 | 2026-08-29 | 八轮修复复审:**8/8 面全部零新发现,收敛达成**。注册表面模态框 9 处 esc 逐字核对通过、静态断言恰 30 模式、32 处 sink 穷举无遗漏(B6 闭环);演化面 E8 实证 16 线程并发恰 1 起草、E9 压测读者零坏 JSON;paths/setup/HPC/主循环/管线/e2e 六面复核全部已定稿决策仍正确执行、八轮改动零波及;回归门 119/24/50 全绿(多面独立复跑一致);在役 web_monitor :8080(pid 3948001,env 合规)/ dsh web :3091 正常。**多轮对抗验证至此收敛** |
| 10 | 2026-08-30 | **kit 演化层接入(真学习)一轮**:B1-B7(损坏 db 打崩观测/refs 缺 case_id 去重吞并/slug `../` 目录逃逸/同策略异根因互踩去重位/非规范化落盘/手改 db 文件序乱连败失真)+ C1/C3(起草烧掉同日去重位),全修带回归 |
| 11 | 2026-08-30 | 二轮:py3.6 无 str.isascii() 致 slug 净化静默跳过→charset 白名单;E2E 实证 ensure_ascii 失配令中文行全入 diff 超 12 行门被 REJECTED;并发压测 4796 写 + 212 读零错,全修 |
| 12 | 2026-08-30 | 三轮 delta 复审:零新发现,初判收敛 |
| 13 | 2026-08-31 | 四轮全流程 fresh-eyes:高2——H1 根因漂移假成功(→统一规则:RESEARCH/FAILED 时 case 6h 内年轻 pending 不论因同判 fail)、H2 同日未应用草稿 diff 上下文过期卡死(→未应用即刷新,已应用绝不动);中2——M1 压制只进不退且无时间窗(→连胜≥2 减压 -0.15 退出通道 + 14d 连败窗)、M2 penalty 泄漏(research 推荐绕过压制、全压时字母序兜底架空学习→压制贯通 + 最高分兜底),全修带回归;E2E 全链(注故障→结账→后验降→起草→应用→压制→回滚)绿 |
| 14 | 2026-09-01 | 五轮双代理:代码审计 0 高 0 中,低3——nan/inf 穿透 `max(,0)` 产"标题上调实为删条目"误标 diff(两侧 `math.isfinite` 钳 0)、kit `_penalty` 容器级畸形 AttributeError(→isinstance,与 observe 侧对齐)、未来 ts 条目仍记 atoms(接受:事实留痕不驱动决策);实弹注射 6/6 通过(未来 ts 零起草/bool 不假减压/负值钳 0 起草 0.15/垃圾条目不崩/1h 容差边界精确/kit 同口径);生产只读健康 health=0.333 无污染;七红线 R1-R7 零冲突;回归门刷新 143/24/76 全绿。**本特性多轮对抗收敛** |
| 15 | 2026-09-02 | **cron 上线后六轮(训练驱动+攻击+哨兵三代理)**:E2E 驱动 3 循环 396/396 断言全过(连败加压 0.15→0.30→连胜减压→摘条目全链,Beta 后验逐步复算一致);攻击 7 项打穿 中2 低4——settle_pending 漏捕 TypeError 整批结账中止(→补捕)、rollback 抹账后当日失语(归为设计意图:人工回滚后同日再压等于对抗人工,次日重起草即冷静期)、history 侧 TOCTOU(→`_strategy_db_diff` 落笔前按新快照复核尾部连胜/连败,提取 `_recent_outcomes`/`_tail_streak` 统一口径)、位置式驱逐乱序丢最新账(→ts 感知驱逐)、长名 slug ENAMETOOLONG 永久失声(→截断+哈希)、load_db 漏兜 RecursionError(→统一 ValueError);修复引入的 sort 键回归被 E2E 当场抓回(复现→红→修→绿);哨兵:cron 实触发验证、轮转干跑、生产零污染、七红线零冲突;回归门刷新 145/24/79 全绿 |
| 16 | 2026-09-02 | 七轮(收官双代理复审六轮 delta):复攻 7 项全过无回退;交叉命中 中2 低1——record_attempt ts 排序遇非 dict 条目 AttributeError+未来 ts 永占槽位(→驱逐键映射脏/未来条目为 "" 最先逐)、证据反转后旧草稿仍可被 apply 误应用(→`_invalidate_unapplied_draft` 清空未应用 stale diff + 收尾横扫,空 diff 在 apply 端 fail-closed REJECTED 入 LEDGER,已应用绝不动)、`_recompute_rates` 文件序口径(→ts 排序与 observe 统一);settle/_recompute 补 isinstance 条目防护;回归门刷新 147/24/81 全绿。范围外提请:apply_proposal.py:347 `capture_output` 系 3.7+ API,apply 为人工操作须用 python3.11 运行(文件冻结未动) |
| 17 | 2026-09-02 | 八轮(双代理复审七轮 delta):审计 低3——`_evict_key` 未来判定无容差,时钟回拨后合法账被当先逐(→1h 容差与 observe 同口径)、`_recompute_rates` 坏 ts/未来条目混入后验窗(→逐条 strptime+1h 容差过滤,与 `_recent_outcomes` 同构)、observe_kit 早退路径不横扫(证据不可确认时 stale diff 多活一天→四个 early return 均以空 touched 调 `_sweep_stale_drafts` fail-closed);攻击复攻 7 项全过,新低1——同日方向翻转只刷新 diff 不刷新标题(→刷新分支同步重写 proposal.md 首行);全修带回归,门 149/24/83 全绿 |
| 18 | 2026-09-02 | 九轮(双代理复审八轮 delta+复攻):复攻 r8 六项全过零回退;审计 低1 可修——刷新分支漏捕 UnicodeDecodeError 逃逸致横扫被跳过(→捕 ValueError);攻击新中2——`_evict_key` 容差内未来条目排尾受保护挤掉真实新账(→钳键到 now,now 与新账同次取时消除秒翻转边)、`_slug_safe` 白名单留分隔符致 (A,B-C)/(A-B,C) 同 slug 互覆盖失声(→`_pair_slug` 附原始对哈希尾巴);全修带回归;复跑 r8+r9 全量 11 脚本除 b2 外全过——b2(容差内未来 fail 压低后验+幻影连败)判**接受项**:前提需 db 写权限,与"未来 ts 仍记 atoms"同类威胁模型;低实录接受:标题用原始名不截断(展示层)、方向翻转不刷新 evidence.md(审计注记)、并发互扫瞬态(下轮自愈);回归门刷新 **151/24/84 全绿,收敛达成** |
| 19 | 2026-09-03 | **v1.4.0 新增两面(SCNet 提交通道 + kit VALIDATE 相位)五代理两轮**:首轮 高3(F1 伪造批准自批/kit H1 -nan 穿透)+ 中8(F2 批准双花/F3 内容调包/F4 区域挪用/F5 case_id 逃逸/F6 类型混淆 fail-open/kit M1 卡相位/M2 1e999 溢出)+ 低若干,全修——PIN 制人工闸(用户拍板,C1)、plan_hash 全绑定(C2)、原子一次性批准(C3)、ASCII 白名单(C5)、配置 fail-closed(C6)、isfinite 双保险(V1)、timeout 兜底+异常 FAILED(V3);二轮 delta 复审 HD1(PIN bootstrap 窗口,代码加固+运维闭环=人工 set-pin)+ MD1/MD2 + LD1-LD7,全修;delete_job 不加闸只加校验+审计(用户拍板,C4);接受项 C7/V4(同 uid 伪造=E2 同型);真机冒烟提交 8663185 全链审计,mkdir query 参数怪癖真机修复;收官复攻 10 项修复全验有效零新发现零回退(复跑 r1-r10+s1/s2/s10/s11 全绿);回归门刷新 **207/24/128 全绿,本轮收敛** |
| 20 | 2026-09-03 | **L2 结构自进化·证据晋升流水线(新特性,四代理一轮)**:高4——H1 auto 类内容闸旁路走私 strategies/history(→`_check_auto_db_confinement`)、H2 simulate/apply TOCTOU(→落盘后重放断言,不过 ROLLED_BACK)、H3 promote 遮蔽 demote(→demote 优先+门拒连败中 promote)、H4 `\g<>` 反引用未审计 apply 才炸(→反引用全审计+异常包装);中8——血统不校验(→`_lineage_ok` 三操作对称)、human-tier 旁路证据门(→类触发无豁免)、sid 同日撞号(→allocated 占用)、install 不再必要(→`_install_still_needed` 重算耗尽)、canary 判定脆弱(→truthy fail-closed+`_pick` 畸形防护)、legacy 携 rule(→拒+未知名显式 False)、僵尸 canary 永生(→LEDGER install ts 锚+30d 饿死)、墓碑解析 fail-open(→fail-closed);低4 全修;kit 连带 no-op 判失败(防 Beta 投毒)/ReDoS 加严/version bool 拒;全修带回归(复现→红→修→绿);E2E 双环 34/34、0901 driver 396/396、a8/a11/na-nf/round5/8/9 复跑全过、SCNet r1-r11+s 系列复跑全过;s6/s7/s9 裁环境性失败(19 轮已定案以 r1-r10 为裁决,L2-14);新定稿 L2-1~L2-14;回归门刷新 **274/24/173 全绿,本轮收敛** |
| 21 | 2026-09-03 | **L2 追审(用户指令复验,三代理:kit 审计+dsh 审计+55 脚本全量复攻)**:复攻 52/55 零回退(3 已知接受=s6/s7/s9);新发现 中2 低4——21-F1 ReDoS 启发式漏 `{m,n}` 嵌套量词(→扩展+合法花括号不受影响实证)、21-F2 禁闭顶层键走私(→顶层键集合锁死,auto 唯一豁免=创建缺失的 strategy_adjustments)、21-F3 True==1 穿透逐字节(→`_canon` 规范化文本比对)、21-F4 rollback 负年龄绕过(→fail-closed)、21-F5 沙盒 socket 未封(→封堵,绝对路径写限制记接受项 L2-15)、21-F6 legacy 四策略无黄金锚(→六支全覆盖);全修带回归;打包冒烟另抓到全新部署图谱策略节点缺失(→`_kit_default_db` 回退 kit load_db 默认六策略,预存于 v1.4.0 包,非回退);新定稿 L2-4/L2-10/L2-12 修订+L2-15;回归门刷新 **279/24/178 全绿,本轮收敛** |
| 22 | 2026-09-04 | **外部运行接管 + 野跑检测器(新特性,用户指令触发,主会话实现并自对抗)**:背景=会话侧经用户授权 bash 直启 LAMMPS 游离状态机外(看板/自愈全盲),用户拍板「审计门可经指令绕过,托管不可跳过」;adopt 路径(`adopt:true` 注入 → IDLE 直转 MONITOR,/proc 自动发现存活进程,log_file 显式指定防陈旧 production.stdout 顶包)fail-closed 四拒(目录缺席/log_file 穿越/指定日志缺失/无活进程),非 adopt 任务行为逐字节不变锚定;野跑检测器(代码强制:每 tick 挂钩 300s 节流扫 /proc,cwd 不在管即飞书+策略账本+state 留痕,只在新增目录告警防轰炸,DONE 相位旧目录残留同判),检测强制、接管人工(不自动接管:外部运行 total_steps/温度程序未知);测试 14 新增(含真 /proc 集成、扫描器桩隔离——真机上有真实运行在跑,测试不得依赖 /proc 干净);活机实证 gentle_compress_full_stage1 收编后监控/尖峰识别/预防性修复链(策略耗尽优雅放弃)全链生效、在管零误报;回归门刷新 **279/24/192 全绿** |
| 23 | 2026-09-05 | **耗尽熔断 + 蜂群研究驱动 L2 演化 + 三条工程修复(live 事故驱动,用户拍板蜂群红线:模板空间内选择)**:live 暴露——orchestrator 对死跑每 ~2.5min 空转一轮耗尽、recover 硬编码 production.in(adopt case 修复打错文件)、bus 不反映 PID 死亡;修复=recover 输入源贯通(input_file/output_log 可选参,basename 校验拒遍历,`>` 默认行为逐字节锚)+耗尽熔断(state recovery_breaker,strikes 退避 30min×2^(n-1) 上限 6h,`exhausted is True` 严格布尔,只压恢复不压检测,strikes>5→FAILED 转人工,成功清零)+bus DEAD 裁决(注册 pids 全死+非 DONE→DEAD+单次飞书);蜂群=策略库耗尽触发 mcp/model_research repair 流水线(联网收集→量规→代码终裁),产物**确定性映射到模板注册表**(match_terms 计分 verified×2 + param_patterns 正则→网格值),全零命中=新机制→swarm-research human_review 提案,低置信回退首参(与盲选逐字节等价锚);红线零松动:apply_proposal 证据门 instantiate==entry 逐字节原样(测试证 picked 过真门 AUTO_APPLIED/off-grid 9.9e-9 越狱 REJECTED);知识库 rsi_knowledge/repair_kb.jsonl 追加式剧集(swarm_research+canary_outcome)回灌后续蜂群 question;节流双闸(同因冷却 24h/全局日 4 次,KB 不可读 fail-closed 不跑);蜂群永不抛,任何失败回退盲选;测试 37 新增(kit 21:输入源/遍历拒绝/exhausted 旗标/熔断退避表/非布尔 exhausted 不跳闸/DECIDE 压制/死+熔断停泊/bus DEAD;dsh 16:模板计分/参数命中/零命中回退/新机制 human 草稿/节流/KB 坏行容忍/picked==instantiate 锚/observe 集成蜂群失败逐字节等价);live 实证 adopt 注入 gentle_compress_tol1e6(log.tol1e6/production_tol1e6.in),空转消失、监控盯对日志;新定稿 L2-16~L2-19;回归门刷新 **295/24/213 全绿,本轮收敛** |
| 23-补 | 2026-09-05 | **pipeline 观测源升级事件流(跑量前硬约束,用户指令)**:覆盖式 snap 快照会把失效冲掉(case CRITICAL→恢复后记录消失,PIPELINE_ERROR 甚至从不落盘),复现文献跑量前必须修;pipeline_check 单出口封装 `_run_impl`+`_emit_event`——每次调用终态(含 PIPELINE_ERROR/NO_LOG)追加 `events/pipeline.jsonl`,事件写失败绝不阻断巡检;snap 快照保留(md_mcp 修复研讨读"每 case 最新"语义不动);observe 第 2 观测源改读事件流,distinct case 面状语义不变(同 case 窗内反复计一例),旧 `mcp/_pipeline` 快照留迁移期只读兜底;测试 7 新增(成功/PIPELINE_ERROR/NO_LOG 入账、写失败不阻断、覆盖后 CRITICAL 仍计入锚、distinct 口径锚、窗口过期、stdio 端到端);顺带抓获并焊死**测试污染生产 KB 洞**(TestObserveKit/Specialist/E2E 未沙盒 specialist_swarm.KB_PATH,每轮门漏 ~12 条假 canary_outcome 进真 repair_kb.jsonl,会回灌蜂群 question 误导研判→四个测试类补 KB 沙盒,污染剧集备份后清空 KB,门全量跑零泄漏实证);回归门刷新 **302/24/213 全绿** |
| 24 | 2026-09-07 | **用户闸队列+should-run 纯咨询裁决+observe 闸+提案桥+sim_review_packet(借鉴 LoopX 三形状,用户拍板只借鉴设计不引组件;三代理对抗:kit 审计+dsh 审计+基线一致性)**:特性实现后审计 高4 中6 低8 全修带回归——高:收件箱读-截断竞态吞 dsh 请求行(→rename 原子接管,窗口结构性消除)、收件箱 resolve 跨类自批准 kit 原生闸+source 伪造(→收件箱只许销 evolution_proposal、来源强制 dsh)、推送驱动闸文件读失败清 known_gates 基线致假爆发(→读失败沿用旧基线);中:adopt/野跑 ref 路径口径不一致(→realpath 全链归一+resolve 限 kind)、ref=None 通配吞队(→精确元组去重)、请求风暴 O(n²)+逐条飞书(→单次≤200 行剩余回投+批量 load/save+聚合一条飞书)、损坏队列静默抹审计史(→隔离 .corrupt.<ts> 再回退)、observe 闸 FAILED 态闸损坏 fail-open(→fail-closed kit_gates_unreadable)、崩溃白占节流窗(→成功后盖章+FAIL 裁决事件+原子写);低 8 全修(evidence_refs 限长/pending 计数 mtime 缓存/损坏告警防静默 IDLE/resolve kind 过滤/负 n 反转钳 [1,1000]/事件 ts 数值化/int(0.5) 截断→float/auto tier 不投 resolve 桥);复攻 9+5 脚本:真修全验有效,残报 5 条均为旧契约假阳性(逐条判读留档);红线零冲突:verdict 永不拦派发、熔断只压恢复(L2-17)、证据门逐字节不松、--approver 人工通道无新桥;测试 49 新增(kit 23:队列 15/verdict 8;dsh 26:observe 闸 12/闸桥 5/review_packet 8/push 1——含本轮修复回归 21);活机实证投请求→入队 ug-20260907-1→WAIT_HUMAN→resolve→IDLE 全链;新定稿 L2-20/L2-21,复盘同类错立规 L2-22(竞态窗自查+禁隐式 fail-open);回归门刷新 **328/24/236 全绿,本轮收敛** |
| 24-补 | 2026-09-07 | **看板 adopt case 详情白屏(live 用户报告驱动)**:adopt 接管 case 目录名≠case_id(tol1e6_cont vs gentle_compress_6000_20260630_070000),`scan_tasks` 按目录名建索引 → `/api/status?task=<case_id>` 404 → 前端 `d.step.toLocaleString()` 抛异常整屏留白;修=`resolve_prod_dir` 兜底 state task prod_dir(case_id 精确匹配才兜,真不存在仍 404)+`is_current` 按 realpath(prod_dir) 认亲贴"当前管理"徽标+前端 404 守卫显式报"任务不存在"不留白屏;**kit v3.3 与 dsh-agent/dashboard 两副本同修**(live :8080 跑的是 dashboard 副本,只改 kit 副本用户侧无效);活机实证 adopt case 详情 200 有数+徽标贴对,web_monitor 带 env 重启;测试 4 新增(兜底命中/未知 case 仍 404/is_current prod_dir 认亲/前端守卫源码锚);回归门 kit 刷新 **240 全绿** |

第 19 轮(SCNet + VALIDATE)归档:`docs/对抗验证_20260903_v1.4_SCNet与VALIDATE/`(验证报告 + 双审计 + 攻击 s1-s13 + 复审 r1-r10 + 复攻记录);第 20 轮(L2 证据晋升)归档:`docs/对抗验证_20260903_L2证据晋升/`(验证报告 + E2E driver + a8/a11 攻击脚本);第 10-14 轮(kit 演化层接入)归档:`docs/对抗验证_20260901_kit演化层接入/`(验证报告 + 五轮实弹注射脚本);第 15-18 轮产物同目录(round5_injection/driver+a1-a7/na-nf/round8/round9 脚本)。
