# dsh-agent v1.6.7+20260920 发布说明

全链分子模拟智能体(LAMMPS/ReaxFF + GROMACS)—— dsh CLI 套壳版。

**许可证**:**PolyForm Noncommercial 1.0.0**(见 LICENSE)——开源可见、可修改、可非商用(个人/学术/教育机构),**禁止商业使用**,商用须另行获得版权人(AI4Science MD Team)书面授权。第三方组件声明见 NOTICE。

## 本版变化(相对 v1.6.6+20260919)

**进化图谱 L1-L3 可视化 + 红区批准唤醒闭环(双闸一处点击) + LMP 路径接缝除根**

- **图谱融合/断裂/L1-L3(web_monitor.py build_graph + pages/graph.html)**:atom 节点带 level(L1 摩擦观测/L2 经验/L3 蒸馏核)与 dead_ts(superseded_at 被吸收时刻);融合边(旧原子→蒸馏核)88 对带出生时间戳;考场节点接入 replay_scores.json(result=success/fail/pending)。前端三态节点模型:unborn(ts 晚于时间轴切点)/ghost(被融合残影,玫瑰虚线 0.3 透明)/alive;普通边在 ghost 端点断裂,融合/蒸馏/赴考金边长存;L1→L2→L3 管线面板分层开关,时间轴回放进化过程。改字段名前后端两侧必须同步
- **批准唤醒闭环(web_monitor.py + dsh-md-ops-gate)**:看板批红区卡只落 decision 文件,dsh 里的 PA 已结束回合没人接——用户还得回会话手喊。现在 `_gate_wake` 后台线程经 dsh web RPC(127.0.0.1:3091 `POST /api/session.prompt`,线协议 `{type:'client-request',rpcId,method,payload}`)把「已批准,请原样重发」注入最近主会话;PA 重发时 ops-gate 先查看板 `/api/exec_gate_lookup?cmd=`(该 cmd 有人工批准且未消费的卡→直接放行去 kit 哈希闸;cmd 被篡改/看板不可达→照旧弹窗,失败关闭)。红区操作从三处点击降为看板一处,两发实弹验证(RUN1500 862e 卡/RUN2500 661bae 卡全自动闭环)。开关:`MD_GATE_WAKE=0` 关唤醒
- **workbench.html 新卡告警**:红区闸门 pending 新到(gateSeen 去重,首载不响)桌面通知+880Hz 蜂鸣——卡不出现=批不了,配套闭环
- **LMP 路径接缝除根(kit 侧,备份 .bak_lmpseam_20260919)**:agent_deploy_kit config/env.sh 第4行与 agents/runtime/v3.3/launch_agent.py 默认值均改 `/usr/local/md-bin/lmp_mpi`——09-12 权限墙搬 binaries 后旧默认路径成死值,直发 ENOENT 把 PA 逼去 /tmp 包装脚本(结构性红区)。PA 直调 launch_agent 从此走绿区零审批
- **dsh-goal-sync 插件(GoalBar 根治)**:goal round 续行 session-start 即 disarm(设计如此,不持久化),进度条唯一更新通道是 update_goal/ctx.goals.edit。插件 120s 定时+turn/end 触发,读看板 /api/status(HTTP 是 kit root:mdorch 文件外唯一合规读路),slots 配置驱动渲染(case 匹配→🔄/DONE→✅/前后→✅/⬜),内容不变不 edit+90s 节流+STALE_REVISION 重试;沙箱 13/13,已挂 web profile
- **实弹背景**:RUN2000 收口后 equil_2x(240k 步)完成,data 分发 RUN1500/RUN2500 双目录,两生产段各 30 核起跑(首个全闭环双发);编排器 state 收养在途

## 历史版本

<details><summary>v1.6.6+20260919</summary>

## 本版变化(相对 v1.6.5+20260919)

**自进化环 ②回放裁判落地(L3 考场) + 快照知识库随行规矩**

- **replay_eval.py(evolution/,纯代码零 LLM)**:考题=kit strategy_db history 带结局事件(只收 success/fail,时序 80/20 切 seen/holdout);考生=(根因,策略)对,判分 hit_rate(对真实成功的覆盖)/fp_rate(出手未成比例);exam_ready 原子 refs 逐一解析回 history 行做第二道存在性核验;模板考生从未真跑→untested 只登记(无真实结局不成考题);成绩册 evolution/replay_scores.json 独立文件原子写,绝不碰 atoms/strategy_db 本体
- **首考真相**:80 事件全收,41 原子 refs 核验过/0 伪造/263 非本考场题型;11 考生无一毕业——留出集尾部是 extreme_temp/general_instability 的 checkpoint_restart 连败(误报 1.0),修复工具箱对这两类根因真实亏空,给 #85 毕业闸一个诚实基线
- **测试**:tests/test_replay_eval.py 13 项全绿(伪造 ref 剔除/时序留出集/输入只读);test_tagging 21 项回归无破坏
- **快照知识库规矩(用户钉死)**:迁移新版本必须带 knowledge_base/ 当日实体拷贝 + kit 策略库,只放 mdsim-data 软链不算数;v1.6.5 快照已按此重铺

</details>

<details><summary>v1.6.5+20260919</summary>

## 本版变化(相对 v1.6.4+20260919)

**AI 验收双层测试体系落地 + 探测七修复(F1-F7) + 「绝不野跑」铁律编码 + RUN1500 重跑 stage 1 上轨全链验证**

- **双层测试体系**(`AI+MD-NEW/tests/md_agent_suite/`):确定性套件 36 项检查四面(编排器+守护+看板 / 启动纪律+安全 / web UI+插件 / 算例材料+实况),stdlib-only python3.11,exit code=FAIL 数,cron 06:23 日跑;LLM 探测层(probe/BRIEF.md 章程)以红队视角枚举套件未编码的面。首轮探测即斩获 7 项:
- **F1/F2 裁决器提权(kit claude_interface.py)**:sudoers 放行 `claude_interface.py *` 但 zoning 只做 basename 比对 → 伪造 `/tmp/analyze_agent.py` 即以 mdorch 执行;修复=realpath 必须等于 runtime 真身,另 `find -exec/-delete/-fprint` 等 7 旗标升红。回归 `adjudicator_hardened` 5 态钉死
- **F3 看板泄漏链**:访问日志原样记 query(token 落盘 318MB/1040 行)→ 剥 query + 600 + 50MB 轮换;token 全链轮换(旧 token 401 死透,新 token 只存 `/etc/mdsim/md-board.env` 640 + 插件配置 640);看板从野跑 nohup 收编 `md-board.service`(EnvironmentFile 喂 token,与编排器/守护同一 systemctl 纪律)
- **F4/F5 队列(kit orchestrator.py)**:pending_adopts 子串消费误吞同前缀案(RUN1500 吃 RUN1500B)→ 精确 case_id 匹配;read+replace 竞态(潜伏数周,生产者追加可丢)→ rename 快照消费(沙箱实测零丢失)
- **F6**:monitor daemon Popen 前 `/proc` 查活,同 case 复用不重复起
- **F7**:goal-sync 插件配置 640 user:mdagent;快照副本 token 脱敏
- **「绝不野跑」铁律**:任何 MD 进程必须经 launch_agent_gate 上轨并被领养;套件 `no_wild_runs` 硬 FAIL——断言为 managed-aware(在轨=state adopted_pids ∪ 待领养队列),合法运行不误报
- **RUN1500 重跑 stage 1(equil)上轨**:PRJ_SYS-A_equil_2x(240k 步 NPT+NVT 300K,30 核)经 gate 发车(物理校验零警告),编排器终态 FAILED 自动归档 RUN2500 → IDLE → 领养 → MONITOR 全链实测通过。顺手两修:orchestrator `_update_bus` 补 total/pct(领养案进度条此前回落陈旧案总步数);web_monitor `get_status` 对无 Stage 注释案回落 task.total_steps(不再错用全局 1350000 兜底)
- **套件断言三处跟随演进**:`state_run2500_sealed` 改案感知(离任即封印生效,仍挂 RUN2500 才要 FAILED+adopt=false);`equil_live_health` 进程探测改 /proc cwd 扫描(rootrun,lmp 属主 mdorch user 读不了 /proc);meta_observer 08:00 物理否决(发车窗无热力学行 0.0K)为时间窗误报,08:30 自愈 YELLOW
- **服务现状**:mdorch/md-guard/md-watchdog/md-board 四 unit active+enabled;equil 在跑健康;**欠账**:守护 auto_followup3.sh 仍硬编码已删快照树+5 元素模板(#119,RUN1500 DONE 前必换绑);stage 2 生产段待发(#120)

---

</details>

# dsh-agent v1.6.4+20260919 发布说明

全链分子模拟智能体(LAMMPS/ReaxFF + GROMACS)—— dsh CLI 套壳版。

**许可证**:**PolyForm Noncommercial 1.0.0**(见 LICENSE)——开源可见、可修改、可非商用(个人/学术/教育机构),**禁止商业使用**,商用须另行获得版权人(AI4Science MD Team)书面授权。第三方组件声明见 NOTICE。

## 本版变化(相对 v1.6.3+20260918)

**09-19 误删事故(4.2G 算例数据永久丢失) + 数据保护四件套落地(dsh-agent 本体无代码变更,全在数据治理面)**

- **事故实锤链**:00:01:02 用户在另一会话批准"旧版删除" → 00:02:00 该会话 `rm -rf` 12 个快照(v1.5.11~v1.6.2) → 撞 root 属主文件后提权清残 → 00:02:21 完成。**v1.5.11 的 cases/ 是 RUN1500/RUN2000/RUN2500 全部科学数据真身**(含在跑的 r26,进程随之死亡)——"可扔快照"与"不可再生数据"共树,清理策略结构性误杀。四层根因:命名说谎(快照名义装 live 数据)/执行方无信号(无本项目上下文,读不懂 root 属主警报)/授权语义错位("旧版"两会话外延不同)/零技术防线。知识全幸存(笔记/FIX-RI 记录/r26 物理修复模板),数据全灭(ext4 活跃盘不可恢复)。
- **四件套(大厂"删除是 workflow 不是 syscall"单机降维)**:
  1. **缓刑区**(S3 soft delete 同构,唯一治本):删除一律 `~/bin/trash_mv.sh` 移入 `~/.trash/YYYYMMDD/`,72h 后 cron 03:47 `trash_purge.sh` 才真删(purge 硬编码只碰 .trash,日期目录名正则校验)
  2. **四问删除闸**(穷人版 K8s finalizer):`sudo /usr/local/bin/predelete_check.sh <path>`——活跃进程 cwd/24h 写入/异主文件(以目录属主为基准)/registry+state+unit+cron 引用,任一命中即拒;保护名单(AI-MD/mdsim-data/cases/归档/kit)+ 浅于 4 段路径直接 rc=2;symlink/.. 穿越经 readlink 解析后照拦
  3. **算例数据分家**:真身迁 `AI-MD/mdsim-data/cases/`(registry 7 处 + state 78 处引用换绑,备份 `.bak_casesroot_*`);path_jail 天然兼容(deny 只含 dsh-home);快照 README 头部机器可读行 `LIVE_DATA: yes/no`,从此快照只含代码+软链
  4. **DONE 不可变归档**(WORM 同构):`AI-MD/归档/` + `sudo archive_seal.sh`(chattr -R +i,user 本人也删不动);解封 `archive_unseal.sh` 留痕 root 属主 `/var/log/archive_unseal.log`
  - sudoers 新增仅三条 NOPASSWD(`70-data-protection`,visudo 通过);纪律写入项目记忆,同目录所有会话共享
- **对抗审计 13 项全过**:purge symlink 逃逸/非日期名/非法日期全跳过;闸 symlink 绕过/.. 穿越/registry 阳性全拦;封存后 chattr/mv/覆盖写/无 sudo unseal 全部 Operation not permitted;压测 2000 文件树闸扫 0.78s、purge 混合龄精确
- **服务现状**:mdorch/md-guard 保持停止(事故后编排器曾对幽灵目录发起第 23 轮恢复 r27,已拦);RUN2500 链无 restart 可续,重跑待用户决策(r26 修复物理模板在 v1.6.3 快照);RUN1500 重跑待排
- **运维纪律(追加)**:⑤ 任何会话批量删除前必跑四问闸并把输出贴给用户,删除动作一律 trash_mv 缓刑;⑥ 快照可弃性的前提是 LIVE_DATA: no,没有该行的一律当不可删处理

---

# dsh-agent v1.6.3+20260918 发布说明

全链分子模拟智能体(LAMMPS/ReaxFF + GROMACS)—— dsh CLI 套壳版。

**许可证**:**PolyForm Noncommercial 1.0.0**(见 LICENSE)——开源可见、可修改、可非商用(个人/学术/教育机构),**禁止商业使用**,商用须另行获得版权人(AI4Science MD Team)书面授权。第三方组件声明见 NOTICE。

## 本版变化(相对 v1.6.2+20260918)

**恢复强度熔断(FIX-RI)上线并实弹验收 + RUN2500 确定性毒点手动物理修复(dsh-agent 本体无代码变更,全在 kit runtime v3.3 与运维面)**

- **FIX-RI 恢复强度熔断(kit v3.3,备份 `.bak_intensity_20260918`)**:既有 recovery_breaker 唯一扳机是"策略耗尽",而 checkpoint_restart 永远"成功"→ 恢复 loop 永不熔断(RUN2000 两天 35 次的结构性原因)。补第二扳机(Erlang restart intensity 同构):`_recovery_intensity_trip` 挂 RESEARCH 入口——24h 滚动窗内恢复 ≥4 次且相邻无 ≥6h 稳定间隔 → 开熔断(next_retry_ts=+21600 时间戳退避非阻塞)+ `breaker_terminal` 人工闸 + 飞书 + 转 MONITOR。清零纪律(K8s 同款):发车成功绝不清零,只认 ≥6h 稳定/DONE 换任务/人工销闸。配套 research/preemptive/F4 三处成功账补 ts(旧账无 ts 天然豁免)。判定必须是程序不是智能体。沙箱 8/8(`test_intensity_trip.py`,快照根)。
- **实弹验收(当日 21:59 首跳)**:RUN2500 确定性毒点(restart.150000 → ISO 2500K 第 599 步 hbondchk 数组溢出,r16–r25 十连死同一签名、22 轮空转)——4 笔带 ts 账(21:13/25/37/49,间隔 12min)后第 5 次 RESEARCH 入口跳闸。六项核验全过:熔断日志/账目/6h 退避/人工闸 ug-20260918-32/MONITOR 停手/DECIDE 压制 RESEARCH(CRITICAL 照报但 decision=CONTINUE,空转物理停止)。策略链现场复盘:Pivot 已识别 checkpoint_restart 连败,但策略库+联网+蜂群三路掏不出第二招——满分执行的空转,正是熔断该兜的死角。全记录 `FIX-RI_实弹记录_20260918.md`(快照根)。
- **RUN2500 手动物理修复(r26,人工销闸后上线)**:两死因实锤——base HEAT 段 tdamp=25 热浴失控(3406K 尖峰 lost atoms);r1 终点欠温 542K 后 ISO tdamp=8 硬拖注入 → hbondchk。修:r26 从 restart.100000 续(150000 毒化隔离 quarantine/),HEAT tdamp 25→10(实测贴轨:104000 步理论 1488K 实测 1486K)+ ISO 前 `velocity all create 2500`(免注入+破确定论)+ ISO tdamp 8→5,dt 保持 0.1 保全步数账目。封存模板 `production.in.template_orig` 同步手术(备份 .bak_phys_20260918)——未来自动恢复继承新物理(velocity create 不加:reshape 会摘、launch gate 会拦)。state 手术:prod_dir 换绑/breaker 人工销闸/4 笔强度账归档 `recovery_history_archived_20260918_fixri` 窗清零/_current 重链/registry 换绑,编排器 22:15 领养 alive=32 step 推进。里程碑(越过历史死点 150599)验证进行中,哨兵 `watch_run2500_r26.sh`。
- **运维纪律(追加)**:① user 侧探活只能用 cmdline 匹配/文件新鲜度——readlink /proc/pid/cwd 是属主/root 特权视角,跨身份误判死亡(哨兵假死乌龙实锤);② LMP/MPIRUN 真值在 mdorch.service unit 的 Environment(代码默认路径已失效),手工发车先查 unit;③ registry.json 是 {key: [{case_id, prod_dir, ...}]} 列表结构,walk 更新按 case_id 字段匹配;④ 手工恢复入账必须补 ts(强度熔断统计命根),人工销闸=强度窗清零+旧账归档不清毁。

---

# dsh-agent v1.6.2+20260918 发布说明

全链分子模拟智能体(LAMMPS/ReaxFF + GROMACS)—— dsh CLI 套壳版。

**许可证**:**PolyForm Noncommercial 1.0.0**(见 LICENSE)——开源可见、可修改、可非商用(个人/学术/教育机构),**禁止商业使用**,商用须另行获得版权人(AI4Science MD Team)书面授权。第三方组件声明见 NOTICE。

## 本版变化(相对 v1.6.1+20260918)

**DONE 收口断链四根因根治 + dsh-goal-sync 插件上线(GoalBar 自动刷新)+ 看板跟随 _current**

动因:RUN2000_r35 真 DONE(115 万步跑完)后全链不闭环——analyze 报假零指标、编排器终态误炸 FAILED、guard 看不见完成不发 RUN2500、看板「今日已停止」误报、GoalBar 冻在 09-17 化石文案。深挖见会话交接记录,四根因全部实锤取证。

- **根因① analyze 假零(kit runtime v3.3,FIX-AN1/AN2)**:orchestrator 硬编码 `prod_dir/species.out`,真产物在 `RUN2000_r35` 找不到 → analyze_agent `or 0` fail-open → char_yield=0% 假数据还飞书报喜。修:新方法 `_resolve_species_file`(发现序:task 显式 species_file → `_current` 软链接目录 → prod_dir 本体,每目录取最新非空 `species.out*`);result 带 error 时飞书明示「产物分析失败,指标勿引用」。
- **根因② pending_adopts 契约破坏(FIX-RQ1)**:launch_agent 每次发车(含 guard 内部 resume)都写 pending_adopts,recover 直连 mpirun 从不消费;terminal_requeue 不剔除 `case_id==刚终任务` 的陈旧条 → DONE→IDLE 时陈旧条炸出 adopt_rejected→FAILED 假崩(strategy_decisions.jsonl 11:19 时间线实锤)。修:terminal_requeue 里 scrub 同 case_id 条目(记 `adopt_queue_scrub` 策略账,os.replace 原子重写)。
- **根因③ guard/看板不跟 `_current` 软链接**(task #33 约定 `T<T>_current → T<T>_rN`,下游全在盯 base 目录):guard auto_followup3.sh 加 `cur_dir()` helper(readlink -f),四真相源统一;web_monitor.py fallback/registry 两路都解 `_current`,条目新增 `completed`(final.data 存在性);前端 `done = !alive && (completed || step≥1350000)` → DONE 显「已完成」蓝/100%,不再显「已停止」灰。另 FIX-RD:guard 锁存 T1/T2/T3 从纯内存改 result.json 存在性推断——**重启不再重发车**(launch gate 只有 flock 无去重,这层是唯一防线)。
- **根因④ species 数据碎片化**:33 个 restart 目录各有片段,r10 超程段污染。拼接脚本 `stitch_species.py`(快照根):r1..r9+r35(resume1100000)后段优先去重、封顶 1150000 → RUN2000_r35/species.out 10113 帧(100..1150000);唯一永久缺口 11300-150100(run-0 早期就地续跑覆盖,quarantine 只有 restart 无 species;ISO 2000K 段完整)。parse_species.py 修 FIX-SP:glob `species.out*` 取最新非空。
- **根因⑤ registry ACL 失明(同日续挖,用户报"卡片点了没反应"实锤)**:编排器(mdorch)12:08 原子重写 registry.json → 新文件属主 mdorch、目录默认 ACL 只传播 mdagent → user 身份的看板读注册表 EPERM → `_merge_registry` 静默降级 → RUN1500/RUN2000 从看板整张消失、详情接口 404。修:`state/` 目录默认 ACL 补 `u:user:rX`(未来重写不再丢)+ 现有 registry.json 补读权;分组标题「今日已停止」改「今日完结」(完成任务落此组,卡片自带 已完成/已停止 徽标)。
- **二轮深挖再修三洞(同日)**:
  - **详情页相位串台(FIX-PS)**:`get_status` 对任意 prod_dir 都借用编排器当前任务的 phase/monitor/analyze 节——RUN2500 在跑时 RUN2000 详情显示 MONITOR 且 DONE 收口分支永不触发。修:prod_dir≠当前任务时相位按本地产物推断(final.data→DONE 否则 HISTORY),monitor/analyze 节禁用(否则 RUN2000 详情温度会显示 RUN2500 的 298K);DONE 分支 step 改 `max(sched_total, 日志尾步)`(续跑目录的 sched_total 只是当段 run 值)。
  - **watchdog L4/L5 假阳性(mdagent-deploy/watchdog_check.py,FIX-WD)**:L4 盯 base 目录,`_current` 移走后已完成任务永久误报(RUN2000 "停更2976分钟"每 5 分钟覆盖写 ALERTS 实锤)——改跟随 `_current` 软链接;L5 裸 urlopen 401 误报——HTTPError 401/403 认作服务存活。另 FIX-WD2:ALERTS 清除原本只在 daily() 里,而 daily() 当天已写就早退 → 全绿周期永不清陈旧告警(挂一整天),清除挪进主循环。md-watchdog 已 systemctl 重启,六层检查全绿,陈旧 ALERTS 已清除。
  - **看板 token 门被重启丢 env 静默关闭**:本轮早些时候重启 web_monitor 未带 `MD_BOARD_TOKEN` → 认证门整体关闭(无 token 200 实锤)。已带 token 重启恢复(no-token 401/with-token 200);绑定保持 127.0.0.1。**教训:重启 web_monitor 必须带 MD_BOARD_TOKEN,门是否在启用态纳入验收**。
  - **memory/notes 同型 ACL 失明**:research 笔记(mdorch 写)user 不可读 → 图谱研判笔记节点静默缺失。memory/ 与 notes/ 默认 ACL 补 `u:user:rX` + 存量 14 个文件补读权。events/scratch 全扫无 mdorch/root 属主文件,无同型残留。
- **恢复强度熔断(kit runtime v3.3,FIX-RI,同日续落地)**:既有 recovery_breaker 唯一扳机是"策略耗尽",而 RUN2000 循环 35 次恢复次次"成功"(checkpoint_restart 永远可用)→ 永不耗尽 → 永不熔断。补第二扳机(Erlang restart intensity 同构):`_recovery_intensity_trip`——24h 滚动窗内恢复 ≥4 次且相邻间隔无一段 ≥6h 稳定 → 开熔断(`next_retry_ts=now+21600` 时间戳退避非阻塞)+ `breaker_terminal` 人工闸 + 飞书告警 + 转 MONITOR。清零纪律(K8s 同款):发车成功绝不清零,只认 ≥6h 稳定间隔/DONE 换任务/退避到期。配套:research/preemptive/F4 本地兜底三处成功账补 `ts` 时间戳(09-18 前 54 条旧账无 ts 天然豁免,零误炸)。判定必须是程序不是智能体——安全限制走 LLM 等于让被告当法官。沙箱 8/8(阈值/稳定间隔豁免/旧账豁免/窗边界 23.9h vs 24.1h/幂等 strikes 递增);部署后 RUN2500 一次真实恢复全程无干扰、ts 正常落账,备份 `.bak_intensity_20260918`。已知盲区:guard resume_case 直连不经此路径(09-16→18 账证明真循环全走编排器路径)。**当晚实弹验收**:RUN2500 确定性毒点(restart.150000→ISO 2500K 第 599 步 hbondchk 十连死)空转 4 轮(46 分钟)后 21:59 跳闸,日志/账目/6h 退避/人工闸/MONITOR 停手/DECIDE 压制六项核验全过,全记录 `/home/user/FIX-RI_实弹记录_20260918.md`。
- **dsh-goal-sync 插件(新 `dsh-plugins/dsh-goal-sync/`,web profile 已挂)**:GoalBar 此前唯一更新通道是模型自觉调 update_goal,goal round 续行停后即化石。插件 timer(120s)+turn/end 触发,拉看板 `/api/status` 按 slots 渲染进度条(`③✅RUN2000 ④🔄RUN2500(N% T_K)` 三态),内容不变不 edit(防 revision 刷屏)+90s 最小间隔+stale revision 重试一次;零模型 token、不依赖模型自觉。沙箱六断言全过后上线,实测 rev 23→链式触发后自动翻 ④🔄。
- **首个真实 RUN2000 产物指标**:analyze_agent char_yield=30.01%(p_retention=null——P 末帧缺席,诚实空值非 bug);parse_species R3_char_yield=0.5499 vs TGA 基准 0.585。
- **验证**:`test_resolve_species.py`(快照根)species 发现链 5/5;`test_guard_sandbox.sh`(快照根)fixture+stub 发车 5/5(T1 幂等/T2 跟 _current/发前存档/不误报崩溃);端到端:RUN2000 DONE → analyze → scrub → guard 存档 species_RUN2000.csv(808KB)→ gate 发 RUN2500(33 进程)→ 编排器 12:08:15 领养 MONITOR → 看板/GoalBar 自动跟随,后台监视器 2 分钟粒度全程记录。
- **运维纪律(追加)**:凡涉及 prod_dir 的下游消费(analyze/guard/看板/parse)必须跟 `_current` 软链接;改正在跑的 bash 脚本必须 `systemctl restart`(bash 按字节偏移读脚本);严禁手改 session_projcache.json(CAS+严格回放,外改即毁投影)。

---

# dsh-agent v1.6.1+20260918 发布说明

全链分子模拟智能体(LAMMPS/ReaxFF + GROMACS)—— dsh CLI 套壳版。

**许可证**:**PolyForm Noncommercial 1.0.0**(见 LICENSE)——开源可见、可修改、可非商用(个人/学术/教育机构),**禁止商业使用**,商用须另行获得版权人(AI4Science MD Team)书面授权。第三方组件声明见 NOTICE。

## 本版变化(相对 v1.6.0+20260918)

**checkpoint_restart 根治 + 恢复死循环四根因修复(kit runtime v3.3,dsh-agent 本体无代码变更)**

变更落在编排器运行时(`agent_deploy_kit_20260630/agents/runtime/v3.3/`,root:mdorch,备份 `.bak_20260918_ckpt/_loop/_loop2`),此处记录系统级行为变更。动因:RUN2000 两天 30+ 次恢复循环(r10 overshoot 到 1.2M/1.15M、r11-r34 run-0 空转、健康 run 被误杀)。

- **续跑三 bug 根治(mdagent 提案 + 评审修正)**——`recover_agent._reshape_for_resume`:read_restart 换入后按 restart 步数重塑模板:摘 `velocity all create`(淬火放大器)/裁已完成阶段/部分段温度插值/run 折算剩余步数/产物文件加 `.resume<step>` 防覆盖。`launch_agent.validate_input_physics` 新闸(续跑 input 含 velocity create 拦、计划终点超 intended 5% 拦、多阶段告警);`monitor_agent` STEP_OVERRUN 运行时兜底。
- **根因① monitor_daemon 温度误杀**:daemon 藏硬编码 `temp > 2000`(monitor_agent 早有浮动阈值,两处未同步),ISO 2000K 正常涨落 ±30K 被 CRITICAL → RESEARCH → RECOVER kill sweep 杀健康 run(r15@1104000 步 T=2009.1K 被杀实锤)。改 schedule 感知 `max(2000, 目标×1.3)`,与 monitor_agent 同口径。
- **根因② 模板降解**:`apply_strategy` 每轮就地改写 production.in,次轮在已裁剪产物上再 reshape → 残余 run 值被当从 0 起算的段边界 → restart 步数恒"越界" → run 0 空转(r16-r34 实锤)。修:首轮封存原始模板为 `.template_orig`,**锚定 base 目录**(剥 _rN 后缀——编排器 prod_dir 跟随 _current 前移,封存件放当轮目录会被甩下,r30-r34 二次实锤)。
- **根因③ 越过终点拒启**:restart 步数 ≥ 原始模板计划终点(各段 run 之和)→ recover 明确拒绝,不再 run-0 空转刷假 final.data;完成收口走既有 DONE 通道(stdout "Total wall time")。
- **运维纪律(教训固化)**:编排器必须 `systemctl` 管(unit 带 `MDSIM_DATA_ROOT`,手动裸起断 schedule seam → daemon 回退内置程序误判温度基准);隔离毒化 restart 必须移出 `pyro.restart.*` glob(quarantine/ 子目录,改后缀仍命中);run-0 目录的 final.data 是半成品,**严禁归档进 base 或喂下一温度档**。
- **验证**:沙箱 `verify_fix.py` 37/37(ISO/HEAT 段续跑、越终点保底、launch 闸、两轮幂等、base 锚定、daemon 温度容差正误两向);实测 r35 自 110 万步健康续跑 ISO `run 50000`(daemon 注入正确 schedule,无多重后缀)。
- **已知残留(非本版范围)**:watchdog L4/L5 假阳性(盯硬编码 base stdout / 裸 urlopen 无 token)每 5 分钟覆盖写 ALERTS;升温段 ~900-930K 必死的真崩溃死因未查明;dsh-agent 全量测试 67 项环境性失败(kit 权限墙 + 无 kit 环境,2026-09-16 快照同口径,非回归);test_tagging 21/21 绿。

---

# dsh-agent v1.6.0+20260918 发布说明

全链分子模拟智能体(LAMMPS/ReaxFF + GROMACS)—— dsh CLI 套壳版。

**许可证**:**PolyForm Noncommercial 1.0.0**(见 LICENSE)——开源可见、可修改、可非商用(个人/学术/教育机构),**禁止商业使用**,商用须另行获得版权人(AI4Science MD Team)书面授权。第三方组件声明见 NOTICE。

## 本版变化(相对 v1.5.27+20260917)

**专家进化体系 ①:三轴知识打标 + 回填 + 提案分流(零 token,纯确定性规则)**

- **动因**:知识库是"被动图书馆"(475+ 原子存在但运行时无一工具查本地库);蒸馏一维压平(课题/方法/运行问题混在 kind 里);毕业靠感觉。用户拍板三轴沉淀:课题(→子agent)/方法(→skill)/智能体运行问题(→skill 或子agent),考题只用真实跑过有结局的事件,绝不重跑 LAMMPS。
- **`evolution/expert_tags.json`(新)**:受控词表——method 10 标签(repair-rescue/dynamics/temp-press/model-opt/modeling·packmol|moltemplate|scripting/trajectory·dump/postproc/hpc-exec/phys-perception)+ ops 5 标签 + known_topics 归并表 + case_id 提取正则 + stopwords + exam_ready_kinds。新增标签=改此文件过人工闸。
- **`evolution/tagging.py`(新)**:`tag_atom(kind,fact,refs)→{topic,topic_candidates,method,ops,exam_ready}` 纯函数;exam_ready=三要素校验(refs 溯源+结局类 kind+结局信号词);`verify_refs()`=进考题留出集前的 refs 存在性核验。topic/topic_candidates 分轴——case_id 正则命中的候选课题不过人工闸,不计毕业资格审查。
- **`evolution/memory.py`**:record_atom 写入时自动打 axes,打标器异常不阻断入账。
- **`evolution/backfill_tags.py`(新)**:一次性回填 CLI,默认 dry-run 报告,`--write` 全程持 ATOMS.lock 带备份原子替换。
- **`evolution/observe.py`**:提案 meta.json 与人工闸请求均带 axes(分流可视)。
- **真账本 482 条已回填**:exam_ready 283,三轴全空 21,灌水告警 4;方法轴 repair-rescue 295 最厚(首个回放考场候选),课题轴 sys-a-pyrolysis 269/run2000-pyrolysis 238。
- **压测 15/15**:tag_atom 3 万条/s;10MB 病态长文曾让 case_id 正则回溯卡死分钟级→入口截断;30 线程并发 600 条 id 唯一;三次回填幂等。
- **三路红队审计(μ代码/ν语义灌水/ξ文件系统,无 P0)**:str refs 逐字符拆分、backfill 双读无锁丢并发原子、坏行/NaN 静默丢弃、伪课题名灌水认证轴、"audit checklist"误标 ops/security、refs 虚构可混考场、空关键词全命中、软链写入、备份权限撞名——全部修复并各配回归测试,`tests/test_tagging.py` 21 项全过。

# dsh-agent v1.5.27+20260917 发布说明

全链分子模拟智能体(LAMMPS/ReaxFF + GROMACS)—— dsh CLI 套壳版。

**许可证**:**PolyForm Noncommercial 1.0.0**(见 LICENSE)——开源可见、可修改、可非商用(个人/学术/教育机构),**禁止商业使用**,商用须另行获得版权人(AI4Science MD Team)书面授权。第三方组件声明见 NOTICE。

## 本版变化(相对 v1.5.26+20260917)

**行为复查类提案去人工化:自动蒸馏闭环(三兜底)+ 图谱布局修复**

- **起因实证**:演化层五类行为复发提案(否定纠正/重复追问/打断/工具错误/审批否决)走人工闸,用户连点 9 条销项行为层面零变化——"拍板"只是已读回执,同类提案逐日复发。用户拍板:这类不要人工,接分布式讨论蒸馏的决策。
- **兜底① 自动蒸馏(evolution/behavior_strategies.py 新模块)**:五类签名提案 tier 改 `auto`,不再开人工闸;确定性规则模板写 `mdsim-data/memory/behavior_strategies.json`(`source: auto-distill`,带 7 天基线),并渲染进 persona 两处(`agent.cordis.yml` + headless patch,标记段幂等重生成、程序保证逐字一致,原人肉红线变程序约束),新会话自动携带。首日实产 4 条策略。
- **兜底② 后验撤回**:每轮 observe 比对窗口计数,**严格连升 4 轮**(复发仍在发生)才撤回——初版"对基线不降即撤"被实证必误判(7 天窗计数衰减慢,基线即当前值);撤回=移出 persona+签名冷却 7 天+开人工闸叫人(异常才介入)。
- **兜底③ 人工降级周报复核**:看板左下"行为策略"卡(GET `/api/behavior_strategies`),active 条目可"翻案"(POST `/api/behavior_strategy_revoke`)——撤下策略+签名永久回人工闸。已实测翻案-恢复全链。
- **graph.html 两处修复**:resize 不重取 devicePixelRatio(缩放/跨屏后画布位图错配拉伸)+ 弹簧力公式多乘 `d3/110*8` 成二次方(长连线一脚~700px/帧,节点甩到屏幕两缘冻成三角翼——"拉伸的很奇怪"根因),改回线性胡克。
- **测试**:test_evolution 三个旧政策测试(human 语义)更新为 auto 语义;TestObserve 补 bstrat 沙盒(此前测试直写真实策略库/persona,已清污染);顺带修复 B2 回潮(record_atom 近似去重按 fact 吞掉同秒异 case,fact 补 case_id 尾巴)。user 下 180 测 0 失败(52 error 全为 kit root:mdorch 权限,环境性)。

# dsh-agent v1.5.26+20260917 发布说明

全链分子模拟智能体(LAMMPS/ReaxFF + GROMACS)—— dsh CLI 套壳版。

**许可证**:**PolyForm Noncommercial 1.0.0**(见 LICENSE)——开源可见、可修改、可非商用(个人/学术/教育机构),**禁止商业使用**,商用须另行获得版权人(AI4Science MD Team)书面授权。第三方组件声明见 NOTICE。

## 本版变化(相对 v1.5.25+20260917)

**workbench 远程访问修复 + 会话级 approval 固化根因闭合**

- **workbench.html 去硬编码**:页面内 iframe(对话 3091/看板 8080)与全部 API 基址从写死 `127.0.0.1` 改为跟随 `location.hostname`——旧写法在远端浏览器里指向访客自己机器,左对话右看板全不可达("看板不可达"即此因)。本机/隧道/LAN 三种打开方式现在都正确。
- **看板绑 LAN**:`WEB_HOST=0.0.0.0` 启动(web_monitor 原生支持的官方开关),token 认证不变(实测无 token 401/带 token 302 种 Cookie)。只看板可直连 `http://10.180.32.93:8080/?token=...`。
- **dsh 对话 GUI 保持 loopback**:`dsh web --host 0.0.0.0` 被官方安全互锁拒绝(无认证控制台不暴露网络),不绕过。完整 workbench 走 SSH 隧道:`ssh -L 8080:127.0.0.1:8080 -L 3091:127.0.0.1:3091 user@10.180.32.93`,再开 `http://127.0.0.1:8080/workbench?token=...`。对话历史存服务端 dsh-home/sessions,不受重启影响。
- **审批死锁最后一环闭合(无代码改动,操作知识)**:会话创建瞬间 seq 2 固化 `approval/policy` 事件,会话事件优先于 config(`overrideOf(session) ?? config.policy`)——cordis.patch.yml 修复只对**新会话**生效;04:41 前创建的旧会话永远 never。日志实证:session-86c80a1c(修复前)=never,session-ce741f64(04:43,修复后)=ask。**验收 sim_exec 弹窗必须新建会话,旧会话复测永远假阴性**。auto-approve.json 保持删除(人工闸设计)。

# dsh-agent v1.5.25+20260917 发布说明

全链分子模拟智能体(LAMMPS/ReaxFF + GROMACS)—— dsh CLI 套壳版。

**许可证**:**PolyForm Noncommercial 1.0.0**(见 LICENSE)——开源可见、可修改、可非商用(个人/学术/教育机构),**禁止商业使用**,商用须另行获得版权人(AI4Science MD Team)书面授权。第三方组件声明见 NOTICE。

## 本版变化(相对 v1.5.24+20260917)

**三区执行策略落地:mdorch 侧裁决器 + 六脚本路径囚禁**

背景:实测确认 mdagent 可直 `sudo -u mdorch claude_interface.py exec` 绕开 dsh 弹窗(PROBE_DIRECT_SUDO_OK)——ops-gate 只是装饰层,本版把真墙建进 mdorch 进程。

- **脚本囚禁(kit v3.3)**:新建 `path_jail.py`(允许 2025-WORKPKG 大树 + /tmp/mdruns,拒绝 kit 运行时/dsh-agent/mdsim-data-dsh-home 子树,realpath 防 symlink 逃逸);launch/monitor/preflight/recover 四脚本入口囚禁 prod_dir/log_file/data_file/ff_file,越界一律 REJECTED。
- **recover_agent 两处必修**:case_id basename 校验(run_research 临时文件名路径逃逸任意写);prod_dir 囚禁(派生 kill 横扫集随入口收敛)。
- **launch_agent 内嵌输入安全闸**:shell/variable shell/include/python 四类禁项从外层包装器 launch_agent_gate.py 搬进本体,直调不再绕过;闸前置到落盘前。
- **裁决器**:`/etc/mdsim/exec-policy.json`(root:root,读失败=红 fail-closed)+ claude_interface `_adjudicate`——绿(只读命令/绿名单脚本/packmol/mpirun+lmp)直过+审计;黄(launch/preflight/recover)执行+审计标黄;红(其余一切,含 `python3 -c`)不执行、写 `/var/lib/mdsim/gate_inbox/` 排队。
- **人工放行**:看板新增 `GET /api/exec_gate`(列队)+ `POST /api/exec_gate_resolve`(approve/deny,批准与命令 sha256 全等绑定);批准后请求方原样重发即执行,一次性消费,篡改任一参数即重新排队。
- 白名单不收窄:收窄会把危险命令从"红区排队"降级成"硬拒绝",失去人工放行通道——裁决层接管,白名单保持原样。
- 实测:绿直过/红排队/fail-closed/批准执行/篡改重排队/拒绝阻断 六组全过;RUN2000_r6 在跑不受影响。

# dsh-agent v1.5.24+20260917 发布说明

全链分子模拟智能体（LAMMPS/ReaxFF + GROMACS）—— dsh CLI 套壳版。

**许可证**：**PolyForm Noncommercial 1.0.0**（见 LICENSE）——开源可见、可修改、可非商用（个人/学术/教育机构），**禁止商业使用**，商用须另行获得版权人（AI4Science MD Team）书面授权。第三方组件声明见 NOTICE。

## 本版变化（相对 v1.5.23+20260917）

**看板"在跑程序"盲区修复：编排器当前任务兜底合成**

- **病根**:`/api/tasks` 只扫 `RUN_CASE_BASE`（程序运行-case）+ registry（实为空表），守护重启到 `cases/` 树（RUN2000_rN）里的运行目录扫描天生看不到 → 看板程序列表空、用户误判"没有在跑"。与 401/token 无关。
- **修复**:`scan_tasks` 尾部新增兜底——`state.task.prod_dir`（领养链会更新到当前 rN 目录）指向的任务未被目录扫描覆盖时，按其 production.stdout 合成任务条目；判活复用既有 `_liveness_step_fallback`（mdorch 属主进程 /proc cwd 匹配为盲，按 step 变化判活，重启后首见 ≤2.5min 保守报停，下一 thermo 间隔自动转活）。
- **看板进程迁回 live 树**:web_monitor 此前跑在 v1.5.15 快照里（修代码不生效的根源之一），已重启到 `AI-MD/dsh-agent`，`MD_BOARD_TOKEN` 认证门保留不变。
- 验证：`/api/tasks` active 出现 `PRJ_SYS-A_L_pyro_RUN2000`(step 580000/1657.7K/alive/is_current)。

# dsh-agent v1.5.23+20260917 发布说明

全链分子模拟智能体（LAMMPS/ReaxFF + GROMACS）—— dsh CLI 套壳版。

**许可证**：**PolyForm Noncommercial 1.0.0**（见 LICENSE）——开源可见、可修改、可非商用（个人/学术/教育机构），**禁止商业使用**，商用须另行获得版权人（AI4Science MD Team）书面授权。第三方组件声明见 NOTICE。

## 本版变化（相对 v1.5.15+20260914；v1.5.16–22 为加固实施期 VERSION-only 迭代，本条目一并收口）

**加固体系落地（harness 跑 mdagent 低权身份）+ dsh-dock 用量记账进 web profile + prepareCall 双包事故修复**

- **硬约束加固（手册 `docs/加固/hardening_manual_v2_20260917.md`，压测 34/34）**：harness 唯一入口 `/usr/local/bin/run-dsh-jailed`（uid=mdagent，裸跑 run-dsh.sh=user 身份=绕全部约束）；sim_exec 经 `sudo -n -u mdorch` 桥（sudoers 钉死脚本路径，每次过人工弹窗）；trace 账本 `/var/log/mdsim/trace.jsonl` root:root 660 + ACL + `chattr +a`，dsh-md-trace 插件记全工具调用，root cron 每 10 分钟巡检；dsh-home 分面（profiles/settings.yaml 归 user 对 mdagent 只读 ACL，sessions/storages 归 mdagent）；auto-approve 全面废除。已知残余面三条（trace 可伪造追加/审批通道非能力零化/user 读不了 .credentials.yaml）属接受设计，见手册。
- **cordis.yml 启动锚定例外**：dsh 启动期无条件覆写 `profiles/*/cordis.yml`（固定常量），授 mdagent rw 该单文件，目录仍 rX 防删改名。
- **pipeline_check 日志选择回归**：`tests/test_pipeline_log_resolution.py` 7 例钉死 `_resolve_log_file` 语义（species.out mtime 新 + log.lammps 旧 → 取后者；*.out 永不进 fallback）。
- **dsh-dock 0.11.3（用量统计，web profile only）**：源码审查 + 压测 13/13（`/tmp/stress_dock.py`）。只开 tokenlog 用量记账（内存态+启动扫历史 6669 条）；mobile-relay 显式钉死（0.0.0.0 网关面）；开关/单价持久化只能改 settings.yaml 的 dsh-dock 段并重启（面板保存对 mdagent 只读 → EACCES，属加固语义非故障）。
- **prepareCall 事故修复**：装 dsh-dock 时其依赖把新版 `@deepseek-ai/dsh-llm 0.1.1-rc.2` 等 5 个 shadow 包拖进 web profile node_modules，与 repo rc.6 双包错位 → `registration.adapter.prepareCall is not a function` 全会话失败。修复：shadow 包改指 repo rc.6 的符号链接 + package.json `pnpm.overrides` 钉版。教训：**profile 里任何 pnpm install 后必须复查 link 符号链接与 shadow 包**；`dsh plugin add` 的 reconcile 会把直连 link 依赖自动加进 bundles 造成重复 insert（@vendor/dsh-browser 与 dsh-web-search-pro patch 撞 id 'browser'），bundles 只留调用方。
- **已知监控假阳性（未修，记入待办）**：watchdog L4 盯硬编码原始 `RUN2000/production.stdout`（重启到 rN 目录后永久停更误报）+ L5 看板检查不带 token 必 401 误报；ALERTS 文件每 5 分钟被覆盖重写，只删无用。RUN2000 在 ~900K 升温段可复现 MPI_Abort 硬死（无 ERROR），守护 restart 模板 read_restart 后 `velocity all create 300.0` 淬火是复发率 82% 的放大器——处置顺序与证据见交接记录。

# dsh-agent v1.5.15+20260914 发布说明

全链分子模拟智能体（LAMMPS/ReaxFF + GROMACS）—— dsh CLI 套壳版。

**许可证**：**PolyForm Noncommercial 1.0.0**（见 LICENSE）——开源可见、可修改、可非商用（个人/学术/教育机构），**禁止商业使用**，商用须另行获得版权人（AI4Science MD Team）书面授权。第三方组件声明见 NOTICE。

## 本版变化（相对 v1.5.14+20260913）

**project 断裂链根治 + 守护与自省层上线（v3/v4 两轮深挖清单 + 三次根因追杀）**

- **F6 project 契约透传**：launch_agent pending_adopts entry + orchestrator 领养分支两行透传；mdorch unit 补 `MDSIM_DATA_ROOT`（服务态 registry 查找断缝）——崩→续→重领养不再是 project 漏斗（此前三丢三修的根因）。
- **kit v3 清单**：S1 重启类策略免去重；S2 策略耗尽→蜂群搜索→checkpoint_restart 真执行体（apply_strategy 新分支：最新有效 restart 换入 read_restart，含 `LammpS Restart` 魔数+尺寸校验）+ `_run_agent` 超时 120→900s；S3 recover 失败不再终态 FAILED（checkpoint_fallback 回 MONITOR，终态 FAILED 只剩 adopt 拒绝/launch 失败）；S2c daemon 启动整体失败安全；V1 发车门堵 `variable shell` 绕过+include（实测拦截）；V2 死亡信号三轮确认防误杀；V3 领养清残留状态（7 键）；V4 thermo 温度物理范围闸 (0,50000)K；V5 daemon schedule 热重载；G3 发车并发锁。
- **kit v4 清单**：watchdog L6 Meta-Observer 死人开关；Meta-Observer 物理 Veto + 资源预算四层(绿/黄/红/黑，black 在 gate 硬拒发) + RED 输出 30 秒可读介入请求；budget_manager.py；knowledge_distiller.py（同 kind≥5 蒸馏决策树，幂等护栏）；cron md-meta(*/30)+md-distill(6:17)。
- **守护 v3 两处根因修复**：N1 存活探测改 /proc 扫描（旧 pgrep 两头错：真实 lmp cmdline 不含目录漏检、观察者命令反被误检）；N2 `local T=$1 DIR=...$T` bash 声明展开坑——DIR 恒为 `.../T`，**resume 从 v3 上线起从未真正触发过**，拆两条声明根治；RV restart 校验魔数实为 `LammpS Restart`（大写S）。
- **崩溃自愈全链实战验收**：守护检出→restart 校验→续跑发车(project/total 契约)→gate(锁+预算)→pending_adopts→终态 requeue→领养→MONITOR——RUN1500 从 restart.550000 复活（47.8%，34 lmp 进程，schedule 注入判温正确）。
- **看板**：知识页待拍板闸可点击销项（POST /api/gate_resolve → 收件箱 → 编排器 sweep 落账，白名单只销演化提案闸，gates 文件单写者审计链不破）；7 条机械闸（死案/被取代）自动销项。
- **联网调研管线 401 根治**：Kimi key 失效→供应商链 Kimi→glm-4.6（model_research 原为单供应商）；BigModel tool_result 字符串型来源正则收割（兼容 Kimi 结构化）；glm 思考链 max_tokens 2.5×放大；实弹 129 来源/4 verified 策略。
- **回归**：dsh python **426**（+3 供应商链）+ node **30** + kit **244** 全绿（kit 门修三类夹具契约：_run_agent 桩签名/S3 语义断言/策略库 ACL——root 包遗留的权限墙欠账一并清偿）。

# dsh-agent v1.5.14+20260913 发布说明

全链分子模拟智能体（LAMMPS/ReaxFF + GROMACS）—— dsh CLI 套壳版。

**许可证**：**PolyForm Noncommercial 1.0.0**（见 LICENSE）——开源可见、可修改、可非商用（个人/学术/教育机构），**禁止商业使用**，商用须另行获得版权人（AI4Science MD Team）书面授权。第三方组件声明见 NOTICE。

## 本版变化（相对 v1.5.13+20260913）

**工作台运行进度条 + kit 三补丁（F3 契约透传 / F4 RESEARCH 预算 / F5 daemon 可观测性）上线**

- **workbench 运行进度条（runbar）**：`dashboard/workbench.html` 顶栏下新增常驻细进度条——case_id · 相位+子相位 · Step/总步数 · 百分比 · 温度/目标 · 风险/异常数 · 编排器与 monitor daemon 健康微点 · 更新时间；数据取 `/api/status`（与看板同源，15s 轮询，不经过任何模型，数字纪律不破）。状态点三色（绿运行/黄 RECOVER·RESEARCH·风险≥60·有异常/红 FAILED·CRITICAL）；点任务名直开看板任务详情；空闲显示"空闲 · 无在管任务"。`total_steps` 直接显示 F3 修复后的真实契约值。验证：node 语法 + 桩 DOM 真实数据渲染 + 真 Chrome headless 截图三连。
- **kit F3（坑③ total_steps 契约透传）**：`launch_agent.py` pending_adopts entry 增 `total_steps` 字段；orchestrator 领养分支硬编码 120000 改 `pending.get("total_steps", 1150000)`——REPORT 百分比不再 225.8% 荒腔走板。
- **kit F4（坑④ RESEARCH wall-clock 预算）**：`_handle_research` 入口加 30 分钟总预算，超时降级本地最小恢复（recover_agent `skip_research`），防"调研-回退-再调研"拖死恢复；成功离场与换任务（`_research_case_id` 护栏）清账。
- **kit F5（坑⑤ daemon 无声死 + 主循环挂起）**：① daemon stdout/stderr 落 `logs/monitor_daemon_<case>.log`（尸检材料）；② OBSERVE 子步心跳检查——pid 消失或心跳>90s 自动重启（挂而不死先 SIGTERM 防双 daemon）；③ daemon 每轮原子写 `prod_dir/.monitor_daemon.hb`；④ 单 tick 300s 上限。**两处与原方案不同**：tick 防挂死用一次性线程+future 超时（`AgentBase.run` 把 tick 派发到 worker 线程，SIGALRM 仅主线程可用，原方案会每 tick 抛 ValueError）；心跳不写 `.v3.3_state.json`（编排器整字典落盘会冲掉 daemon 写的键→心跳永丢→误判死亡→daemon 堆积），改独立小文件零竞争。实战验证：上线当天两次自动重生（systemd 重启孤儿 + kill -9 破坏测试），94s 内恢复。
- **kit ADOPT_QUEUE 抬常量移植生产树**：0913 交付包内已有的修复（两读取点统一引模块常量 + TestTerminalRequeue setUp 补 patch）此前只在快照里，root 包只落了 M2 判活——本次补齐到 node01 生产树（orchestrator.py 三处 + test_regression.py setUp 三行，与 0913 包逐字一致），防下一版打包回归。
- **明确不做**：runbar 不做目标级里程碑条（五段工作流进度）——goal-state 约定未立，另议；F4 不改 recover_agent 内部联网逻辑（编排器层降级已足够防无限重入）。

**回归门槛**：dsh python **423** + node **30** + kit **244**（v1.5.14 封版实测，见 打包清单）。

# dsh-agent v1.5.13+20260913 发布说明

全链分子模拟智能体（LAMMPS/ReaxFF + GROMACS）—— dsh CLI 套壳版。

**许可证**：**PolyForm Noncommercial 1.0.0**（见 LICENSE）——开源可见、可修改、可非商用（个人/学术/教育机构），**禁止商业使用**，商用须另行获得版权人（AI4Science MD Team）书面授权。第三方组件声明见 NOTICE。

## 本版变化（相对 v1.5.12+20260912）

**知识面：知识名片 + 经验研讨团（用户拍板"还需要一智能体做分布式讨论,对经验名片总结归类,按智能体体系/按物理过程"）**

- **知识名片引擎 `evolution/knowledge_cards.py`**：memory 的 atoms/SOP/notes → 结构化名片（确定性骨架：kind/标题/事实/refs/计数/时间窗；正文可 LLM 蒸馏但提示词钉死"只准复述给定事实",原文事实永远随卡对照展示）。`--llm` 增量蒸馏（按内容指纹缓存,重跑零调用）；`review` 子命令人工核卡（`reviewed_by` 入卡）。蒸馏链 Kimi→glm,Kimi 401 自动跳过。当前 57 卡 27 蒸馏。
- **经验研讨团 `evolution/card_council.py`（五角色+主席双轴归类）**：五角色=智能体体系五层（对话/编排/监控/恢复/演化治理）+跨层通用；物理轴 8 类（热与能量/物质守恒/电荷平衡与收敛/键合与拓扑/数值稳定/建模与前处理/流程治理/其他）。三级降级 roles+chair → roles-only → 确定性关键词基线,LLM 失败永不阻塞；主席输出过覆盖率校验（丢卡回填基线/越界 id 剔除/未知组键拒收/跨组重复只留首见/**基线空组不整组丢弃**——实跑曾因此蒸发 2 卡,已修+回归钉死）。glm-4.6 思考链吃输出预算：角色 max_tokens 6000、主席 8000→16000 两档,紧凑序号输出（分组填卡序号不填长 id）,角色/主席结果按卡片指纹缓存抗抖动。产物 `memory/knowledge_taxonomy.json`（原子写派生视图,不改卡片本体）。
- **看板知识页 `dashboard/pages/knowledge.html` 升级**：三视图切换（按类别/按智能体体系/按物理过程）+ 研讨团横幅（模式/覆盖数/主席裁决语/升级命令）+ 待拍板闸区；`/api/knowledge_cards` 附带 taxonomy。60s 自刷新。
- **图谱页 `dashboard/pages/graph.html` ES5 语法降级（修旧内核白屏）**：原为全站唯一使用 `??`/`**`/async/对象展开/Object.entries/padStart 的页面,Chrome<80 解析即语法错误→整页白屏（用户实发,知识页正常/图谱白屏的差分定位）。降级到与全站同语法基线（var/function/fetch.then/apply 代展开）,行为逐条等价：实时数据 node 重放输出一致 + 服务器真 Chrome headless 截图像素验证（84% 面板底色+节点色齐全）。dsh-agent/mdexp/AIMDexp-GPU 三副本同步,交付-0911 快照不动。
- **web_monitor 来客登记（临诊）**：`log_message` 落 `/tmp/web_monitor_access.log`（时间/方法/路径/UA）,排查"用户打不开但本机正常"类通路盲区（此前请求日志静音,本次排障全程无观测面）;稳定后可撤。
- **`tools/root_fix_bundle_20260913.sh` v2（root 一键包,等 GRUB 后执行）**：①kit orchestrator M2 判活修复（cmdline 必须含 lmp_mpi,防 MPI abort 后 sh 包装残留误判 alive）+ ADOPT_QUEUE 抬常量（修 kit 门 3 红,见下）②策略库 setfacl user 可读（修 kit 门 2 个 PermissionError）③logs 补 setgid ④重启 mdorch ⑤root 跑 kit 门验证 ⑥销两条过期 kit 原生闸。全部带锚点断言+备份,不命中即止。
- **kit 侧同日修复记录（随交付快照分发,生产树等 root 包）**：09-12 终态 requeue 改动两处内联 `__file__` 领养路径,绕开 TestTerminalRequeue 的 BASE 沙盒直读生产 pending_adopts.jsonl（当日恰有活领养请求→DONE 误转 IDLE 假红 3 条）。生产路径本身与 launch_agent 写入一致无恙;修法=抬 `ADOPT_QUEUE` 模块常量（两读取点统一引用）+ 测试 setUp 补 patch。交付快照内已直接修好并以 AGENT_BASE 指快照根验证。
- **明确不做**：研讨团不引入新组件/不做自动写回卡片（taxonomy 永远派生视图）;知识名片不自动晋升为策略（走既有 L2 管线）;图谱不做 canvas 兼容层（ES5 语法即达基线）。

**回归门槛**：dsh python **423** 全绿（含 test_card_council 15 条新增）+ node **30** 全绿 + kit **244**（交付快照内全绿;生产树 239+5 红 = ADOPT_QUEUE 沙盒 3 + 权限墙读库 2,修复均打包在 root_fix_bundle v2,执行后即绿——见上）。

# dsh-agent v1.5.12+20260912 发布说明

全链分子模拟智能体（LAMMPS/ReaxFF + GROMACS）—— dsh CLI 套壳版。

**许可证**：**PolyForm Noncommercial 1.0.0**（见 LICENSE）——开源可见、可修改、可非商用（个人/学术/教育机构），**禁止商业使用**，商用须另行获得版权人（AI4Science MD Team）书面授权。第三方组件声明见 NOTICE。

## 本版变化（相对 v1.5.11+20260910）

**fable-5 借鉴落地（用户拍板"你改一下我的"+定调五段主工作流；引擎只 LAMMPS/GROMACS、建模工具只 packmol/moltemplate）**

- **人格新增【主工作流·五段总纲】**（用户口径逐段落地）：物理感知（按是否断键成键选引擎，物理没读透不带病建模）→ 建模（工具只 packmol 构型打包 + moltemplate 拓扑组装，不引入第三家）→ 动力学（力场/系综/时间/续跑四件事在此定，ReaxFF 0.1–0.25 fs、经典 1–2 fs）→ 管线监管（全程走编排器；遇错蜂群分布式讨论→比选→拍板；教训走 L2 进化沉淀）→ 分析（专科手产物为准）；**规模铁律：小体系验证成功后才大体系运行**，新体系/新力场/新参数组合一律先小体系（500–5000 原子量级）全链走通才放大。人格三处同步纪律照旧：presets 源 ≡ headless patch persona 块（逐字一致）≡ 数据根挂载点，本次三处已同步。
- **人格新增【记忆纪律】三条**（fable-5 三形状）：① 不露记忆痕迹——引用沉淀经验直接给结论与适用条件，实时数字仍必须工具真返回；② 适用边界优先防过度泛化——经验按"验证时的条件"定界不按"存在过"定界，引用前核对体系/引擎/力场/规模四要素，结论区分【已验证适用/条件相近可参考/不适用】，附 APPLY·DON'T APPLY 例对（ReaxFF-必炸 vs GROMACS-稳定双轨边界；packmol/moltemplate 分工都不做动力学）；③ 未入账不算数——只有 memory/(atoms+LEDGER) 算沉淀经验，沉淀走 L2 晋升管线不手改账本，改记忆文件前先读原文查重。
- **flow-gate 豁免白名单**（借鉴"先定义何时不需要证据工具"；依据 flowgate.jsonl 实测三类误报）：纯问候/致谢（全锚定不误放"好的，帮我看下 run1 状态"）；纯身份问句（含模拟事务词守卫，混入 run1/力场仍布防）；代码审计简报免 repair-intent（两条历史违规均为"只读审核代码"简报被误当模拟修复）；交接文档全文粘贴免 model-choice（"交接方案/将本文件完整粘贴"）。豁免只免布防（不注入提醒/不计违规），**不放松任何销防条件，"用户要求跳过工具"硬约束不在豁免之列**（回归测试钉死）。
- **status-intent 证据增 `sim_gmx_status`**（分引擎口径）：GROMACS 状态问答经 gmx 真相源作答不再误计违规；LAMMPS 布防面不变（原两工具仍可销防）。
- **新 MCP 工具 `sim_search_sessions`（10→11）**：只读检索历史会话存档（数据根 dsh-home/sessions，zstd 流式解压）——"上次 QEq 怎么修好的/之前哪个会话讨论过 AWH"类回忆型问题先查档拿真实片段（会话/工作目录/时间/角色/原文），不凭印象编造。只取真实用户行（source.kind=user，排除插件提醒/tool-result）与助手终稿 text 块（排除 reasoning）；多词空格=AND；上限钳制（60 会话扫描/30s 预算/每会话 3 条/默认 20 条）；缺档空结果不抛、zstd 缺席返错误节、只读零触碰（回归断言会话档 mtime 不变）。
- **旧代码清理**：`_latest_pipeline_snapshot` 迁移期 legacy 兜底删除（旧 mcp/_pipeline 目录已不存在，纯死代码）；乱码文件治理（`修复方案_AI_HANDOFF.md` 恢复正名，被 run-dsh.ps1 取代的乱码 Windows 启动器副本删除）；工具地图过时"7 工具"计数改为不写死数字防 rot。
- **明确不做**：moltemplate 未装机不纳入任何白名单/墙（纯输入构建器，产物仍须经 LAMMPS 门才能跑）；豁免机制不提供配置面（代码级，防降级攻击口径延续 2026-08-29 E4）。
- **同日权限墙侧**（记录在 mdagent-deploy/DEPLOY.md v1.3，非本 repo）：packmol 第三门 + 红队 27/27 零绕过 + 审计账 D5 修复（sim_exec 复通）+ 12Dec18 旧 lmp_mpi/mpich 启动器清剿上墙。

**回归门槛**:dsh python **394**（+6：search_sessions 注册/真实行过滤/多词 AND/缺档不抛/缺 q 报错/只读零触碰） + node **35**（+6：问候身份豁免/审计简报豁免且真修复仍布防/交接粘贴豁免且真选型仍布防/身份守卫/gmx_status 销防/跳过工具不豁免） + kit **240** 不动（kit 侧改动属权限墙记录）。

# dsh-agent v1.5.11+20260910 发布说明

全链分子模拟智能体（LAMMPS/ReaxFF）—— dsh CLI + Kimi K3 套壳版。

**许可证**：**PolyForm Noncommercial 1.0.0**（见 LICENSE）——开源可见、可修改、可非商用（个人/学术/教育机构），**禁止商业使用**，商用须另行获得版权人（AI4Science MD Team）书面授权。第三方组件声明见 NOTICE。

## 本版变化（相对 v1.5.10+20260910）

**GROMACS 并入 agent loop（用户拍板:"agent loop 就是针对 gromacs 和 lammps")+ 野跑闸（"不走编排器不能跑"→"野跑直接停掉,能续跑":auto_kill_wild 直接 SIGTERM)**

- **共享扫描器**：扫描逻辑抽成 `dashboard/gmx_scan.py` 唯一实现，看板（web_monitor re-export 保持调用面）与 MCP 工具共用，不再各写一份。
- **新 MCP 工具 `sim_gmx_status`**(9→10)：返回各 run 阶段/步数/进度%/模拟时长 ns/温度K/总能量/存活/接管标记，配置缺席→空列表不抛。人格两份 yml（逐字一致）工具清单+Mode A 路由更新：先分引擎——问 gromacs 调 sim_gmx_status（无 flow-gate)，问 LAMMPS 仍走 sim_pipeline_check。
- **推送驱动接 gromacs**：跑完（活→死且 pct≥100，常规进处理层）/中途停止（活→死未满程，严重推微信=进程死）/温度爆炸（>5000K，严重）。基线存 push_state.json 的 gmx 节，扫描失败保留旧基线不清空（同闸 D1 纪律）。
- **野跑闸**:`dashboard/gmx_adopted.json` 接管注册表（本机文件入 .gitignore,repo 只带 .example;env GMX_ADOPTED_FILE 可另指）。alive 且未接管 = 野跑：**`auto_kill_wild=true`（本机已开）时推送驱动直接 SIGTERM 停掉**——温和停，gromacs 走完当前步写终态检查点，`.cpt` 可续跑最多损失一个检查点间隔；pid 按 cwd 锁定不误杀，杀失败仍留严重告警不静默；同一野跑只处理一次。=false 时退回人工闸告警。看板红色"野跑·未接管"徽章；人格钉死"要放行先把 dir 加进 adopted 再重启"。注册表缺席/损坏按未接管处理（宁多报不漏）。温度爆炸只在活着时判（死 run 日志尾高温是历史残骸，首轮基线轰炸教训）。
- **唯一启动入口 `gmx_agent.py`（代码强制，用户拍板"要写到代码里不能提示词约束")**:v3.3 契约 `python3 gmx_agent.py input.json result.json`(py3.6.8 实跑验证）。硬校验四道：dir 必须在 enabled monitor root 下（扫不到=管不着，不许跑）/ tpr 存在 / 该目录无在跑 mdrun / loadavg1<核数。**先 adopt 原子写注册表再 spawn（启动即接管，无先跑后登记窗口）**;gmx 不在 PATH 可用 gmx_bin 显式指定。python3 本在 sim_exec 白名单 → 每次启动自动走审计通道，kit 一行未改。绕过入口的 mdrun 活不过一个 cron 周期（auto_kill_wild)。
- **建模过程上看板（用户:"建模过程也放进去，每一次模型改变都能看到，大小/原子数/bond/angle 逐一对照")**:run 目录认定从"有阶段日志"扩到"有建模产物(packed.xyz/.gro/packmol 日志）";补认 nvt.log（此前 27 个平衡段目录在看板上隐形——"看板没内容"的病灶之一）。卡片新增**流水线徽章链**（建模→grompp→min→平衡→prod，完成●/当前▶/未达○）与**模型统计行**：原子数/盒子 nm/键/角/二面角计数（解析最新 .gro + topol.top 的 #include 链，深度≤8 防环）;dir 里有 `model_spec.json`（期望值，如 `{"natoms":15000,"box_nm":[10,10,8],"bonds":…}`）则逐项 ✓/✗ 对照标红。last_modified 改跟建模产物 mtime——每次改模型看板即变。建模进程（packmol/grompp）判活只展示，**执法仍只针对 mdrun**(push driver 切到 mdrun_alive 判定）。活机实证：44 runs 上桌（8 prod / 27 nvt / 9 build)，模型统计真实解析（natoms 6592/box 4.06³/bonds 294/angles 544/dihedrals 828)。
- **明确不做**:gromacs 不进 kit 编排器状态机（它是 LAMMPS 专用；接管目前是"登记受监控"语义）;sim_exec 白名单不加 gmx（杀进程走既有 kill 白名单+弹窗）;deepmd 仍归 AIMDexp GPU 自家看板。

**回归门槛**:dsh python **388**(+40:sim_gmx_status 注册/返回/缺配置/零触碰；gromacs 推送跑完/中途停/爆炸/爆炸只在活时判/稳态/扫描失败；接管注册表缺失/命中/损坏/adopt 原子幂等；野跑告警一次不重复/已接管不报/auto_kill SIGTERM/无开关不杀/杀失败仍告警；gmx_agent 启动即接管/根外拒/重复拒/缺 tpr 拒/契约退出码/py3.6 语法/gmx_bin 兜底；建模 gro/top 统计/spec 对照/纯建模目录上榜/nvt 认/建模进程不触发执法/mtime 跟模型改动；自定义前缀阶段日志后缀识别/总步数取日志自带 nsteps/精确名优先/#备份#不误认/2018.1 换行能量表定宽列解析；点开详情字段全/越根拒/不存在拒） + node **29** 不动，kit **240** 不动。

**同日补丁(用户"我刚计算的东西呢""还有几篇呢"实例)**:
- **卡片可点开（"不能只显示，绑定和 lammps 一样的功能")**：新端点 `/api/gromacs_detail?dir=`(realpath 前缀校验防越根）+ 详情模态框——状态/流水线链/模型逐项 ✓✗ 对照/Step/进度/时长/温度/总能量/动能/压力/进程数/文件清单/当前日志尾 8KB，模态框跟随看板刷新周期联动（对齐 LAMMPS 单任务看板实时性）。卡片/模态共用顶层渲染助手（gmxStageChain/gmxCmpCell/gmxModelRows)。能量表解析新增压力列。
- **看板 GROMACS 段隐形病灶**:`#gmx-group` 原嵌在 `#task-list` 内且只在列表模式拉数据；无参打开被自动跳转单任务详情页 → 永远看不到。已移出容器 + 两种模式都拉取 + **置顶**(LAMMPS 休眠期 43 张历史卡会把它压到屏外）。活机实证：node 桩 DOM 灌真实数据渲染无异常。
- **扫描根改为 `文献-水凝胶/projects` 总根**（撤 paper-07 单独根防重复卡），下钻深度 2→3(paper-04 的 build/base 三级才够到）;12 篇 + Case_check 全上桌，活机 141 runs。paper-07 三个在跑 mdrun(s8prod/cprod 同 build 目录、m1_min 在 air1to1/M1_build）全部先 adopt 再上桌，执法闸零杀风险。
- **自定义前缀阶段日志**:`s8prod.log`/`vmin.log`/`m1_min.log` 等后缀识别（精确名优先，同级取 mtime 最新，`#x.log.1#` 备份不误认）；总步数新来源=阶段日志自带 Input Parameters nsteps（反映实际在跑的 tpr)，填 stdout 与 mdp 之间的空档。
- **能量表解析重写为 15 字符定宽列**:gmx 2018.1 按 5 列一组换行（Temperature 与 Kinetic En. 分行），旧单行正则漏抓 → 温度 null → 爆炸检测失明。新实现按列名索引取值，单行/换行两版通吃（两个真机日志实证）。
- **"卡片点不开"病灶=服务器单线程**:`HTTPServer` 串行处理，`/api/gromacs` 全扫 141 run 要 ~4s 而页面 3s 一刷——详情请求永远排在扫描后面饿死，模态框停在"加载中…"。改 `ThreadingHTTPServer`(handler 全无共享写，daemon_threads)+ `/api/gromacs` 5s TTL 缓存（扫描不再堆积）。CDP 真实浏览器驱动实证：点击→模态框 disp=block、详情 20KB 全渲染（此前同样手法复现了卡死）。
- **GROMACS 归档（用户:"计算过的程序类似 dsh 加一个归档")**：不活跃 run 全部收进折叠"📁 GROMACS 归档"区（仿 LAMMPS 历史记录组），主区只剩活跃卡；归档默认收起、点标题展开，归档卡半透明、同样可点开详情。活机：活跃 1 / 归档 146。
- **M2-NVT 新根 + run6 接管**:paper-07 用户在 `air1to1/M2_nvt/run6` 起的 prod(深度 5,projects 根深度 3 扫不到，看板与执法都看不见）——monitor_roots 加 M2_nvt 根（run1-6 上桌），run6 先 adopt 再上桌。教训：用户开新层级目录时，扫描根/深度要跟着走。

# dsh-agent v1.5.10+20260910 发布说明

全链分子模拟智能体（LAMMPS/ReaxFF）—— dsh CLI + Kimi K3 套壳版。

**许可证**：**PolyForm Noncommercial 1.0.0**（见 LICENSE）——开源可见、可修改、可非商用（个人/学术/教育机构），**禁止商业使用**，商用须另行获得版权人（AI4Science MD Team）书面授权。第三方组件声明见 NOTICE。

## 本版变化（相对 v1.5.9+20260909）

**看板新增 GROMACS 运行区（用户实证驱动:"看板 gromacs/deepmd 读不上"；扫描根配置化，换机不改代码）**

- **病灶**：看板主扫描树只有 LAMMPS 解析器+硬编码 case 根，用户实际在跑的 gromacs 水凝胶复现（/tmp/mdruns,29 个 run）在看板上完全不可见。
- **配置驱动**:`dashboard/monitor_roots.json`（每条 = path + type + label + enabled；`~` 自动展开；env `MONITOR_ROOTS_FILE` 可指别处；缺席/损坏/目录不在 → 静默空列表）。本机文件入 .gitignore,repo 只带 `monitor_roots.json.example`——**换机器/新目录只改这个文件，不动代码**（用户明确要求）。
- **后端**:`parse_gmx_log`（日志尾 64KB 取最后 Step/Time 块 + 能量表 Temperature/Total Energy，非有限值守卫）、`_gmx_total_steps`(`*_stdout.log` 头 `N steps, M ps.` 优先，`prod.mdp` nsteps 兜底）、`get_gmx_pids`(/proc cmdline 含 gmx+mdrun 且 cwd 匹配，与 get_lmp_pids 同纪律）、`scan_gromacs_runs`（深度 ≤2,run 目录 = 含 prod/md/npt/min.log 任一，阶段按此优先级；活跃优先+mtime 倒序）。新端点 `GET /api/gromacs`。
- **前端**：任务列表页新增紫色"⚗️ GROMACS 运行"分组卡片（run_id/阶段/Step/模拟时长 ns+pct/温度/总能量/PID/进度条/目录），列表模式与 /api/tasks 并行拉取，空则整组隐藏。
- **实证**：活机扫描 /tmp/mdruns 命中 29 run,gmx_mix/gmx_mix_coo 判活正确、pnipam 100% 判已完成；/api/gromacs 活端点返回与磁盘一致。
- **明确不做**:deepmd 不扫（它在 AIMDexp GPU 有自己的 8081 看板）；gromacs 区只读展示，不接编排器/不接审计写门；主扫描树（LAMMPS 事实源）一行未动。

**回归门槛**:dsh python **348**(+10:解析/缺文件/stdout 优先/mdp 兜底/扫描字段/缺配置/坏配置/禁用根/深度限/阶段优先级） + node **29** 不动，kit **240** 不动。修一处测试基建：discover 字母序使本模块先导入 web_monitor，预建 `dsh-home/` 目录避免下游 POST 测试崩 Handler。

# dsh-agent v1.5.9+20260909 发布说明

全链分子模拟智能体（LAMMPS/ReaxFF）—— dsh CLI + Kimi K3 套壳版。

**许可证**：**PolyForm Noncommercial 1.0.0**（见 LICENSE）——开源可见、可修改、可非商用（个人/学术/教育机构），**禁止商业使用**，商用须另行获得版权人（AI4Science MD Team）书面授权。第三方组件声明见 NOTICE。

## 本版变化（相对 v1.5.8+20260909）

**MCP 工具面精简 13→9（用户裁决，调用日志实证驱动）**

- **证据**:`events/toolcalls.jsonl` 63 次调用（08-29→09-08)——`sim_recent_ops`/`sim_strategy_log`/`sim_orchestrator_log` 各 1 次、`open_monitor` 4 次；核心六件（read_paper/pipeline_check/status/model_research/context/repair_research）占 54 次。
- **砍**:`sim_recent_ops`（与 sim_context 内嵌 recent_operations 纯重复）、`open_monitor`（看板用户自开，webbrowser 在 headless/ssh 本就半残）。
- **并**:`sim_strategy_log`+`sim_orchestrator_log` → `sim_context` 的 `section` 参数（state 默认/strategy/orchestrator；各自默认 n 不变），处理器下沉为 `_strategy_decisions`/`_orchestrator_log` 内部函数。
- **保留**:`sim_exec`/`sim_hpc_submit` 写门低频是设计使然（按次弹窗/人工闸），不以调用数论生死；`sim_review_packet` 保留为聚合只读面。
- **同步面**:headless patch yml 与 presets cordis yml 工具清单/Mode A 路由/Mode C 确诊段、dashboard 架构页工具层卡片、设计文档工具表与两处流程引用。
- **明确不做**:sim_review_packet 不并入 sim_context（它是跨 kit+dsh 演化层的聚合面，与 state 单文件读不同构）；旧副本 `agent开发/dsh-agent` 不动（非 live）。

**回归门槛**:dsh python **338**(+5:死工具下线断言/默认 state 节/strategy 节/orchestrator 节/未知 section 回落，全部红→绿实证） + node **29** 不动，kit **240** 不动。

# dsh-agent v1.5.8+20260909 发布说明

全链分子模拟智能体（LAMMPS/ReaxFF）—— dsh CLI + Kimi K3 套壳版。

**许可证**：**PolyForm Noncommercial 1.0.0**（见 LICENSE）——开源可见、可修改、可非商用（个人/学术/教育机构），**禁止商业使用**，商用须另行获得版权人（AI4Science MD Team）书面授权。第三方组件声明见 NOTICE。

## 本版变化（相对 v1.5.7+20260907）

**经验沉淀：软失败信号采集→证据晋升→看板轨迹（借鉴 teamai-cli friction→learnings 形状，用户拍板"经验沉淀很重要"；不引入组件，形状与 flowgate/pipeline 同构）**

- **friction 采集插件（新 `dsh-plugins/dsh-md-friction/`)**:dsh 宿主无 Stop/session-end hook,经 `session/event` 一条缝组合轮级摩擦——打断(turn/end reason=aborted)/工具错误(tool/result 带 error,留证前 5 个错误码)/审批否决(approval/decided rejected|cancelled)/LLM 重试(llm/retry)。每个 turn/end 组装一行追加 `events/friction.jsonl`(append-only,毫秒时间戳,写失败静默绝不影响主循环,logPath 解析与 flow-gate 同纪律:相对值锚 MDSIM_DATA_ROOT、逃逸回落默认)。turn/start 重置计数器防跨轮泄漏;turn 结束即释放 Map 防会话数泄漏。已注册 headless/tui/web 三 profile(link+symlink)与 setup.sh profile 生成块(新机器落地自动带)。
- **observe 消费(evolution/observe.py 第 3 观测源)**:三类软失败签名 `friction-interrupt`/`friction-tool-error`/`friction-denial` 窗口聚合,≥min_occurrences 各晋升 **human 层提案**(软失败涉及交互/权限判断,绝不自动改),接 RSI 关键词检索(新三签名配词)与训练记忆 record_atom 复发溯源;每类签名入 summary patterns。`mcp/paths.py` 新增 `friction_log()` 路径单点。
- **交付标准补两条分析纪律**(`docs/文献复现交付标准.md` §3,paper-04 GROMACS 复现会话复盘驱动):①欠约束项强制"结论翻转风险:高/低"评级,高风险项预登记裁决实验(只改该参数的对照 run),默认值必须能被追问出"为什么是这个值";②差异型结论强制三档噪声地板标注(>3×显著/1–3×提示/<1×不显著),估不出噪声地板的分析脚本不许下差异结论。演化层燃料节同步点名 friction.jsonl。
- **看板每任务轨迹(新端点+新页面)**:`/api/trajectory?task=<case_id>` 把 pipeline 事件流(按 case_id)+ orchestrator 日志(消息命中)+ 策略决策(整行命中)串成一条时间线;`?session=<id>` 给 friction 轮级轨迹;无参返回会话摩擦总览(按 session 聚合:轮数/打断/工具错误/否决/重试/最后活动)。只读组合既有日志,尾部 ≤2MB 扫描,缺文件/坏行降级为空永不抛。新页面 `/pages/trajectory.html`(会话总览点击进轮级轨迹,30s 自刷,全字段 esc),workbench 加「🧭 轨迹」入口,index 任务卡每条加「轨迹」链接。
- **明确不做**:不自动批准任何东西(人闸不动);不改动 dsh 宿主 vendored 代码(插件走 cordis 公开事件缝);④高影响欠约束点并行枚举走提案→用户闸队列而非自动派发;⑦常驻红队 agent 不做(红队是流程不是角色)。

**回归门槛**:dsh python **333**(+5:friction 晋升 2/trajectory 端点 3) + node **29**(+5:friction 插件计数/打断/重置/crash-tail)全绿、kit 240 不动。冒烟:`/pages/trajectory.html` 与 `/api/trajectory` 200;profiles 三链接解析实证。

# dsh-agent v1.5.7+20260907 发布说明

全链分子模拟智能体（LAMMPS/ReaxFF）—— dsh CLI + Kimi K3 套壳版。

**许可证**：**PolyForm Noncommercial 1.0.0**（见 LICENSE）——开源可见、可修改、可非商用（个人/学术/教育机构），**禁止商业使用**，商用须另行获得版权人（AI4Science MD Team）书面授权。第三方组件声明见 NOTICE。

## 本版变化（相对 v1.5.6+20260907）

**看板 adopt case 详情白屏修复（live 用户报告驱动）**

- **根因**:adopt 接管的外部运行 case_id 与工作目录名不一致（如 `gentle_compress_tol1e6_cont` vs `gentle_compress_6000_20260630_070000`)，看板 `scan_tasks` 按目录名建索引 → `/api/status?task=<case_id>` 404 → 前端 `d.step.toLocaleString()` 抛异常 → 详情面板整屏留白。
- **修复**:`resolve_prod_dir` 兜底（case_id 精确匹配 orchestrator 当前任务时直接用 state 里的真 `prod_dir`，真不存在的 case 仍 404);`is_current` 改按 `realpath(prod_dir)` 认亲，"当前管理"徽标贴对卡片；前端加 404 守卫显式报"任务不存在"不留白屏。**kit v3.3 与 dsh-agent/dashboard 两副本同修**(live :8080 跑 dashboard 副本）。
- **新定稿 L2-22（实现期自查两条，24 轮复盘同类错立规）**：竞态窗必答（读→处理→写回序列先想并发追加）；禁隐式 fail-open（读失败显式三选一：fail-closed/沿旧值/注释理由）。
- 活机实证：adopt case 详情 404→200 数据齐全，徽标贴对；web_monitor 带 env 重启。

**回归门槛**:dsh python **328** + node 24、kit **240**(+4：兜底命中/未知 case 仍 404/is_current prod_dir 认亲/前端守卫源码锚）全绿。基线文档执行记录 24-补。

# dsh-agent v1.5.6+20260907 发布说明

全链分子模拟智能体（LAMMPS/ReaxFF）—— dsh CLI + Kimi K3 套壳版。

**许可证**：**PolyForm Noncommercial 1.0.0**（见 LICENSE）——开源可见、可修改、可非商用（个人/学术/教育机构），**禁止商业使用**，商用须另行获得版权人（AI4Science MD Team）书面授权。第三方组件声明见 NOTICE。

## 本版变化（相对 v1.5.5+20260907）

**借鉴 LoopX 三形状优化（用户拍板：借鉴设计形状，不引入组件）**

- **用户闸队列（kit）**：新增 `harness/core/user_gates.py`——待人工判断项持久可审（`.v3.3_user_gates.json`)，不再一条飞书即消散。四类闸：breaker_terminal（修复链终态 FAILED)/wild_run_adopt（野跑待接管、adopt 被拒）/evolution_proposal(dsh human_review 提案）/manual。**单写者纪律**:仅 orchestrator 写队列；dsh 只向 append-only 收件箱 `logs/user_gate_requests.jsonl` 投请求行，orchestrator 每 tick 扫箱（(kind,ref) 精确去重入队/销项，**rename 原子接管后批量消费**——无读-截断竞态窗，单次≤200 行剩余回投，新闸聚合一条飞书）。**收件箱按不可信源处理**：入队来源强制 "dsh"，收件箱 resolve 只许销演化提案闸（kit 原生闸只能走 kit 代码路径）。销项标记不删除；队列损坏先隔离 `.corrupt.<ts>` 再回退，不静默抹审计史；已告警的入队点不重复飞书（防轰炸）。adopt 成功自动销对应野跑闸（realpath 全链归一 + kind 过滤）。state 旗标 `_user_gates_enabled:false` 可整关（回退路径）。
- **should-run 裁决（kit，纯咨询）**:orchestrator 每 tick 顶部 `_should_run()` 输出 verdict(ACT/IDLE/WAIT_HUMAN/SUPPRESSED)+理由+证据，节流入策略账本（迁移即记，稳态 1/hr)。**红线：永不 block dispatch**——熔断逻辑原样不动，SUPPRESSED 时检测子相位照常（L2-17 不松）。
- **observe should-run 闸（dsh 演化层）**:`observe.py main()` 开头 `_should_observe()`——距上轮 < POLICY `observe_gate.min_interval_minutes`（默认 30)跳过；kit 卡 FAILED 且有 pending 闸 → 等人工不空转；kit state 缺失放行、损坏 fail-closed;`--force` 人工越过。裁决行追加 `events/pipeline.jsonl`。
- **提案→队列桥（dsh）**:`draft_proposal` tier=human 自动投 enqueue 请求（桥失败只告警，起草永不被阻塞）;`apply_proposal` 批准成功后投 resolve 回执（销项，无批准逻辑新增——批准仍只走 --approver 人工通道）。
- **sim_review_packet(dsh MCP)**：新只读工具——一屏紧凑复盘包（相位+should-run 裁决/熔断/待人工闸/管道事件尾/提案与 LEDGER 账），分节容错永不抛，只组合既有真相源零新写入。
- **推送露出**:md_push_driver 检测新增 pending 闸 → 微信严重告警（销项不报；闸文件读失败沿用旧基线，不爆发假"新闸")。
- **活机实证**：收件箱投请求行 → orchestrator 扫箱入队 ug-20260907-1 → should_run 迁移 IDLE→WAIT_HUMAN → 投 resolve 行销项 → 回 IDLE;sim_review_packet stdio 冒烟六节齐全。
- **测试污染洞再焊两处**（同 KB_PATH 型）：新写路径（user_gates 队列、dsh→kit 收件箱）模块常量全部沙盒化；真实队列/收件箱曾被测试写入 13+2 条假闸，已清（备份 `.bak_20260907_testpollution`)，全门跑完真实文件零写入实证。
- **observe 闸加固**：闸文件原子写；`min_interval_minutes` 浮点（0.5 不被截 0)；崩溃留 FAIL 裁决事件且不白占节流窗（成功后盖章）;kit FAILED+闸文件损坏 fail-closed 跳过；事件 ts 数值化与 pipeline 事件流同 schema。

**回归门槛**:dsh python **328**(+26:observe 闸 12/闸桥 5/review_packet 8/push 1) + node 24、kit **236**(+23：队列 15/verdict 8）全绿。基线文档执行记录第 24 轮+新定稿 L2-20(should-run 纯咨询纪律）/L2-21（用户闸队列单写者）。

**第 24 轮多智能体对抗审计（三代理：kit/dsh/基线一致性）已实现后复审**:14 项发现（高4 中6 低8）全修带回归——收件箱读-截断竞态吞请求行、收件箱 resolve 跨类自批准旁路、推送闸基线读失败假爆发、adopt/野跑路径口径不一致、ref=None 通配吞队、请求风暴拖 tick、损坏队列抹审计史、observe 闸 FAILED 态 fail-open、崩溃白占节流窗等；复攻脚本复验真修全有效（残报均为旧契约假阳性，逐条判读留档基线文档）。红线零冲突：verdict 永不拦派发、熔断只压恢复（L2-17)、证据门逐字节不松、批准仍只走人工通道。

## v1.5.5+20260907 内容

**文献复现交付标准落地（Mode B 合规判据代码化，用户拍板"不能打折扣"）**

- **新文档 `docs/文献复现交付标准.md`**：Mode B（read_paper → 建模 → 动力学 → 分析）每次交付的强制标准——§0 多过程拆分（一过程一模型，阶段表显式 write_data→read_data 衔接，不许糊进一个 in 脚本）；§1 论文模拟信息全对照表（每条信息指出模型体现位置，精确到阶段号+文件+行；论文未报的参数标"取默认值 X（依据）"不许留空）；§2 体系汇总（总体一段+按阶段各一段+显式 M1→M2→M3 链）；§3 指标对比（每指标标注出自哪个阶段产物；任一主要指标显著偏离→转带参照失效样本进演化层燃料，不许润色交付；**上游阶段指标未判 ✓ 下游不许开跑**——偏差来源可归因）。
- **read_paper 工具描述接线**（`mcp/md_mcp.py`)：三条交付红线嵌入工具描述，Mode B 每次读文献即见标准。
- 与演化层闭环：失效签名进五根因桶（thermal_runaway/QEq_instability/atomic_ejection/structural_collapse/input_error）或"物理偏差"，同签名 14 天 ≥4 次触发 L2 起草；pipeline CRITICAL 经 `events/pipeline.jsonl`(v1.5.4）进 observe 复发统计。

**回归门槛**：dsh python **302** + node **24**、kit **213** 全绿（本版为文档+工具描述变更，无行为变化，门槛数不变；跑门实证全绿后记账）。

## v1.5.4+20260905 内容

**pipeline 观测源升级事件流（复现跑量前硬约束）+ 测试污染生产 KB 洞焊死**

- **事件流**：`pipeline_check.run()` 改单出口封装（`_run_impl` + `_emit_event`)，每次巡检终态——含此前从不落盘的 PIPELINE_ERROR / NO_LOG——追加 `events/pipeline.jsonl`。旧覆盖式 `snap_<case>.json` 会把失效冲掉（case 从 CRITICAL 恢复后失效记录即消失），快照保留仅供 md_mcp 修复研讨读"每 case 最新";observe 第 2 观测源改读事件流（distinct case 面状语义不变，同 case 恢复后再崩跨窗可见），旧 `mcp/_pipeline` 快照留迁移期只读兜底。事件写失败静默，绝不阻断巡检主链。
- **测试隔离修复**:v1.5.3 给 canary 横扫新增 KB 剧集写入时，四个老测试类（TestObserve/Kit/Specialist/E2E）未同步沙盒 `specialist_swarm.KB_PATH`，每轮回归门向真实 `repair_kb.jsonl` 漏 ~12 条虚构剧集（sid G promote/demote)——会回灌蜂群 question 误导联网研判。已补沙盒、清空污染（备份 `repair_kb.jsonl.bak_20260905_testpollution` 保留）、全门跑完真实 KB 零写入实证。

**回归门槛**:dsh python **302** + node 24、kit 213 全绿（新增 7：三类终态入账/写失败不阻断/覆盖后 CRITICAL 仍计入锚/distinct 口径锚/窗口过期/stdio 端到端）。基线文档执行记录 23-补。

## v1.5.3+20260905 内容

**耗尽熔断 + 蜂群研究驱动 L2 演化 + 三条工程修复（live 事故驱动，用户拍板蜂群红线）**

背景：live 事故三连——orchestrator 对死跑每 ~2.5 分钟空转一轮 "All strategies exhausted"（无退避硬转）；recover_agent 硬编码 `production.in`（adopt 接管 case 用 `production_tol1e6.in`，修复会打错文件）；status_bus 不反映 PID 死亡（死跑仍显 RUNNING）。用户拍板新方向：策略耗尽 → 多智能体蜂群联网搜索 → AI 判断 → 更新策略库+知识库，全部代码层强制（不写提示词），走 L2 演化。

- **recover 输入源贯通（kit）**：`recover_agent` 新增可选参数 `input_file`/`output_log`（默认 `production.in`/`production.stdout`，默认路径行为逐字节锚定）；basename 校验拒路径穿越；orchestrator 从 task 透传（adopt case 修复打对文件）；output_log 非默认时 `>>` 追加（保住被接管跑的历史日志）。
- **耗尽熔断（kit）**：recover 耗尽返回结构化 `"exhausted": true`（严格 `is True` 判定，非布尔不跳闸）；orchestrator `recovery_breaker` 状态——strikes 退避 `30min×2^(n-1)` 上限 6h，开闸期间 DECIDE 压制恢复转移（节流日志 `breaker_suppressed`，不空转）、**只压恢复不压检测**（死进程判定照常）；strikes>5 → FAILED 终态+飞书转人工；修复成功即清零，退避到期自动恢复。
- **bus DEAD 裁决（kit）**：注册 pids 全死 + monitor_status≠DONE → bus status `DEAD` + 一次性飞书（`_dead_notified` 防重发，复活清旗）；熔断停泊期间死跑如实显示 DEAD。
- **蜂群研究驱动起草（dsh 演化层）**：策略库耗尽触发 `evolution/specialist_swarm.py` → 调 `mcp/model_research.py` repair 流水线（拆题→4 路并行联网收集→量规评审→代码终裁）→ 产物**确定性映射到模板注册表**（match_terms 计分 verified 权重×2 + param_patterns 正则→网格参数值，映射表人工交付）；低置信回退首参（与盲选逐字节等价锚）；蜂群对全部模板零命中=新机制 → 自动起草 `swarm-research-<cause>` human_review 提案（附研究报告，模板永远人工交付）。**红线零松动**：蜂群不能起草 off-template 规则，apply_proposal 证据门"条目==instantiate 产物"逐字节比对原样通过（测试实证：picked 参数过真门 AUTO_APPLIED；off-grid `9.9e-9` 越狱注入被门拒 REJECTED）。
- **知识库闭环**：`evolution/rsi_knowledge/repair_kb.jsonl` 追加式剧集（swarm_research + canary_outcome 结局）；同根因近 3 条剧集回灌后续蜂群 question，构成"更新知识库"闭环。节流双闸：同因冷却 24h + 全局日 4 次（POLICY 闸，删 `swarm_research` 条目即全关）；KB 不可读 fail-closed 不跑（省费用）；蜂群永不抛，任何失败回退盲选（起草永不被蜂群阻塞）。
- **live 实证**：事故 case `gentle_compress_tol1e6` 经 adopt 注入（log.tol1e6 / production_tol1e6.in），2.5 分钟空转消失、监控盯对日志、bus 状态如实；旧 case pending 策略 C 诚实结账 fail（不投毒 Beta 后验）。

**回归门槛**：dsh python **295** + node 24、kit **213** 全绿（新增 37：kit 21=输入源/遍历拒绝/exhausted 旗标/熔断退避表/非布尔不跳闸/DECIDE 压制/死+熔断停泊/bus DEAD；dsh 16=模板计分/参数命中/零命中回退/新机制 human 草稿/节流/KB 坏行容忍/picked==instantiate 锚/observe 蜂群失败逐字节等价）。新定稿 L2-16~L2-19 见 `docs/对抗基线_ADVERSARIAL_BASELINE.md`。

## v1.5.2+20260904 内容

**外部运行接管（adopt）+ 野跑检测器（kit 编排器，用户指令触发）**

背景：会话侧经用户授权 bash 直启 LAMMPS（绕过 sim_exec 审批弹窗）时，任务游离于 v3.3 状态机之外——status_bus、看板、自愈链全盲（"野跑"）。用户拍板纪律：审计门可经用户指令绕过，但**托管不可跳过**。

- **adopt 接管路径**：任务带 `adopt:true` + `prod_dir` + `log_file` 注入 `.v3.3_state.json`（phase IDLE）→ `_handle_adopt` 直转 MONITOR，不重跑 PREFLIGHT/VALIDATE/LAUNCH；/proc 自动发现该目录存活 lmp 进程接管看门狗；`log_file` 显式指定解决外部运行非标准日志名（如 log.fullrun）被目录内陈旧 `production.stdout` 顶包的问题。**fail-closed 四拒**：目录缺席 / log_file 路径穿越 / 指定日志缺失（笔误守卫）/ 无存活进程，一律 FAILED 不接管。非 adopt 任务行为逐字节不变（回归锚钉死）。
- **野跑检测器（代码强制，检测强制、接管人工）**：orchestrator 每 tick 挂钩、每 300s 扫 /proc 全机 lmp 进程，cwd 不在在管 prod_dir 即判野跑 → 飞书告警 + 策略账本 `wild_run_detected` + state `_unmanaged_runs` 持久可见；只在新增目录时告警防轰炸；DONE/FAILED 相位旧目录残留进程同判野跑。不自动接管（外部运行 total_steps/温度程序未知，健康判定会瞎）——发现后走 adopt 收编。
- **活机实证**：当日 bash 直启的 `gentle_compress_full_stage1` 经 adopt 收编，监控/瞬态尖峰识别/预防性修复链（策略耗尽优雅放弃）/看板登记全链生效；扫描器上线即在岗，在管 case 零误报。

**回归门槛**:dsh python 279 + node 24、kit **192** 全绿（adopt 7 + 野跑 7 新增，含真 /proc 集成与行为不变锚）。

## v1.5.1+20260904 内容

**L2 证据晋升流水线对抗加固（第 21 轮追审，中 2 低 4 全修）**
- **ReDoS 启发式补强**：嵌套量词识别扩展到 `{m,n}` 花括号形态（`(a{2,}){2,}` 类灾难模式install 门前拦截）；合法花括号（`\d{1,7}`、`(abc){1,2}`）不受影响。
- **策略库顶层键锁死**：auto/evidence 提案除授权面外不得增删或改写 db 顶层键（堵 install 顺手夹带 `backdoor_top` 类走私）；auto 唯一豁免=创建缺失的 `strategy_adjustments` 键（老库兼容）。
- **禁闭比对升级 `_canon` 规范化文本**：True≠1,堵 bool/int 混淆穿透"逐字节"承诺（JSON 0.5/0.50 语义等价，记接受项）。
- **rollback 24h 时限 fail-closed 补齐**:ledger ts 在未来（时钟回拨/伪造）负年龄不再绕过 R6 闸。
- **沙盒网络封堵到 socket 层**：试跑期 `socket.socket`/`create_connection` 一并封死（finally 恢复）；越界写快照仅覆盖 tmp 树记接受项。
- **测试纪律**:legacy 六策略黄金输出逐字节锚补齐（delete_overlap/langevin_damp/nve_limit/reduce_heating 四支新增）,防后续漂移无拦截。
- **全新部署图谱兜底**（打包冒烟抓到）：无 `.v3.3_strategy_db.json`（全新大脑）时知识图谱不显示策略节点、研判笔记「采用」边断——现按 kit `load_db` 语义回退默认六策略，单一事实源不复制表。

**回归门槛**:dsh python 279 + node 24、kit 178 全绿；55 历史攻击脚本复攻 52/55(3 已知接受）零回退。定稿修订见 `docs/对抗基线_ADVERSARIAL_BASELINE.md` L2-4/L2-10/L2-12/L2-15。

## v1.5.0+20260903 内容

**L2 结构自进化：证据驱动自动晋升流水线（试点 new_specialist_script，经第 20 轮多智能体对抗审计）**
- 演化层从"只能自动调参数"升级为**自动造新恢复策略**：某根因 14d 窗内 settled fail≥4 且标准工具箱全被压制/连败、且无活 canary 无未过期墓碑时，`observe_kit` 自动按根因匹配模板起草 install 提案（slug `specialist-install-<cause>-<pair哈希>`,同日去重/未应用即刷新/已应用绝不动）。
- **载体=DSL 规则（用户拍板，非生成 Python）**：策略条目携带 `{match, replace, guard, max_output_delta}` 规则，演化通道永远只写数据（`.v3.3_strategy_db.json`）不写代码；通用执行器 `kit/.../specialist_rules.py` + 模板族 `evolution/specialist_templates.py` + 沙盒 `evolution/specialist_sandbox.py` 由人工一次性交付并列入 forbidden_paths（通道永不可碰，考题不可能被考生篡改）。规则安全：白名单键/flags⊆{I,M,S,X}/count∈[1,20]/ReDoS 启发式（嵌套量词+交替量词+`.*`≤2)/`\N`+`\g<>` 反引用全审计/幂等性+空/垃圾/超长探针。
- **证据门（POLICY `evidence_classes`，无需人工批）**：install=内容禁闭（strategies/history 逐字节不动）+ 血统校验（条目与模板 instantiate 产物逐键一致，杀 off-template)+ `_install_still_needed`（按当前盘重算耗尽，治好不装）+ 沙盒试跑由门子进程重跑（提案自附报告只作审计，杀自证）；promote=仅删 canary 标记 + 统计按当前盘新读 db 重算（TOCTOU 纪律）；demote=正向 diff 删键 + 配套 auto 类墓碑提案（penalty 封顶，冷却 7d 防立即重起草，墓碑解析 fail-closed)。approver 仅记录不豁免；落盘后按（备份， 盘上）重放全部断言，不过→ROLLED_BACK。
- **金丝雀兜底语义**：新策略 `canary:true` 入库（truthy 即 canary，畸形真值 fail-closed),`select_strategy` 两轮制——非 canary 优先，标准工具箱尽没才选 canary（无 canary 库时行为与接入前逐字节等价）；研究推荐 canary 一律压制回退打分。
- **实账自动晋升/降级**：canary settled 满 4 账 3 胜且 posterior≥0.5 → 起草 promote;3 连败（demote 优先，3W→3F 反转序列永不晋升）或 30d 零结账饿死（锚点=首账 ts 或 LEDGER install ts)→ 起草 demote+墓碑。阈值全在 POLICY.json 可人工调。
- **人工保留**:POLICY 修改权（删 evidence_classes 条目=总开关全关)、`--rollback`(db 条目 >24h 拒整文件还原，引导正向修正，L2-R6)、LEDGER 全审计（行带 op/specialist_id)。物理正确性不可机检——靠兜底语义+人工事后审 PROMOTED 行兜底。
- **kit 侧连带加固**:apply 零改动=no-op 判失败记账（防 Beta 后验投毒），备份延迟到有真实改动才建；legacy 名+rule 拒、未知策略名显式 False;`_pick` 畸形条目防护。
- 对抗验证：四代理审计高 4 中 8 低 4 全修（复现→红→修→绿）;E2E 双环（耗尽→起草→证据安装→4 账→promote / 3 连败→demote+墓碑→7d 抑重起草）34/34;复跑前轮全部脚本零回退。定稿纪律 L2-1~L2-14 见 `docs/对抗基线_ADVERSARIAL_BASELINE.md`，报告与脚本归档 `docs/对抗验证_20260903_L2证据晋升/`。

**回归门槛**:dsh python 274 + node 24、kit 173 全绿。

## v1.4.0+20260903 内容

**CORAL 借鉴:环内反思与智能体手写知识**
- **pivot 心跳**：进 RESEARCH 前把该根因下"已连败/被演化压制"的策略显式注入研判层（`_pivot_context`,penalty>0 或尾部连败 ≥2 即触发,环内即时可见、不等 37 分钟 observe 周期）;research_agent 推荐命中压制名单时强制换路——改荐备选、置信度压 0.55 以下交打分层终裁,reasoning 留 pivot 痕迹。
- **RESEARCH notes**：每次根因研判结论落 `mdsim-data/memory/notes/`(markdown,首行 `<!-- JSON -->` 机读头),atoms 记"发生了什么",notes 记"为什么";图谱新增「研判笔记」节点(按结果着色,边:研判于 case / 采用策略)。

**进化程度量化(📈 指标页)**
- 新增 `/api/evolution_metrics` + `/pages/metrics.html` 记分卡:恢复胜率/一次修复率/同因复发率/恢复耗时中位(kit 新补 `settled_ts`)/后验置信度/偏离先验度/提案采纳率/知识规模,七维归一化合成**进化指数(/100)**;近 30 天分桶趋势线,全部由既有学习数据算出,缺数据降级「—」。

**学习层消融对照(论文级因果证据)**
- `experiments/ablation_replay.py`:真值矩阵(启发式匹配分认不出最优策略,差异只能来自学习)+ 配对回放(同 case 序列同随机数喂两臂)双臂对照——学习开 vs 学习关(历史账簿全清空,防后验从 `_recompute_rates` 后门溜回,首轮实证抓到该泄漏)。
- seed 42 × 300 case:恢复胜率 **86% vs 73%(+13pp)**、平均尝试 **1.21 vs 1.51**、一次修复率 **79% vs 49%(+29pp)**;6 种子复验稳定(+10~18pp)。
- 纳入回归门 `tests/test_ablation.py`:学习臂不占优即红。

**看板 AI 决策过程图(archify + 实时相位)**
- 🧠 AI 决策过程(30 秒决策环)workflow 图:感知/研判/行动三泳道,OBSERVE→ANALYZE→DETECT→EVALUATE→DECIDE 主链 + 升级路径;iframe postMessage 实时辉光当前子相位,异常升级红色「⚠ 升级」徽标。

**试运行验收相位(VALIDATE,先测试性跑通再上生产)**
- kit 状态机新增 VALIDATE 相位(PREFLIGHT 过→VALIDATE→LAUNCH):`validate_agent` 把生产输入截到首个 run、强制固定热力学列短跑 N 步(默认 10,`task["validate_steps"]` 可调,最多 4 核不抢生产),按固定六项清单验收——完成收尾/无 ERROR/原子守恒/无 NaN/步数到位/温度不越程序最高温 4 倍;**全过才放行 LAUNCH**,不过落 FAILED + 飞书,报告留 `<prod_dir>/validate_report.json`(validate.in/stdout 可复查)。`task["validate"]=False` 显式直通(老任务兼容)。

**SCNet 国家超算互联网提交通道(2026-09-03 真机联调通,人工确认代码强制)**
- 新增 `scripts/scnet_client.py`(纯 stdlib):AK/SK HMAC-SHA256 签名认证 → 每区域 token(JWT 本地解析过期,临期整体重认证,缓存 chmod 600)→ 授权区域/集群/队列发现 → 文件上传(建目录+multipart) → 提交作业 → 实时作业查询 → 删除作业,CLI 八子命令(auth/regions/clusters/upload/submit/approve/query/delete)。
- **真机联调结果**:区域 华北二区【太原】(clusterId 20027,家目录 /work/home/<user>),SLURM 调度器 1697599991,可访问队列 tyhcnormal(4282 节点,64 核/节点);实测修正三处文档口径——/center 必须用区域 token(平台 token 报 10001)、hpcUrls 不带 /hpc 前缀(efileUrls 带 /efile,`_svc_url` 归一化)、efile mkdir 的 path/createParents 必须走 query 参数(JSON body 报 10003)。
- **真机冒烟通过(2026-09-03)**:dry-run 出 plan_hash → 终端 approve → 提交 sleep 作业(8663185,statR 落 c04r1n24)→ delete 终止(statC),审计链 dry_run→approved→submitted→deleted 完整。
- **双闸门**:① md_config `scnet` 段 enabled+auto_submit+白名单(队列/节点/墙时,越界拒绝零外发);② **人工确认代码强制**(用户拍板,不依赖提示词)——真提交须一次性批准文件(终端 `approve --plan-hash` 开具,哈希绑定提交参数,30 分钟有效,用后销毁)或 TTY 内联输入 YES;agent 子进程无 TTY 两条路都开不出,只能 dry-run 出计划。每次尝试(含被拒/批准)记 `events/scnet_submit.jsonl`。
- 远端目录约定 `<homePath>/YMY-AI/<case_id>/`(用户拍板)。凭证在 `<数据根>/dsh-home/.credentials.yaml`(SCNET_USER/ACCESS_KEY/SECRET_KEY),绝不进代码库。闸门 enabled/auto_submit 已开(冒烟用),真 MD 作业还差 `lmp_cmd`(集群 LAMMPS 加载命令);每次提交仍须人工批准,与闸门状态无关。

**回归门槛**:dsh python 189 + node 24、kit 120 全绿。

## v1.3.0+20260902 内容

**演化层接入 kit 恢复策略库（真学习，2026-09-01，经九轮多智能体对抗审计）**
- kit 恢复策略从"假学习"（success_rate 恒 0.5、history 空写）升级为**真实结果归因**：`record_attempt` 记账 pending、`settle_pending` 统一判负规则（DONE 全判 success，RESEARCH 同因复发/FAILED 判 fail）、Beta(1,1) 后验回写 success_rate（近 20 条已结账窗）。
- 演化层新增 `observe_kit()` 事件源：cron */37min 驱动，已结账结果记入训练记忆 atoms；同 (策略,根因) 尾部连败 ≥2 自动起草权重压制提案（auto 类，白名单单文件 ≤8 行，apply_proposal 双层门禁 fail-closed + LEDGER + 备份链可 `--rollback`）。
- **单写字段纪律**：kit 自写 strategies/history，演化通道只写 `strategy_adjustments`（penalty ≤0.6 封顶），`select_strategy` 打分减 penalty——无写竞争，演化贡献显式可审计。
- db 并发安全：fcntl 锁内 read-modify-write + tmp/os.replace 原子写；history 封顶 200 条，近未来时间戳容忍 ±1h。
- 回归门槛：kit 84、dsh python 151 + node 24 全绿；对抗基线与验证报告见 `docs/对抗基线_ADVERSARIAL_BASELINE.md`、`docs/对抗验证_20260901_kit演化层接入/`。

**archify 文档级流程图（看板可视化）**
- vendored 零依赖 archify CLI（`tools/archify/`），产出三张 showcase 级流程图挂入看板 `/pages/`：📊 管线（workflow）、🛠 恢复（kit 状态机 lifecycle）、🧬 演化（数据流），工作台顶栏直达。

**知识关系网（/pages/graph.html）**
- 新增 `/api/graph` + 零依赖 canvas 力导向图：atom 观测 → 场景 → SOP/提案 → 恢复策略 → 演化压制全链可视化；**case 作业节点**自动探测"跑的什么"（data 文件/力场/原子数/阶段步数/最新进度，registry + prod_dir 输入脚本解析）。
- 底部**时间轴回放进化过程**：节点按发生时间逐个浮现，可拖滑块定位任意时刻；图例按类型过滤、点击节点看详情（fact/样例/refs/LEDGER 原因）。

## 本版变化（相对 v1.1.0+20260829，即 v1.2.0 内容）

**运行时数据根外置（核心架构变化）**
- 全部运行时数据（事件流、注册表、演化记忆/提案/备份/LEDGER、流水线快照、推送状态、dsh 会话与凭证）迁出代码目录，集中到 `<代码根上一级>/mdsim-data/`；`MDSIM_DATA_ROOT` 环境变量可覆盖。代码目录变为纯代码，发布零 exclude、零数据件、零凭证。
- 新增 `mcp/paths.py` 单一解析源（env > md_config.json["data_root"] > 默认父目录）；所有 Python 读写点（md_mcp/pipeline_check/observe/memory/apply_proposal/md_push_driver/model_research）与 JS 侧 flow-gate logPath（env 锚定，cwd 兜底病灶根除）全部切换到运行时解析。
- DSH_HOME 迁至 `<数据根>/dsh-home/`（会话+凭证是最敏感数据，随数据根走）。

**看板注册表 + 超算（HPC）最小闭环**
- 新增 `state/registry.json` 作业注册表（flock+tmp+os.replace 原子写）：作业登记 case_id/project/prod_dir/log_file/log_format/pid_source/location；看板改为**注册表优先 + 目录扫描兜底**（按 prod_dir 去重，未注册旧作业仍可见）。
- 新增 `scripts/register_job.py` CLI；kit 侧 orchestrator 启动作业时自动登记（失败只 warn 不阻断）。
- 日志解析升级为**表头自适应**：按 LAMMPS 热力学输出列名映射（不同 case 列序不同也能正确解析 step/temp/能量/压强/体积/密度），兼容 7/8 列格式。
- 新增 `dashboard/hpc_collector.py`：远端作业经 ssh 周期采集 squeue/sacct（slurm）或 qstat（pbs）状态 + 日志尾部拉回本地缓存，看板显示"远端作业"徽标与调度器状态；ssh 失败静默降级"远端不可达"，绝不影响本地条目。`hpc.enabled=false` 为人工闸（红线不变，hpc_submit 仍只渲染不提交）。
- 作业/事件带 project 字段（项目维度，schema 原生支持）。

**setup.sh 收敛**
- 换机器引导从六组正则收敛为：md_config.json 四字段（缺失时从 .example 模板播种）+ persona 内嵌路径改写 + POLICY roots + dsh-home 播种（settings/auto-approve/预设/凭证模板 chmod 600）+ profile 生成（package.json 含 dsh.profile.bundles、pnpm-workspace.yaml hoisted、cordis.yml/patch）+ 依赖安装（--ignore-scripts 规避 EL8 python3.6 假死）+ node-pty 显式重建 + 品牌补丁 + 回归。
- 收敛后经**真实临时目录全新安装演练**验证：从克隆到起服全通。

**终端交互模式（tui profile，2026-08-30 新增）**
- 新增第三种运行形态：`./run-dsh.sh --profile tui` —— 终端原生交互界面（deepseek-harness-tui，与 harness rc.6 peer 精确匹配），保留终端 scrollback 可回看复制，支持 steering/会话恢复/行内审批/Plan 模式。
- 与 web/headless 同口径：叠满三个安全插件（ops-gate/auto-approve/flow-gate），默认预设 md-sim-assistant 人设、七条红线、MCP 只读工具链全部生效（身份/红线/流程门探针实测一致）。
- 启动器对 tui 自动补 `--model`/`--preset`（解析 settings.yaml 的默认路由与默认预设，填平上游两处缺口，用户显式指定不覆盖）。

**全品牌统一 MD Exp + persona 全链重定位（2026-08-30，用户拍板）**
- `scripts/apply-branding.py` 扩展：TUI 欢迎屏鲸鱼象素画 → "MD 小精灵"象素画（粗体 MD 字母：M 凹槽大方块眼 + ·小腮红点 + ▀小嘴，D 右侧两条游动摆动姿态小尾巴，MD 下方上标小标 ᴱˣᵖ；网格小 5x12/大 6x14，宽不变、高 +1 行给小标），字标 "DEEPSEEK HARNESS" → "MD MOL SIM"（纯英文），终端窗口标题 → "MD Exp"；CLI `--help` 描述、系统提示尾注、Web 设置页公告/FAQ 等全部运行时 "DeepSeek Harness" 文案统一替换（npm 包名、DEEPSEEK_API_KEY、update-check 包名等功能标识符不动）。
- persona 从"运维助手"重定位为**全链分子模拟智能体 MD Exp**：建模与力场选型 → 输入准备 → 作业提交（HPC 提交永远人工确认，红线不变）→ 运行监控 → 故障诊断修复 → 结果分析与报告，全链路的判断与决策由 agent 负责；三份 persona 副本（headless patch / presets / dsh-home .agent-presets）逐字同步，七条红线语义不动；新增固定开场白脚本（分子模拟助手四段式：建模怎么弄 / 计算力场选型 / 动力学怎么弄 / 讨论结果怎么分析，新会话第一轮逐字使用）。

**演化层接入 kit 恢复策略库——真学习上线（2026-08-30）**
- kit 恢复策略从"假学习"（success_rate 恒 0.5、history 从不写）升级为**结果归因真学习**：recover_agent 每次执行记 pending 尝试（fcntl 锁 + tmp/fsync/os.replace 原子写，history 封顶 200），orchestrator 在状态机既有节点结账——进 RESEARCH/FAILED 统一判负规则：同根因 7 天窗内复发判 fail，且 case 6h 内任何年轻 pending（含根因漂移/无法判因）同判 fail（修复未带来稳定），稳定超 6h 判 success；进 ANALYZE（DONE）全判 success；success_rate 改 Beta(1,1) 后验（近 20 条已结账）。
- **单写字段纪律**：kit 自写 strategies/history；演化通道只写新增的 `strategy_adjustments` 段（`<策略>:<根因>` → penalty），select_strategy 打分减去匹配 penalty（research 推荐同样受压制，全候选被压时取扣分后最高分兜底）——无写竞争，演化贡献显式可审计。
- observe.py 新增 kit 观测源：已结账结果记训练记忆 atoms（refs 含 case_id 幂等），同 （策略，根因） 近 14 天窗口内尾部连败 ≥2 自动起草 `strategy_weight_tuning` auto 提案（penalty +0.15 步进、0.6 封顶），连胜 ≥2 且仍有压制时起草案减压（-0.15，降到 0 摘除条目——压制有退出通道）；连败窗口排除未来时间戳（>1h 容差），penalty 解析抗 bool/负值/nan/inf 注入与容器级畸形（kit 与演化两侧同口径）；diff 生成前重读磁盘新快照 + 规范化校验（非规范化落盘不起草），同日未应用草稿自动刷新 diff 防上下文过期卡死，已应用提案绝不动；过 apply_proposal 既有门禁（POLICY 新增 auto 类，路径白名单仅 `.v3.3_strategy_db.json`，≤12 行，回归红自动回滚）。
- E2E 全链验证（注故障→结账→后验降→起草→应用→压制→回滚）+ 九轮多智能体对抗审计（含 cron 上线后训练驱动 396 断言+实弹攻击三轮复攻）；回归 kit 84、dsh 151+24 全绿。
- 演化层由 cron 驱动上线：`observe.py` 每 37 分钟一轮（py3.6.8 实测兼容），observe.log 每周截尾 1MB；apply 提案为人工操作，须用 python3.11 运行 apply_proposal.py（其用 3.7+ subprocess API）。
- **已知限制（apply_proposal.py 属本任务冻结面，列为接受项）**：`apply --rollback` 是整文件还原，回滚点之后 kit 自写的 history/success_rate 会计会一并回退（rollback 为人工操作，备份链在 memory/backups/ 可查）；人工回滚后当日同名提案不再起草（冷静期设计：同日再压等于对抗人工决定，次日恢复）；同日内的连胜减压最早次日生效（同日去重位设计，防止单日内反复横跳）；1h 容差内的近未来时间戳条目按合法账处理（时钟回拨防护的代价：伪造此类条目需 db 写权限，有写权限本即可直接改库）；同日方向翻转只刷新 diff.patch 与标题，evidence.md 滞留旧证据（语义以 diff 为准）；并发 observe 实例瞬态互扫合法 diff（fail-closed 不崩，下一轮同日刷新自愈）。

## 验收记录（2026-08-29 迁移后真机）

- 回归：Python 55/55、node 15/15 全绿
- 状态流：流水线快照落 `mdsim-data/scratch/pipeline/`；风险→修复流：observe.py 读数据根事件/快照，复发自动起草提案（附 RSI 图谱引用 + 训练记忆溯源）至 `mdsim-data/memory/proposals/`
- 看板：注册表作业在列、存活判定不依赖 cwd；未注册旧作业经扫描兜底仍可见；远端条目徽标与降级显示 mock 验证通过
- dsh web/headless 在 DSH_HOME=数据根 下正常启动，会话历史/凭证/auto-approve 均正常
- 发布检查：代码目录 grep 无任何数据件（`_*.jsonl/_pipeline/LEDGER/.dsh/proposals/backups/experience`）与真实密钥

## 数据根布局

```
mdsim-data/
├── events/    flowgate.jsonl / toolcalls.jsonl / agent_inbox.jsonl
├── state/     registry.json / push_state.json / hpc_cache/
├── memory/    atoms.jsonl / scenarios.json / SOP.md / LEDGER.jsonl / proposals/ / backups/
├── scratch/   pipeline/（快照,可重建缓存）/ push.log
├── dsh-home/  .credentials.yaml(chmod 600) / settings.yaml / sessions/ / profiles/ / .agent-presets/
└── projects/<project>/{state,runs}/   （schema 预留）
```

## 迁移（换机器/换目录）

```bash
bash scripts/setup.sh <agent_deploy_kit 根的绝对路径>
# 环境变量: MDSIM_DATA_ROOT(数据根,默认 <代码根上一级>/mdsim-data)、RUN_CASE_BASE(任务扫描树)
```

凭证：编辑 `<数据根>/dsh-home/.credentials.yaml`（由 .example 生成，chmod 600）填入 KIMI_API_KEY。

注：全新克隆请先跑 setup.sh（播种 md_config.json 等机器配置）再跑回归；或临时用 `MDSIM_KIT_BASE=<kit根>` 环境变量越过配置文件直接跑测试。

## 多轮多智能体对抗验证记录（2026-08-26 至 2026-08-29，共 9 轮）

发布后质量门：8 个并行对抗审计子代理（路径/注册表看板/HPC/setup/演化层/主循环/管线/e2e 八面）循环"审计→修复→回归→复审"，以 `docs/对抗基线_ADVERSARIAL_BASELINE.md` 为唯一裁决标准（7 条安全红线 + 30+ 条编号定稿决策，后轮不得翻案）。

- 第 1-2 轮：高危 3（看板 500/nan 白屏/错列）+ 致命 4（HPC 命令注入/路径逃逸/循环连坐/apply 红线绕过），全修
- 第 3-5 轮：SCHEDULE 配置驱动统一、flow-gate 降级焊死、record_atom 跨进程锁、XSS/JS 数据根语义等中低危全修；基线文档冻结
- 第 6-8 轮：按冻结基线复审，新发现逐轮递减（高1中1低5 → 中1低2 → 低3：看板模态框 esc 收口、演化层起草竞态与原子写），全修
- 第 9 轮：**8/8 面零新发现，收敛**
- 终态回归门：dsh-agent python **119/119**、node **24/24**（flow-gate 21 + wechat_push 3）、kit python **50/50** 全绿；每轮修复必带回归测试（复现→红→修→绿）

## 已知限制

- node ≥ 22（run-dsh.sh 自动探测 ~/node2219/bin 等位置）
- pnpm、python3.11（MCP/演化层）；Kimi K3 API key
- agent_deploy_kit（v3.3 专科脚本，作为 kit 根被引用）
- headless 无审批应答者：sim_exec 写门 fail-closed（安全兜底，web 模式正常弹窗）
- llm.longai.vip 已失效（401）；web-search-deepseek 配置项留存但不可用
- SCNet 真 MD 作业待办：批准 PIN 未在真终端设置（`approve --set-pin`)、太原 tyhcnormal 的 `lmp_cmd` 未配置
- L2 证据晋升阈值为初始值（4 账 3 胜/posterior 0.5/连败 3/冷却 7d/饿死 30d)，待试点实账校准；物理正确性靠兜底语义+人工事后审 LEDGER

---

以下保留 v1.1 / v1.0 发布说明要点作历史记录：v1.1 全自主决策模式转正（persona 确认条款翻转"自主执行+备份+审计+事后汇报"，唯一人工闸=不可逆且无法备份的破坏操作；run-dsh.sh 默认 danger-full-access）；v1.0 首个记录版本（流程硬编码化、演化代理 rsi-loop、HPC 预留接口、品牌 MD Exp）。完整原文见 v1.1 快照 RELEASE.md。
