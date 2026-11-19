# ============================================================
# 本工作区专属 dsh 启动器(Windows) —— 锁定独立 DSH_HOME
# 用法:
#   .\run-dsh.ps1 web --host 127.0.0.1 --port 3091          # 起 web UI(独立实例)
#   .\run-dsh.ps1 --profile headless "现在模拟跑到哪了"       # 无头单任务
#   .\run-dsh.ps1 --profile tui                               # 终端交互模式(2026-08-30 新增)
# 环境:
#   MDSIM_DATA_ROOT     = <上级目录>\mdsim-data  (数据根外置 2026-08-29;所有机器写的文件都在这)
#   DSH_HOME            = <数据根>\dsh-home  (2026-08-29 阶段5 外置;与 zerocarbon/量化 彻底隔离)
#   DSH_PERMISSION_MODE 默认 danger-full-access(2026-08-29 用户拍板:全自主决策;
#     想回到审批模式先 set DSH_PERMISSION_MODE=workspace-write)
# ============================================================
param([Parameter(ValueFromRemainingArguments = $true)] $DshArgs)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $env:MDSIM_DATA_ROOT) { $env:MDSIM_DATA_ROOT = Join-Path (Split-Path -Parent $here) "mdsim-data" }
if (-not $env:DSH_HOME) { $env:DSH_HOME = Join-Path $env:MDSIM_DATA_ROOT "dsh-home" }  # 与 run-dsh.sh 一致:尊重预设
if (-not $env:DSH_PERMISSION_MODE) { $env:DSH_PERMISSION_MODE = "danger-full-access" }

Set-Location $here

# 与 run-dsh.sh 行为一致(二轮对抗补齐):--profile headless(两 token 或等号形式)且未显式
# 给 --patch 时,自动叠本工作区 persona 补丁;漏传 = 静默退回 stock harness(病灶 D-01)
$NeedPatch = $false
$HasPatch = $false
if ($DshArgs) {
  for ($i = 0; $i -lt $DshArgs.Count; $i++) {
    if ($DshArgs[$i] -eq "--profile" -and $DshArgs[$i + 1] -eq "headless") { $NeedPatch = $true }
    if ($DshArgs[$i] -eq "--profile=headless") { $NeedPatch = $true }
    if ($DshArgs[$i] -eq "--patch" -or $DshArgs[$i] -like "--patch=*") { $HasPatch = $true }
  }
}
if ($NeedPatch -and -not $HasPatch) {
  $DshArgs = @("--patch", (Join-Path $here "md-sim-assistant.headless.patch.yml")) + $DshArgs
  Write-Host "[run-dsh] headless 自动叠加 --patch md-sim-assistant.headless.patch.yml" -ForegroundColor Cyan
}

# 与 run-dsh.sh 行为一致(2026-08-30 tui 终端模式):tui profile 的两个上游缺口——
# --model 缺省时 persona {{model}} 装配即崩;交互路径 preset 硬默认 "standard" 不读
# settings 的 agent-presets.default。均按 settings.yaml 解析后补显式 app 参数,
# 插在 --profile 之后(启动器只认队首选项),用户显式给了就不动。
$IsTui = $false; $HasModel = $false; $HasPreset = $false
if ($DshArgs) {
  for ($i = 0; $i -lt $DshArgs.Count; $i++) {
    if ($DshArgs[$i] -eq "--profile" -and $DshArgs[$i + 1] -eq "tui") { $IsTui = $true }
    if ($DshArgs[$i] -eq "--profile=tui") { $IsTui = $true }
    if ($DshArgs[$i] -eq "--model" -or $DshArgs[$i] -like "--model=*") { $HasModel = $true }
    if ($DshArgs[$i] -eq "--preset" -or $DshArgs[$i] -like "--preset=*") { $HasPreset = $true }
  }
}
if ($IsTui) {
  $TuiExtra = @()
  $SettingsPath = Join-Path $env:DSH_HOME "settings.yaml"
  if (-not $HasModel -and (Test-Path $SettingsPath)) {
    $m = [regex]::Match((Get-Content $SettingsPath -Raw), "(?ms)^agent-default-model:\s*\n\s*provider:\s*(\S+)\s*\n\s*model:\s*(\S+)")
    if ($m.Success) {
      $TuiExtra += @("--model", "$($m.Groups[1].Value)/$($m.Groups[2].Value)")
      Write-Host "[run-dsh] tui 自动补 --model $($m.Groups[1].Value)/$($m.Groups[2].Value)(源自 settings.yaml agent-default-model)" -ForegroundColor Cyan
    } else {
      Write-Host "[run-dsh] ⚠️ tui 未能解析 agent-default-model,persona 含 {{model}} 时会启动失败" -ForegroundColor Yellow
    }
  }
  if (-not $HasPreset -and (Test-Path $SettingsPath)) {
    $p = [regex]::Match((Get-Content $SettingsPath -Raw), "(?ms)^agent-presets:\s*\n\s*default:\s*(\S+)")
    if ($p.Success) {
      $TuiExtra += @("--preset", $p.Groups[1].Value)
      Write-Host "[run-dsh] tui 自动补 --preset $($p.Groups[1].Value)(源自 settings.yaml agent-presets.default)" -ForegroundColor Cyan
    }
  }
  if ($TuiExtra.Count -gt 0) {
    $NewArgs = @()
    for ($i = 0; $i -lt $DshArgs.Count; $i++) {
      $NewArgs += $DshArgs[$i]
      if ($DshArgs[$i] -eq "--profile") { $NewArgs += $DshArgs[$i + 1]; $NewArgs += $TuiExtra; $i++ }
      if ($DshArgs[$i] -eq "--profile=tui") { $NewArgs += $TuiExtra }
    }
    $DshArgs = $NewArgs
  }
}

Write-Host "[run-dsh] DSH_HOME=$($env:DSH_HOME)  MODE=$($env:DSH_PERMISSION_MODE)" -ForegroundColor Cyan
pnpm exec dsh @DshArgs
