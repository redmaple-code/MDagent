# Bio integration with the tenant web platform

This source candidate synchronizes the existing node/tenant/remote-world platform
and adds a separate Bio preset. It is not deployed or scientifically accepted.
The existing material preset remains the default. No server removal, service
restart, credentials migration or environment installation was performed while
preparing this candidate.

## Execution placement

- The web/controller host runs the model, permission gate and receipt display.
- In `node-world` mode the enrolled node is the final compute host.
- In `node-ssh` mode the enrolled node runs only the fixed `run_biogateway.py`
  relay and uses its existing private SSH configuration. Structure inspection,
  system construction, topology/parameterization, MD and analysis run only on
  the final SSH host. A failure never falls back to controller Python.

The final target descriptor contains a host identity hash, UID, Python/release/
project paths and resource policy. The compute host verifies these values. The
model cannot supply another host, interpreter or shell command.

## Optional operator binding

Bio is disabled for an account without a valid operator binding. A production
operator prepares `accounts/bio-targets/<username>.json` outside Git, using
[platform-binding.example.json](../bio-agent/deploy/platform-binding.example.json).
`MDAGENT_BIO_CONFIG_DIR` can select another protected directory. The service must
own the directory and its protected parents; each registry file is mode `0600`,
regular, and not a symlink. `executionTarget.tenantId` must equal the actual
account name; `nodeId` must belong to it. Replace all example placeholders.

The registry contains:

| Field | Purpose |
| --- | --- |
| `schemaVersion` | `bio.platform-binding.v1` |
| `executionTarget` | Public descriptor obtained on the final compute host |
| `mode` | `node-world` or `node-ssh` |
| `allowedActions` | Explicit subset of the typed Bio/model/human action catalog |
| `gatewayPython`, `gatewayReleaseRoot`, `gatewayConnectionFile` | Required only for `node-ssh`; paths on the enrolled ingress node |
| `rosterRoots` | Optional snapshot of existing extra preset roots to retain |

The cloud platform currently discovers the original preset from the tenant's
`.agent-presets`. Bio adds an immutable system root under
`<MDAGENT_HOME>/bio-profiles/<username>/<binding-hash>/presets`; it does not set
another default agent/model/provider. If a deployment already has additional
configured roster roots, include them in `rosterRoots` before enabling Bio.
The public generated target/preset is outside the tenant's writable home and
contains no token or private SSH material. Scientific data stay on the compute
host; the local `bio-control` directory stores transport receipts only.

Tenant startup attaches the Bio root/UI only for a matching enrolled node. The
web routing selects that configured node when it is owned and online. Changes
to a binding recreate the tenant instance; a stale target hash is denied even
before the old instance has been reaped. Removing an account's binding disables
Bio but does not delete its projects, prior profiles, grants or results.

## Platform gate and human approval

The shipped `platform-gate.mjs` reads the actual `RemoteSubprocessRuntime` world
binding and derives `/v1/bio/access` on the same API origin. It uses the existing
per-instance `mdw_` token; no account token, global gate secret or model API key
is passed to the scientific child.

The API checks the short token, active account, node ownership/revocation/online
state, complete final-target hash, typed action and bounded session/workflow/
revision fields. The returned decision must repeat every binding field. Raw
commands are rejected. This endpoint grants platform access; compute execution
still requires the independently checked, target-bound human scientific grant.

The web task panel uses the existing authenticated per-tenant Connection RPC
`/bio-workflow`. Human approval creates `READY`; submitting is a separate action.
The Bio session exposes only its typed tools, including masking inherited shell
tools. The original material session retains its own tool catalog.

## Existing deployment prerequisites

Use the existing authenticated nginx-to-tenant gateway, enrollment relay and
compatible DSH `0.1.0-rc.6` node shell. The original repository's simple nginx
API example is not a complete tenant web deployment; production nginx/systemd
configuration and node-shell binaries were deliberately not exported. This PR
does not supply or install a new node shell. Final-node execution and enrollment
bootstrap remain unverified.

The source runtime and workspace plugins must be readable by tenant UIDs, while
`cloud/`, account registries and credentials remain private. Do not recursively
expose an environment, data directory or entire deployment tree to achieve this.
The workspace lock change adds three importers using existing pinned dependency
resolutions; no dependency version was upgraded. A clean dependency installation
has not been performed. Local engineering checks reused DSH rc.6, TypeBox 1.1.38,
Schemastery 3.18.4 and Cordis 4.0.4; the lock pins Schemastery 3.18.1/Cordis 4.0.1,
so exact production-version acceptance remains open.

`MDAGENT_WEB_TRUSTED_HOSTS` sets a comma-separated public host allowlist; its
default is loopback. `MDAGENT_WEB_ORIGIN` sets the external handoff origin; absent
configuration returns a same-origin relative URL. Existing runtime config was
not copied or changed. HPC routes mount when the existing `MDAGENT_HPC_SECRET`
is configured; the merged SSH/Slurm module and encrypted data are retained.

External integration tokens cannot rotate the primary API token, change the
account password or change the provider API key. These three account-management
routes require a primary token or an authenticated browser session, even when
an external bearer token accompanies a browser cookie.

The `MDAGENT_HOME` service base must be service-owned (or root-owned), without
group/world write permission. Tenant home components must be real directories.
Overlay/gate files are atomically published as private `0600` regular files
using directory descriptors, no-follow opens and descriptor ownership changes.
Planted destination links and hard links fail closed. `web.log` must be a
service-owned regular file with one link; it is opened as `0600`, and exit/status
handling uses the same descriptor. An explicitly configured single-user
`MDAGENT_SPIKE_DSH_HOME` also requires a protected parent. Startup rejection
revokes the new instance ticket; it does not repair unsafe filesystem entries.

Remote provider disposal releases shared-world references using Cordis effects.
The final release closes the channel. Process stdin/EOF wait for spawn success;
terminal exit, disconnect, startup refusal and cancellation end the local
handle once. Disconnect/cancellation report an unknown remote process outcome,
not successful remote quiescence.

## Offline checks

From repository root, with already installed compatible dependencies:

```bash
node scripts/build-bio-ui.mjs
PYTHONPATH=/absolute/repository/cloud python3 -B -m pytest -p no:cacheprovider \
  cloud/tests/test_bio.py -q
PYTHONPATH=/absolute/repository/cloud python3 -B -m pytest -p no:cacheprovider \
  cloud/tests/test_review_security.py -q
BIO_TEST_PYTHON=/absolute/existing/python node --test \
  bio-agent/agent/tests/*.test.mjs \
  bio-agent/web/runtime/tests/*.test.mjs \
  dsh-plugins/dsh-bio-agent/test/plugin.test.mjs \
  dsh-plugins/dsh-remote-world/test/subprocess.test.mjs
```

Run legacy cloud modules in separate processes; their existing module-level
temporary database setup does not support a shared pytest process. Auth, bridge
and cloud fixtures explicitly use isolated open-signup mode; the separate Bio
test checks that production signup still requires an invitation. Their credential
assertions now verify that only a proxy token resides in a tenant's DSH home.
The remote-world regression uses a loopback WebSocket fixture and the real
provider/Cordis/Bio bridge chain; it does not connect a real enrolled node or SSH
host. The transfer-budget regression uses sparse synthetic files over 64 MiB,
real manifests and hash checks, with independent scientific acceptance stubbed.

These are engineering checks only. Real login/browser routing, SSH hops,
Linux/CUDA, restart continuity on a remote host, scientific outcomes and customer
dialogue remain deferred. Current Bio scientific methods do not cover every
biological system; consult the capability tool and workflow documentation.
# 可选网页显示集成

新增 `preview:*` action 仅授权固定预览目录和派生服务。`bio_view` / `bio_observe` 与 `view:*` 属于独立显示目录；基础 Bio 会话资格仍只检查原 11 个科学工具，并使用 `agent.ctx.tools.schemas(agent)`。旧操作者 binding 保持科学工作流权限；新功能需明确开启。

公网 `/v1/bio/preview/*` 和 `/v1/bio/visualization/*` 沿用网页登录 cookie/API 身份；tenant loopback relay 另验每实例 world token、scoped Agent、节点与目标。发送前复核异步权限和来源，取消后的块不会交给 renderer。所有租户及 renderer 冷资源共用 4 Mbps 裁决器。多 worker 无共享裁决后端时拒绝开启。

用户入口、独立依赖、发布资源和本地验收见 [网页结构与轨迹预览](../bio-agent/docs/web-visualization.md)。本地组件/WebGL/限速证据分别记录；源码包、部署与远程科学接受仍各自验证。
