"""Full scientific conditions in readable review, with immutable full bindings."""
import json
import re
from decimal import Decimal
from .reporting import write_record


def _display(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    return re.sub(r"[\u202a-\u202e\u2066-\u2069]", lambda match: f"\\u{ord(match.group()):04x}", text)


def human_summary(controller, value: dict) -> str:
    protocol = value["protocol"]
    plain = lambda item: _display(item)[1:-1] if isinstance(item, str) else _display(item)
    names = {"prepare":"构建体系与拓扑", "minimize":"能量最小化", "equilibrate":"预平衡",
             "production":"动力学采样", "validate":"数值与文件检查", "analysis":"轨迹分析"}
    lines = [plain(protocol["title"]), "研究目标：" + plain(protocol["decisionSummary"]),
             f"方案：{value['workflowId']}，版本 {value['revision']}"]
    target = getattr(controller, "target", None)
    if target: lines.append(f"最终计算端：{target['targetId']}；登记节点：{target['nodeId']}。")
    lines.append("结构输入：" + ("、".join(plain(item["path"]) for item in protocol["inputs"]) or "由方案生成，或沿用续跑来源"))
    if value.get("reference"): lines.append("续跑来源：" + plain(value["reference"]["sourceWorkflowId"]))
    for stage in protocol["stages"]:
        params, resources = stage["parameters"], stage["resources"]
        lines.extend(["", names[stage["kind"]]])
        if "preparation" in params:
            preparation = params["preparation"]
            lines.append("构建条件：" + _display(preparation))
            if preparation.get("mode") == "amber-protein-ligand": lines.append("配体位于蛋白外部；此方案不保留口袋中的结合姿态。")
            lines.append("体系与约束：" + _display(params["system"]))
        if "minimization" in params:
            minimization = params["minimization"]
            lines.append(f"能量最小化容差 {minimization['toleranceKjMolNm']} kJ/mol/nm；迭代上限 {minimization['maxIterations']}。")
        if "dynamics" in params:
            dynamics = params["dynamics"]
            duration = Decimal(str(dynamics["steps"])) * Decimal(str(dynamics["timestepFs"])) / Decimal(1000)
            lines.append(f"模拟时间 {duration} ps，{dynamics['steps']} 步；时间步 {dynamics['timestepFs']} fs。")
            lines.append(f"系综 {dynamics['ensemble']}；温度 {dynamics['temperatureKelvin']} K"
                         + (f"；压力 {dynamics['pressureBar']} bar。" if "pressureBar" in dynamics else "。"))
            lines.append(f"随机种子 {dynamics['seed']}；{'初始化速度' if dynamics['initializeVelocities'] else '保留已有速度'}；"
                         f"轨迹每 {dynamics['reporterInterval']} 步记录，checkpoint 每 {dynamics['checkpointInterval']} 步保存。")
            lines.append("续跑方式：" + params["restart"] + "；其余积分器条件见完整参数。")
        if "analysis" in params:
            analysis = params["analysis"]
            metrics = {"rmsd":"相对参考结构的偏离 RMSD", "rmsf":"所选原子的波动 RMSF", "radiusOfGyration":"结构紧凑程度（回旋半径）"}
            lines.append("分析指标：" + "、".join(metrics[k] for k in analysis["metrics"]))
            lines.append("统计对象：" + plain(analysis["selection"]) + "；对齐对象：" + plain(analysis["alignmentSelection"]))
            lines.append(f"周期边界处理 {analysis['pbc']}；每 {analysis['stride']} 帧分析一次。")
        if "validation" in params: lines.append("检查条件：" + _display(params["validation"]))
        platform_text = ("CPU" if resources["platform"] == "CPU" else
                         f"CUDA GPU {resources['deviceIndex']}，精度 {resources['precision']}")
        lines.append(f"资源：{platform_text}，CPU {resources['cpuThreads']} 线程；计算时间上限 {resources['wallTimeSeconds']} 秒；输出上限 {resources['maxOutputBytes']} 字节。")
    lines.extend(["", "批准只确认此版本，开始计算需另行提交。采样是否充分及研究结论仍需根据结果判断。"])
    return "\n".join(lines)


def review_view(controller, value: dict, bindings: dict) -> dict:
    snapshot = {"protocol": value["protocol"], "specSha256": value["specSha256"],
                "reference": value.get("reference"), "bindings": value["bindings"], "freshBindings": bindings}
    record = write_record(controller.project, snapshot)
    protocol = value["protocol"]
    target = getattr(controller, "target", None)
    lines = ["Bio 生物分子模拟方案：人工审阅", f"工作流：{value['workflowId']} / revision {value['revision']}",
             f"完整 spec SHA-256：{value['specSha256']}",
             "单位：温度 K；压力 bar；时间步 fs；轨迹时间 ps；cutoff/盒长 nm；padding/gap Å；盐 mol/L；最小化容差 kJ/mol/nm。",
             "标题与研究目的：", _display({"title": protocol["title"], "researchGoal": protocol["decisionSummary"]}),
             "结构输入（含完整内容身份）：", _display(protocol["inputs"])]
    if target:
        lines.extend(["最终计算目标（与本次授权绑定）：", _display(target),
                      "结构构建、拓扑生成、参数化、动力学和分析均在该计算端运行。"])
    if value.get("reference"):
        lines.extend(["续跑来源与模型血缘：", _display(value["reference"])])
    for stage in protocol["stages"]:
        lines.extend([f"阶段：{stage['kind']}；全部执行参数与资源预算：", _display(stage)])
    lines.extend(["批准绑定：", _display(value["bindings"]),
                  f"完整协议、来源与环境文件清单：{record['path']}", f"该记录 SHA-256：{record['sha256']}",
                  "本次确认只批准此版本；需要开始计算时另行点击提交。采样是否充分需要根据结果判断。"])
    return {"text": "\n".join(lines) + "\n", "record": record}
