# DSH Button Visualization — Local Engineering Acceptance

Date: 2026-10-13
Status: LOCAL_ENGINEERING_VERIFIED / FINAL_REVIEW_FIXES_VERIFIED / NOT_DEPLOYED

## Result

原生 DSH Bio 聊天已提供简单按钮：3D 预览、选择已登记结构或已完成任务、打开、播放/暂停、真实时间条、表示/居中和收起。空白聊天的输入框上方也能打开，无需模型消息。多个完成阶段必须明确选择；配套结构与轨迹由接受 manifest 核验。

下拉框表示待打开对象，“当前显示”标注画面的实际来源。迟到的打开、阶段解析、详细窗口和恢复不会覆盖新的来源；选择或打开失败时保留实际来源标签。同 ID 的结构以精确文件名及 SHA-256 区分，科研输入 ID 保持原样，旧视图仅在 ID + SHA 唯一匹配时恢复。

预览坐标唯一直连。未配对、断线、身份/来源变化及权限撤销均停止，不中转；旧 HTTP/private read 在 Bridge 读取前拒绝。原科研上传、下载、审批和执行未改变。生产权限默认仍需操作者明确配置。

## Native browser evidence

- 原始 DSH CLI/frontend/preset/SessionProjects/BridgeClient/HumanWorkflowApi/PreviewApi，真实 scoped Agent。
- 工程计算端为本机只读 Python stdio，非真实超算 SSH；使用真实许可、连接器和 Molstar renderer。
- 静态结构 300 Cα，旋转/缩放/居中可用，播放禁用。
- 接受轨迹 203 个源帧，概览 200 个真实采样帧；首帧 2.500 ps，末帧 507.500 ps（源帧 203）。
- 详细窗口 1–64：末帧源帧 64 / 203、160.000 ps；返回概览恢复 200 帧及原始时间范围。
- 1280×900、1280×720、390×844 均验收；短窗口在画布外滚动可到达全部基本播放控件，画布内滚轮用于缩放。
- 原生工作区/会话切换后旧 dock 消失，连接器 helpers / active 均为 0；已返回 Bio 工程体验并重新配对、打开轨迹供用户操作。
- 所有人工可视化操作的模型请求与坐标中转均为 0。

最终构建 `bio-view-BZxz1ZCT.js`，SHA-256 `40be1c04c7e0b54f3d80aab8b64743223044fd7dd34ae22c6520c0e0f72d6dcd`。
连续缓存播放 **226.750 s**，实际 renderer 推进 **2256** 次，覆盖 **200** 个帧；记录到的额外卡顿 **0 ms**，新增坐标传输 **0 bytes**。该测量使用持续播放的开始/结束快照，不使用 seek 代替推进。授权与元数据请求仍会访问服务器。

证据目录：`.superpowers/sdd/2026-10-13-dsh-button-visualization/`。最终证据为 `final-browser-validation.json`、`final-cached-start.json`、`final-cached-end.json`、`final-session-switch-closed.json`、`final-original-hashes.json`、`final-desktop.png`、`final-ready.png`、`final-static.png`、`final-narrow.png` 和 `final-short.png`；先前构建证据保留但不作为最终构建的通过依据。

## Validation performed

- Modeling Python：`PYTHONPATH=bio-agent:bio-agent/src python -B -m unittest discover -s bio-agent/tests -v` — **114/114 PASS**。
- Node：`BIO_TEST_PYTHON=/opt/anaconda3/envs/modeling/bin/python node --test bio-agent/web/runtime/tests/*.test.mjs bio-agent/local-connector/test/*.test.mjs bio-agent/agent/tests/*.test.mjs` — **136/136 PASS**。
- Cloud 三个独立 runner：`test_bio_direct.py` 4、`test_bio_preview.py` 7、`test_bio.py` 15 — **26/26 PASS**，使用 cloud Python 及 `PYTHONPATH=cloud:bio-agent/src`。
- Remote-world/Bio plugin — **16/16 PASS**。
- `node scripts/build-bio-ui.mjs`、`node scripts/build-bio-visualization.mjs` PASS；两份生成 client 一致，所有既有依赖锁未变。
- `/opt/anaconda3/bin/python -B bio-agent/deploy/build_bundle.py` → `bio-20261013-973245c17a46`，140 个白名单文件；解包 `verify_release.verify()` 与 source-catalog import PASS。
- 冻结科学 runtime SHA-256：`2d12f3d6387783f5f28153a020267b65ab97a95c3b16e22a65e07b977d2829c0`；工程原始文件哈希复核全部一致。
- `git diff --check` PASS。所有测试临时物使用本轮登记 TMPDIR；完整日志为 `final-suite-*.log`。再次验证时应先创建新的独占 TMPDIR。
- 未安装依赖、未运行正式 MD、未 commit/push/deploy；未改变生产配置及原凭据。

## Review

一次新上下文整分支审查：`/root/button_whole_branch_review`，gpt-6-astra。三项 Important 加来源标签 Minor；来源标签按实际科学辨识影响上调为 Important。统一一轮修复四项，新增 8 项 Node 和 1 项 Python 回归先观察 RED，再 GREEN，随后全套相关测试通过。未进行第二次审查。

详细结果及每项裁定保存于 ledger 的 `progress.md`、`final-review.md`、`rulings.md`。Deferred minors: None。

## Important caveats

真实超算 SSH/身份/网络、正式 HTTPS/LNA/CSP、实际链路吞吐和提速、生产多用户部署及科学有效性仍为 **PENDING**。本地工程夹具与缓存测试不能证明真实超算提速或科学收敛。需要配套发布同一候选中的前端和计算端；混用旧计算端时新输入 ID 会被拒绝，不猜选或中转。

## Storage and handoff

已停止上一版本的本轮 owned 服务。按准确路径白名单清理本轮已无使用者的 `test-tmp`、`package-red-extract`、`package-extract`、`final-package-extract`，以及两个无效/中间候选 `bio-20261013-4e2eed0dc942`、`bio-20261013-42f735e35879` 的 archive/receipt。符号链接仅解除，未跟随删除目标；完整准确路径、占用及实测记录见 `final-cleanup.json`。

清理前 **120.70 GiB**，清理后 **120.75 GiB**，实测增加 **48.81 MiB**。任务开始到结束的其他空间变化不能归因于本任务。

保留全部未提交源码、当前及此前验证候选、验收截图/日志/哈希、旧 native 故障与验收状态，以及运行中的 `native-experience-5`。来源不明的历史 `/private/tmp` 未清理，未删除真实科研数据或共享依赖。

## Try it

打开 http://127.0.0.1:3081，选择 **Bio 工程体验**。当前已配对并打开 `engineering · v1`，点击 **播放** 或拖动时间条即可。切换对象时在下拉框选择 `engineering-protein.pdb` 或已完成任务，再点 **打开**。

新开面板：**3D 预览 → 连接本地 → 选择对象 → 打开**。本轮工程辅助只把演示码放入密码框，不打印或保存到对话；关闭面板后重新配对。体验服务仍运行，停止它的准确 supervisor PID 见 `native-experience-5/gateway.json`；正式连接配置后应另行进行真实 SSH/HTTPS/带宽验收。

## Changed files

本轮基线与最终哈希对照在 `current-changed-files.json`，包含 37 个必要源码/测试/文档文件。先前脏工作树变化作为既有上下文保留，未回退或整理。

## Rulings I made

1. No commits, pushes, or ledger deletion — explicit user protocol supersedes skill defaults — evidence and local work remain uncommitted.

2. Reuse current linked worktree and installed compatible dependencies — approved session scope — do not create duplicate environments or worktrees.

3. packageAt supports the existing flat dependency layout for frontend asset-only packages — installed DSH has no JS export and no pnpm store; preserve version validation and reuse it — wrong resolution would reject startup rather than install or upgrade dependencies.

4. Register the same manual buttons in the native empty-chat composer dock and use the original DSH session-create API before preview — DSH hides blank headers and has no Agent before a first message — extra entry is visible only for blank Bio sessions; if API changes initialization fails closed.

5. Human get returns a tool summary without acceptance SHA; use artifacts as the authoritative accepted-manifest check and recheck revision/unique attempt — no scientific contract or new model tool — a stale manifest is rejected by artifacts and describe, not inferred from filenames.

6. Python discovery includes bio-agent and src on PYTHONPATH — existing tests import tests.support — command-only correction, no installed environment changes.

7. Review uses preserved dirty-baseline hashes, current files and full branch diff instead of an empty commit range — no commits authorized — reviewer sees prior integrated work as source context and this turn delta explicitly.

8. Promote current-display label preservation from Minor to Important — users must identify the scientific source still on screen after changing a pending selection or failing to open it — cost is a small caption/state correction and regression.

9. Preview input IDs additionally bind registered filename, original ID and SHA, with unambiguous legacy IDs still accepted — same ID/SHA across multiple physical files cannot otherwise be selected without guessing — source-ref fields and scientific input IDs remain unchanged; mixed old frontend/backend deployments fail closed and require the complete candidate.

10. No cloud cache authorization change — real preview_transport checks signature including all allowedActions, so revoked direct permission denies the old tenant before describe — isolated stubs without this boundary cannot establish a deployment bypass.

11. Real SSH/identity/network behavior remains PENDING — local stdio does not establish actual HPC login or link reliability — operator acceptance is still required before production.

12. HTTPS/LNA/CSP remains PENDING — current browser evidence uses loopback HTTP — formal HTTPS may reject loopback until operator configuration is verified.

13. Actual HPC speedup remains PENDING — zero new bytes during cached play is not an end-to-end throughput comparison — no real speedup percentage is claimed.

14. Scientific validity remains PENDING — engineering fixture and frozen runtime integrity do not establish MD sampling or scientific conclusions — production research still needs scientific acceptance.

15. Reviewer browser independent retest was outside its read-only scope — primary actual renderer/UI records establish local engineering behavior; reviewer checked records and code — this does not add a second independent browser acceptance.

Deferred minors: None.
