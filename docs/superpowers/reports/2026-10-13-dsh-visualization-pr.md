# DSH task visualization — PR validation

Date: 2026-10-14
Status: LOCAL_ENGINEERING_VERIFIED / NOT_DEPLOYED / REAL_HPC_PENDING

## Current behavior

已有 Bio 聊天任务的页头提供“结构与轨迹”；首页和空白新会话保持原生 DSH 布局。
面板可选择已登记结构，以及建模或计算任务中已完成阶段的接受结果；多个阶段需明确选择。
配套结构、轨迹、来源哈希、版本和接受 manifest 均重新核验，失败时保留当前画面的来源标签。

本地配对后点击“打开”显示结构。轨迹支持播放/暂停、真实时间定位、概览与最多 64 帧的详细窗口。
按钮不调用模型。预览字节只通过本地 SSH 连接器读取同一已登记执行目标，服务器负责授权和元数据；
未配对、权限或来源变化时停止，坐标中转入口拒绝读取。

主题跟随 DSH。拖动标题栏移动窗口，拖动右下角分别调整宽高；复位、双击标题栏和方向键也可操作。
窗口限制在可见范围内，画布随尺寸调整；来源、帧和相机视角保持。连接、加载和错误提示固定在选择框下方。
本地工程入口在用户点击“连接本地”时刷新演示配对码，避免等待后过期。

## Initial preview/window validation (2026-10-13)

- Node runtime / local connector / Bio agent: **159/159 PASS**.
- Bio Python offline engineering suite: **114/114 PASS**.
- Cloud: **107 unittest cases + 28 pytest HPC cases PASS**; original gate script **57/57 PASS**.
- Bundle credential export regressions: **3/3 PASS**.
- Native UI and Mol* builds PASS; generated client copies identical. Current asset: `bio-view-C4OgQAik.js`.
- Verified candidate `bio-20261013-2f85c7194ba9`: **142 files, integrity PASS**; fresh build assets exactly match the candidate.
- Frozen scientific runtime SHA-256: `2d12f3d6387783f5f28153a020267b65ab97a95c3b16e22a65e07b977d2829c0`.
- Actual DSH browser evidence for this build: unpaired Open prompt, 1UBQ ribbon rendering, title drag, independent resize,
  viewport clamping, keyboard resize, reset and ongoing trajectory playback during resize; exact source/frame/camera preservation.
- Original scientific inputs and the user screenshot preserved. No dependencies installed or upgraded.

Node command: `BIO_TEST_PYTHON=<compatible-modeling-python> node --test bio-agent/web/runtime/tests/*.test.mjs bio-agent/local-connector/test/*.test.mjs bio-agent/agent/tests/*.test.mjs`.
Bio command: `PYTHONPATH=<repo>/bio-agent:<repo>/bio-agent/src <compatible-modeling-python> -B -m unittest discover -s bio-agent/tests -v`.
Cloud unittest modules run in independent processes with an absolute Cloud/Bio PYTHONPATH.
`test_gate.py` runs directly; `test_hpc.py` uses pytest. An initial unittest-only invocation for these two modules
reported an import-time SystemExit and zero collected tests; original logs were retained and the correct runners passed.

Full command logs, source hashes and integrity receipts are retained in the local ignored
`.superpowers/sdd/2026-10-13-dsh-visualization-pr/` ledger. Actual final UI evidence is retained in
`.superpowers/sdd/2026-10-13-dsh-preview-window/`; these local directories are excluded from Git.

## Appearance and style templates update (2026-10-14)

The same task panel now offers six color schemes (auto, element, chain, residue, sequence rainbow and uniform),
a custom uniform-color picker and three material finishes (matte, glossy and metal). Eleven one-click templates
combine representation, coloring and material: six biomolecular presets and five material presets for element atoms,
metal crystals, interface groups, material ball-and-stick and lightweight wireframe. Manual refinements show Custom.
View-state v2 stores appearance metadata; v1 views and legacy representation commands remain readable.

Appearance-only changes reuse current representations. Geometry changes replace all representations in one
rollback-capable transaction. Rapid choices retain the latest requested style and respect source bindings.
The source, current trajectory time, camera and DSH theme binding remain intact; no preview regeneration is needed.

- Fresh pre-push Node runtime / connector / Bio agent tests: **181/181 PASS**; bundle-security tests: **3/3 PASS**.
- Complete project build: `node scripts/build-bio-visualization.mjs`; current asset `bio-view-DHXRaWXq.js`.
  Asset SHA-256 values and the existing verified candidate were rechecked before this commit.
- Candidate `bio-20261014-a22da4377983`: **145 files, integrity PASS**, with the unchanged scientific runtime hash above.
- Actual native DSH/Mol* evidence: all six Bio templates on 1UBQ and all five material templates on a registered
  216-atom Cu/Ni interface engineering fixture. Applied geometry, theme and material match renderer transforms;
  static frame/time, source and camera remain unchanged across template choices.
- During playback, cartoon → metal spheres → rainbow cartoon continued from frame 3 to frame 6 with source and
  camera preserved. Light/dark synchronization and controls at 319 × 750 also passed; viewport and theme restored.
- Material presets do not add raw LAMMPS dump/data parsing or atom-type mapping. Interface groups use existing
  chain IDs. The ideal Cu/Ni display fixture uses an arbitrary test lattice and is not a LAMMPS calculation result.

Local appearance and template evidence remains in the ignored `2026-10-14-display-appearance` and
`2026-10-14-style-templates` ledgers. Fresh pre-push logs and Git/PR readbacks are retained in the ignored
`.superpowers/sdd/2026-10-14-appearance-templates-pr4/` ledger. These records, scientific fixtures and build output
are excluded from the commit. The earlier Python/Cloud/gate counts above describe the initial preview acceptance;
those unchanged scientific components were not rerun for this display-only update.

## Integration and limits

This change builds on Bio PR #2 and is reviewed against `codex/bio-remote-workflow`.
Source, contracts, scoped runtime, frontend, connector, tests, provenance and user instructions are included together.
Credentials, SSH identities, account/runtime configuration, scientific data, build output and local evidence stay outside Git.

Preview/direct/view permissions default off and require explicit operator configuration.
The current demo uses LOCAL_PYTHON_STDIO and synthetic trajectory fixtures. Real HPC SSH/identity/VPN,
HTTPS/LNA/CSP, end-to-end throughput and speed improvement, production multi-user behavior and scientific trajectory
acceptance remain **PENDING**. No deployment or formal MD simulation occurred. This report combines initial
preview/window acceptance with the dated appearance/template update above; older reports retain their candidate identities.
