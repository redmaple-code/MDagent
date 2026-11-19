# dsh-agent 进化层设计与 AI 交接文档

**版本**:v1.6.7+20260920(初版 v1.6.0+20260918,09-20 增补:②回放考场落地+首考结果、L0 外部知识层、图谱进化语义)
**日期**:2026-09-20
**定位**:本文档同时服务两个目的——(a) 论文写作的技术素材(设计动机、架构、算法、实验数据均可直接引用);(b) AI/人类接手者的交接手册(文件清单、运行命令、实现规格、运维纪律)。
**代码根**:`/opt/mdagent/app`(live 真身)
**数据根**:`/opt/mdagent/data`(真实账本,多版本共享)
**论文专用素材**:《论文素材_外部知识爬取与自进化_20260920.md》(同目录,聚焦"爬取数据+自进化"两主题的成文写法)

---

## 1. 一句话概述

dsh-agent 进化层是一条**"外部知识候选池(L0-EXT) + 经验沉淀 → 三轴归类 → 回放考试 → 资格审查 → 毕业成专家子agent/skill → 在岗继续强化"**的完整流水线,核心创新是:**确定性代码做筛选与裁判(零 token),LLM 只烧在蒸馏正文与最终判断考场;考题只来自真实跑过且有结局的历史事件,绝不重跑分子动力学模拟;外部爬取的专家经验只作候选弹药,必须经回放考场裁决才能入环**。

---

## 2. 问题背景(写论文的 Motivation 节)

系统运行数月后暴露出三个结构性问题:

1. **知识库是"被动的图书馆"**。至 2026-09-18 已沉淀 482 条经验原子(atoms),但运行时 11 个 MCP 工具无一查询本地知识库——经验存在,却没有人喂给 agent。踩过的坑在下一个会话里原样再踩。
2. **蒸馏一维压平**。所有经验混在单一 `kind`(失效签名)维度里:课题知识(某体系的热解动力学)、方法知识(控温控压设置技巧)、智能体自身运行问题(流程闸违规/工具误用)三类本质不同的知识被同一管道处理,无法分流到各自的归宿。
3. **毕业靠感觉**。一个领域的经验何时"足够厚"、可以固化成可复用的 skill 或独立子 agent,没有任何量化判据;更没有"它真的会了吗"的检验环节。

参考了两个外部工作后由用户拍板定案:

| 外部工作 | 借鉴 | 不取 |
|---|---|---|
| Dream-RSI(论文,代码未发布) | **回放事件**作为训练/评估素材的概念 | LLM "做梦"生成经验——太烧 token |
| RSI-Harness(github.com/CosmosMind-ai/RSI-Harness) | 模式→工件的决策表、证据分级 | 无评估回路,人工闸即终点(比我们的后验闭环浅) |

---

## 3. 设计原则(四条铁律,用户原话语义,不可违)

1. **三轴沉淀**:经验按 ①课题(归宿=课题子 agent,含物理感知)②方法(控温压/建模/力场等技术问题,归宿=skill)③智能体运行问题(归宿=skill 或子 agent)三轴归类。
2. **零 token 优先**:能用确定性代码裁判的绝不用模型。打标、聚类、回放裁判全部纯代码;LLM 只在蒸馏正文与 L4 判断考场上烧。
3. **考题只用真实跑过且有结局的事件,绝不重跑 LAMMPS**(重跑=纯烧算力且无任何新信息);无真实结局不成考题;禁止合成题(出题人的物理假设本身可能是错的)。
4. **毕业靠回放考古,晋升靠实战立功**:入场考试用历史数据回放(免费);毕业后的晋升/回炉由新生产任务的真实结局驱动(任务本来就要跑,零额外算力),结局归因到建议者,用 Beta(1,1) 后验做在岗 RL。

---

## 4. 总体架构

### 4.1 分层记忆(既有, TencentDB-Agent-Memory 模式的 stdlib 实现;2026-09-20 增 L0-EXT)

```
L0-EXT 外部语料层  external_corpus/             —— 爬取的论坛/issue 问答(5570 条,unverified,只作候选弹药)
L0 原始层  _flowgate.jsonl / LEDGER.jsonl      —— 事件流水,只读不写
L1 原子层  memory/atoms.jsonl                  —— 一条=一个失效/修复事实,带 refs 指回 L0
L2 场景层  memory/scenarios.json               —— 同签名原子聚成复发场景,挂历史修复结果
L3 规则层  memory/SOP.md                       —— 从"修复已生效且未回滚"场景蒸馏的规则,人可读白盒
```

纪律:**压缩必须可溯源**(下层存证据,上层存结构);SOP.md 每次全量确定性重建,绝不增量涂抹导致来源断链。原子支持 `retracted`(撤回:事实错了)与 `superseded_by`(被蒸馏结论取代:事实没错但已被吸收),两者对查询不可见但本体保留(审计不删除)。

### 4.2 毕业阶梯(v1.6.0 新增,在三轴之上)

```
L0 原子(atom) ──厚度──▶ L1 场景 ──蒸馏──▶ L2 知识卡 ──▶ L3 skill ──▶ L4 子agent(领域专家)
                              每一级晋升都要过对应的"考场":
                              L3 毕业 = 回放裁判(replay_eval,纯代码)
                              L4 毕业 = 截断考场(VCR 式决策点单发,烧 LLM,分级:助理级→专家级)
                              毕业后 = 在岗 RL(新任务结局归因,Beta 后验,连败回炉)
```

### 4.3 三轴数据模型(v1.6.0 核心)

每条原子/提案携带 `axes` 字段:

```json
{
  "topic":             ["sys-a-pyrolysis"],            // 已认证课题(known_topics 归并表命中)
  "topic_candidates":  ["PRJ_SYS-A_L_pyro_RUN2000"],    // case_id 正则直通的候选课题(未过人工闸)
  "method":            ["repair-rescue", "dynamics/temp-press"],
  "ops":               ["ops/flow-gate"],
  "exam_ready":        true
}
```

**topic 与 topic_candidates 分轴是关键安全设计**(红队ν发现):case_id 正则 `[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+){2,}` 是自由提取通道,伪课题名(如 `MyResearch_Project_v1`)可借此灌水认证轴骗毕业。因此正则命中的只进 candidates,**不过人工闸不计入毕业资格审查**;只有 `known_topics` 受控词表归并的才算认证课题。

各轴归宿:topic→课题子 agent;method→skill;ops→skill 或子 agent。

### 4.4 外部知识层 L0-EXT(2026-09-20 新增,详见《论文素材_外部知识爬取与自进化_20260920.md》)

**动机**:回放考场首考(v1.6.6)暴露修复工具箱对 extreme_temp/QEq_instability/general_instability 三大根因真实亏空——自有经验在these根因上全是失败账,无成功样本可供蒸馏。外部专家经验(论坛/issue 维护者答复)是候选弹药的自然来源。

**设计**:
- 语料:AI4Science md-error-corpus,五源爬取(matsci.org LAMMPS 论坛 Wayback/GROMACS 论坛/gmx-users 邮件列表 2000-2019/GROMACS GitLab/LAMMPS GitHub issues),5570 条有解记录+1169 无解,2982 唯一错误签名,12246 解法候选,3966 条维护者回复;CC BY-NC-SA 4.0
- 落位:`mdsim-data/external_corpus/`(数据真身);检索缝 `corpus_lookup.py`(签名归一化+token 打分,绿区只读);已入 atoms(ops-rule)纪律:遇报错先查语料
- **入环闸门(核心纪律)**:外部知识**经考场入环,不经考场不入环**——语料 unverified,检索返回是参考不是命令;疼痛类别(体系爆炸 579/电荷与自由能 97/温控与压控 54)的修复模式须蒸馏成 spec_* 策略候选 → 回放考场裁决 → 实弹验证,胜出后才 reviewed=true 升格
- 图谱可视化:节点 type=corpus/extcat(橙色 polar 层,level=0「L0 · 外部语料」),「供弹」金边直连三大根因考场节点



---

## 5. 已落地模块详解(①三轴打标+回填+分流,v1.6.0)

### 5.1 文件清单

| 文件 | 状态 | 职责 |
|---|---|---|
| `evolution/expert_tags.json` | 新增 | **受控词表**:method 10 标签、ops 5 标签、known_topics 归并表、case_id 正则、stopwords、exam_ready_kinds。新增标签=改此文件过人工闸 |
| `evolution/tagging.py` | 新增 | `tag_atom(kind,fact,refs)→axes` 纯函数;`exam_ready()` 三要素校验;`verify_refs()` refs 存在性核验。零 LLM、零写库 |
| `evolution/memory.py` | 修改 | `record_atom()` 写入时自动打 axes,打标器异常不阻断入账(try/except) |
| `evolution/backfill_tags.py` | 新增 | 一次性回填 CLI:dry-run 默认,`--write` 全程持锁带备份原子替换 |
| `evolution/observe.py` | 修改 | 提案 meta.json 与人工闸请求均带 axes(分流可视) |
| `tests/test_tagging.py` | 新增 | 21 项单测(含全部红队回归项) |
| `stress_tagging.py`(版本快照夹) | 新增 | 15 项压测 |

### 5.2 词表(expert_tags.json)内容

- **method(10)**:`repair-rescue`(修复救援)、`dynamics/temp-press`(控温控压)、`model-opt`(模型优化)、`modeling/packmol`、`modeling/moltemplate`、`modeling/scripting`(自编成脚本)、`trajectory/dump`、`postproc`(后处理)、`hpc-exec`、`phys-perception`(物理感知)
- **ops(5)**:`ops/flow-gate`、`ops/friction`、`ops/evolution`、`ops/security`、`ops/process`
- **known_topics(7)**:`sys-a-pyrolysis`、`run2000-pyrolysis`、`fds-calibration`、`v13-forcefield-cmp`、`hydrogel-gmx`、`graphene-aerogel`、`pyro-general`
- **exam_ready_kinds(11)**:带结局的原子类别(recovery-outcome 等)

### 5.3 打标算法(tagging.py)

```
tag_atom(kind, fact, refs):
  1. 入口截断: kind[:200], fact[:4000], refs 拼接[:1000]
     —— 压测实测 10MB 纯 'a' 输入让 case_id 正则回溯爆炸卡死分钟级;
        标签语义本就不依赖长文尾部,截断零损失
  2. method 轴: 关键词子串匹配(关键词常嵌在 case_id/复合词里,
     如 qeq_instability,词边界会漏真命中)
  3. ops 轴: 纯 ASCII 词字符关键词走 \b 词边界正则
     —— "audit checklist" 这类正常文本不能误标 ops/security
  4. topic 轴: known_topics 子串归并(认证) + case_id 正则提取(候选)
     stopwords 挡 slug/环境变量/工具名;正则被改坏时仅 topic 轴降级为空,
     不拖垮 method/ops
  5. exam_ready 三要素(语法检查):
     ① 有 refs 溯源到 L0 决策点快照
     ② kind ∈ exam_ready_kinds(带结局的类别)
     ③ fact 含结局信号(冒号结构化结局或 成功/失败/fail/pass/修复/验证)

verify_refs(atom, base_dir):  进考题留出集前的第二道闸
  形如 'path:line' 的 ref 指向的文件须真实存在;任一 ref 可核验即通过
  (其余可能已轮转);非文件形态 ref(会话 id)不可核验→False
  —— 红队ν:exam_ready 只是语法检查,虚构修复没有真文件可指
```

**词表加载校验**(`_validate_table`):剔除非 str/空关键词——空串子串匹配=全命中(红队ξ),塞 `""` 进词表等于给该标签开全通。

### 5.4 入账接线(memory.py)

`record_atom()` 在既有验证链(长度≥10、截断 2000、friction 噪音拦截、str 型 refs 包 list、文件锁内近似去重、毫秒+uuid 全局唯一 id)之后追加:

```python
try:
    import tagging
    atom["axes"] = tagging.tag_atom(kind, atom["fact"], refs)
except Exception:
    pass   # 打标器故障绝不阻断入账——记忆层可用性优先
```

### 5.5 提案分流(observe.py)

自动蒸馏起草提案时,`meta.json` 与人工闸请求两处都写入 `axes`——人工裁决时直接看到该提案归属的课题/方法/运行域,为后续"按轴分发给对应子 agent/skill"铺路。

### 5.6 回填工具(backfill_tags.py)

```
python3.11 evolution/backfill_tags.py            # dry-run 报告
python3.11 evolution/backfill_tags.py --write    # 实写(自动备份)
python3.11 evolution/backfill_tags.py --write --force   # 强制重打已有 axes
```

对抗加固要点(全部为红队实测发现后修复):

- **全程持 `ATOMS.lock` 文件锁**,与 `record_atom` 同一把锁;锁内重读重算再写——原实现双读无锁,读写窗口内并发入账的新原子会被旧快照回滚丢失
- **坏行/无 kind 行原样回写**(原静默丢弃=数据丢失);`json.loads` 严格模式拒 NaN/Infinity(原 loads 收 dumps 吐,文件对 jq 永久损坏)
- 写入前 `lstat` 拒非正则文件(软链跟随会把任意可读文件拷进备份)
- 备份 `os.open(O_EXCL, 0o640)` + uuid 短缀(不依赖 umask,同秒不撞名),保留最近 10 份
- 已有 axes 是字符串等畸形时判非法重打,不崩
- 报告含 `method_flood` 告警:单原子挂 ≥4 个 method 标签=灌水信号

### 5.7 真账本数据画像(2026-09-18 回填 482 条)

```
exam_ready(可考题化): 283     三轴全空: 21    标签灌水告警: 4

[topic 认证轴]  sys-a-pyrolysis 269 · run2000-pyrolysis 238 · pyro-general 271
                hydrogel-gmx 9 · v13-forcefield-cmp 4 · fds-calibration 3
[topic_candidates 候选轴(不过闸)]  PRJ_SYS-A_L_pyro_RUN2000 233 · PRJ_SYS-A_L_pyro_RUN1500 31 · ...
[method]      repair-rescue 295(最厚,首个回放考场候选)· modeling/scripting 19
              dynamics/temp-press 17 · model-opt 11 · postproc 9 · phys-perception 7
[ops]         evolution 84 · friction 31 · flow-gate 19 · process 8 · security 5
```

读法:修复救援类经验最厚,是第一个够格开考场的方法域;SYS-A/RUN2000 两个课题占绝对主体,课题子 agent 的第一批候选。

**09-20 更新**:atoms 已增至 527 条(含僵尸 case 刷单污染事件与治理记录);②回放考场已落地并在真账本上完成首考(见 §6.5)。

---

## 6.5 ②回放裁判落地与首考结果(v1.6.6+20260919,原 §8 路线图②)

`evolution/replay_eval.py` 已按原规格落地并完成真账本首考:

- **考题**:kit strategy_db history 带结局事件(只收 success/fail),时序 80/20 切 seen/holdout;本次 80 事件全收
- **考生**:(根因, 策略)对;exam_ready 原子 refs 逐一解析回 history 行做第二道存在性核验——41 条核验过、**0 伪造**、263 条非本考场题型(untested 只登记)
- **判分**:hit_rate(对真实成功的覆盖)/ fp_rate(出手未成比例)
- **首考成绩(诚实基线,非 bug)**:11 考生**无一毕业**(毕业线 hit≥0.8 且 fp≤0.1)——checkpoint_restart 对 QEq_instability/general_instability hit=1.0 但 fp≈0.94(重启成功≠根因治愈,同因 7 天内复发),对 extreme_temp **25 发 0 中**;三支 spec_* 新策略候选(timestep_floor_damp/qeq_relax_stronger/nve_limit_tighten)untested 从未开火——揭示的不是评估器问题,而是**弹药供应链断供**:选路层在只有一把扳手的工具箱里反复挑那把扳手(76/80 次尝试押 checkpoint_restart,胜率 0.05)
- **治理结论**:①度量卫生——"已完成未关账"的僵尸 case 曾以 ~9 分钟/条的速率刷假失败账(94% 复发率大半来自同一 case),修复=终态关账 + 机械剔账规则(仅剔除可证实机械刷单,真实败账不动);②外部知识层(§4.4)由此立项

---

## 6. 既有闭环:行为策略自动蒸馏(v1.5.27,与三轴互补)

三轴是**知识的归类与分流**;v1.5.27 落地的是**行为复发问题的自动治理**,两者共用 L0-L3 记忆底座:

- **五类行为签名**(否定纠正/重复追问/打断/工具错误/审批否决)提案 tier=`auto`,不走人工闸;确定性规则模板写 `behavior_strategies.json` 并渲染进 persona 两处(`agent.cordis.yml` + headless patch,程序保证逐字一致)
- **后验撤回**:每轮 observe 比对窗口计数,**严格连升 4 轮**(复发仍在发生)才撤回——"对基线不降即撤"被实证必误判(7 天窗计数衰减慢);撤回=移出 persona+签名冷却 7 天+开人工闸叫人
- **人工降级**:看板"行为策略"卡可翻案——撤下策略+签名永久回人工闸

当前在役:4 条策略(user-correction / friction-interrupt / friction-tool-error / friction-denial),0 撤回 0 冷却。

**进化指数记分卡**(`dashboard/web_monitor.py build_evolution_metrics`):由 kit 结账历史 + 记忆层算出的 0-100 分。子分:胜率/一次修复/低复发/训练量/先验偏离/演化吞吐/知识覆盖。注意头部三项(胜率/复发率/恢复耗时)是**全历史 trailing 指标**——RUN2000 崩溃循环期的旧账会长期锚住分数,近期真实水平看 5 天×6 的趋势桶。

---

## 7. 对抗审计与压测(论文的 Robustness 节)

### 7.1 三路红队(2026-09-18,无 P0)

| 红队 | 发现 | 修复 |
|---|---|---|
| μ(代码) | str 型 refs 被 `list()` 逐字符拆分,静默丢溯源 | `_norm_refs` + 入账前包 list |
| μ | backfill 双读无锁,并发入账被旧快照回滚 | 全程持锁,锁内重读重算 |
| μ | 坏行/无 kind 行静默丢弃 | kept 列表原样回写,计数进报告 |
| μ | `json.loads` 收 NaN、`dumps` 吐,文件对 jq 永久损坏 | `parse_constant` 严格拒收 |
| μ | 已有 axes 是字符串等畸形时崩溃 | 非 dict axes 判非法重打 |
| ν(语义灌水) | 伪课题名经 case_id 正则直通认证轴骗毕业 | topic/topic_candidates 分轴,候选不过闸不计数 |
| ν | "audit checklist" 正常文本误标 ops/security | ops 轴词边界 + 词表删裸词 audit |
| ν | refs 无校验,虚构修复可混进考场 | `verify_refs()` 存在性核验,进留出集前必过 |
| ν | 单原子挂满标签灌水无告警 | `method_flood` 计数告警 |
| ξ(文件系统) | 空关键词子串匹配=全命中 | 加载时剔除非 str/空关键词 |
| ξ | ATOMS 是软链时跟随写入任意文件 | `lstat` 拒非正则文件 |
| ξ | 备份权限依赖 umask、同秒撞名 | `O_EXCL 0o640` + uuid,留 10 份 |

每条发现配一项回归测试,共 21 项单测全过。

### 7.2 压测(stress_tagging.py,15/15)

- **性能**:tag_atom 3 万条/s;backfill 1 万条写盘 0.5s——打标不是热点
- **压测抓获的真修复**:10MB 病态长文曾让 case_id 正则回溯卡死分钟级→入口截断;词表正则被改坏→仅 topic 轴降级为空;备份名同秒撞名→uuid 短缀
- 病态输入(None/bytes/控制字符/下划线炸弹)不崩;词表缺失/坏 JSON 全轴降级;30 线程并发 600 条 id 唯一 axes 齐全;三次回填幂等;真账本剥 axes 后与备份逐字节等价

---

## 8. 路线图:②-⑤ 实现规格(接手者照此施工;② 已落地,见 §6.5)

### ② 回放裁判 `replay_eval.py`(**已落地 v1.6.6**,首考结果见 §6.5)

- **输入**:某标签(如 `repair-rescue`)下所有 `exam_ready=true` 且 `verify_refs` 通过的原子
- **机制**:候选程序(规则集/skill 草稿)在原子的历史决策点上回放,计算**命中率/误报率**
- **留出集更严**:训练/留出切分,留出集阈值更严(防过拟合历史)
- **进留出集前必须过 `tagging.verify_refs`**——这是红队ν堵的虚构修复通道,接手时勿绕
- **输出**:每个候选的 (hit_rate, fp_rate) 成绩单元,喂给 ③

### ③ 毕业审查 `graduation.py`(weekly timer)

五条件全绿出毕业提案进人工闸:

1. 厚度:该标签下 ≥5 张 reviewed 知识卡
2. 回放成绩:命中率 ≥0.8 且误报率 ≤0.1(②的成绩单)
3. 稳定性:30 天内无撤回(retracted)
4. 覆盖面:≥3 个子场景
5. L4 追加:在用计数 + 判断闭环证据

毕业后持续后验监控,**退化即降级回炉**。

### ④ L4 物理感知截断考场(唯一烧 LLM 的环节)

- **VCR 式回放**:真实 case 的决策点单发——给候选子 agent 放送到某一步的完整上下文,让它下一个判断;~12-20 次调用/场
- **难度=截断比例**:100% 上下文 → 50% → 25%,逐级加压
- **鉴别诊断题**:开检查单(从多个看似合理的诊断中选真因)
- **过度自信重罚**:答错且自信度高的扣分重于答错本身
- **分级毕业**:助理级 → 专家级,不是一考定终身

### ⑤ 在岗 RL(毕业后)

- 新任务是生产驱动本来要跑,**零额外算力**
- 任务结局归因到建议者(谁的知识/建议被采纳)
- Beta(1,1) 后验更新在岗分;连胜晋升、连败回炉
- 新事件回填题库,滚动加难

---

## 9. 交接运维手册

### 9.1 目录与真身

| 路径 | 角色 |
|---|---|
| `AI-MD/dsh-agent` | **live 真身**(当前 v1.6.0+20260918),所有运行中的服务读这里 |
| `AI-MD/mdsim-data` | 数据真身(记忆/账本/提案),多版本共享,只认这里 |
| `AI+MD-NEW/mdexp_agent_v1.6.0+20260918` | v1.6.0 开发快照存档(含 stress_tagging.py 与 README 审计表) |

### 9.2 常用命令

```bash
cd /opt/mdagent/app

# 记忆层
python3.11 evolution/memory.py record <kind> <fact> [refs...]   # 手动入账(自动打 axes)
python3.11 evolution/memory.py rebuild                          # 重建 L2/L3
python3.11 evolution/memory.py retract <atom_id> <理由>          # 撤回
python3.11 evolution/memory.py semantic <关键词>                 # 语义搜索

# 打标回填
python3.11 evolution/backfill_tags.py [--write] [--force]

# 行为策略
python3.11 evolution/behavior_strategies.py list|render|revoke <sid>

# 蒸馏一轮(行为闭环 + 提案)
python3.11 evolution/observe.py --force
/usr/bin/python3 /opt/mdagent/deploy/knowledge_distiller.py  # L1→L3 决策树

# 测试
python3.11 -m pytest tests/test_tagging.py -q    # 21 项应全过
```

### 9.3 运维纪律(血泪教训,勿违反)

1. **新增标签=改 expert_tags.json 过人工闸**,不在代码里塞关键词
2. **SOP.md 每次全量重建**,绝不增量涂抹(来源断链)
3. **撤回/取代不删本体**(retracted/superseded_by 标记),审计可溯源
4. **打标器故障不阻断入账**;回填不丢坏行;所有整写走 `tmp+os.replace` 原子写
5. **备份 O_EXCL 0o640**,不依赖 umask
6. **use_count 类读路径统计绝不写 atoms 本体**(读路径写放大是多轮对抗修掉的坑)——毕业证据场景用独立 usage.jsonl
7. test_evolution.py 有 52 个 error 是 kit 文件 root:mdorch 权限的环境性问题,live 与快照逐字节一致,**勿当回归**

### 9.4 已知待办(接手 backlog)

| # | 任务 | 优先级 |
|---|---|---|
| 84 | ②回放裁判 replay_eval.py | ✅ v1.6.6 已落地+真账本首考(§6.5) |
| 85 | ③graduation.py 五条件审查 | 依赖 84 |
| 86 | ④L4 截断考场 | 依赖 85 |
| 87 | ⑤在岗 RL 归因接线 | 依赖 85 |
| — | sim_repair_research 注入本地知识(本地命中已验证→跳过联网研讨,token 摊销闭环,~15 行) | 独立可做 |
| — | 进化指数头部卡片改 30 天滚动口径(趋势桶已是滚动数据,头部卡未用)——需用户拍板 | 待定 |

---

## 10. 论文视角的创新点提炼(可直接引用)

1. **零 token 的三轴确定性知识分类器**:受控词表 + 词边界/子串双模式匹配 + 正则候选通道隔离,3 万条/s,可审计、可复现、无幻觉面——与 LLM-as-tagger 路线形成对照。
2. **认证轴/候选轴分离的防灌水设计**:自由提取通道(case_id 正则)的产物永远进不了认证计数,人工闸是唯一的轴间晋升通道——类比密码学的"不可信输入不得进安全域"。
3. **回放-only 评估范式**:考题=真实跑过有结局的事件(refs 可核验到 L0 快照),既不重跑昂贵的物理模拟,也不合成题(出题人物理假设可能错)——把"评估成本"与"评估可信度"同时压到极限。
4. **双回路进化**:离线回路(回放考古定毕业,零边际成本)+ 在线回路(生产任务结局归因 + Beta 后验,零额外算力),两条回路共用同一证据账本。
5. **后验撤回的严格递增语义**:"严格连升 4 轮才撤回"是对窗口计数衰减特性的实证修正——朴素"不降即撤"在计数型指标上必误判,该教训对所有在线策略评估通用。
6. **全链对抗加固**:三路红队(代码/语义灌水/文件系统)共 12 项发现全部修复并配回归测试;压测实测抓获正则回溯炸弹等 3 个真缺陷——工程鲁棒性可作为系统的第四项贡献。
7. **外部知识"经考场入环"**:爬取的论坛/issue 专家经验(5570 条)只作 unverified 候选弹药,检索缝返回"参考不是命令",必须蒸馏成策略候选→回放考场裁决→实弹验证胜出后才升格 reviewed——外部知识与自产经验走同一条毕业路,不给外部语料开信任后门。
8. **考场即诊断仪(诚实基线)**:首考 11 考生零毕业不是评估器失败,而是回放考场把"修复工具箱对三大根因真实亏空"量化出来(hit 1.0 但 fp 0.94=重启成功≠根因治愈;25 发 0 中=该根因无可用武器)——评估回路的首要价值是暴露弹药供应链断供,驱动外部知识层立项。

---

## 附:版本沿革

| 版本 | 日期 | 进化层相关 |
|---|---|---|
| v1.5.25 | 2026-09-17 | 绿区裁决器(path_jail + exec-policy + exec_gate) |
| v1.5.27 | 2026-09-17 | 行为策略自动蒸馏闭环(三兜底)+ 后验撤回 |
| **v1.6.0** | **2026-09-18** | **三轴打标 + 回填 + 提案分流 + 红队加固(本文档)** |
| v1.6.6 | 2026-09-19 | ②回放裁判落地 + 真账本首考(11 考生零毕业·诚实基线)+ 僵尸 case 治理 |
| **v1.6.7** | **2026-09-20** | **L0-EXT 外部知识层(语料落地+检索缝+图谱供弹)+ 图谱进化语义(融合/断裂/L1-L3)+ 本文档增补** |
