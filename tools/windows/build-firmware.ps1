# Build the blueline Mu-Silicium firmware volume on Windows, with the EDK II setup of
# UniXNU-pocket-max tools/windows/setup-edk2.py plus tools/windows/build-basetools.py (both in EDK2_WIN_DIR).
#   .\tools\windows\build-firmware.ps1 [-Uefi ..\uefi-blueline] [-EdkWin C:\Users\user\edk2-win2] [-Target DEBUG]
#                                       [-Display vendor|simplefb] [-NoVendor] [-UniXNU <checkout>] [-Extra <package dirs>]
# Run scripts/import-blueline-uefi.py first. The FD lands in <Uefi>\Build\bluelinePkg\<Target>_CLANGPDB\FV and is
# copied to build\firmware\blueline-<stock|mu>-<display>-<target>.fd of this repository.
#   -Display vendor    the stock XBL DisplayDxe initializes the panel (USE_CUSTOM_DISPLAY_DRIVER=1, as polaris)
#   -Display simplefb  SimpleFbDxe takes over the framebuffer ABL left at 0x9D400000 (sheng's working setup)
#   -NoVendor          Mu drivers only, no stock XBL drivers (BLUELINE_VENDOR_HW=0, as sheng's working setup)
#   -UniXNU            a UniXNU checkout (package\UniXNUPkg, drivers\DarwinBoot); default refs\UniXNU-main
param(
  [string]$Uefi = (Join-Path $PSScriptRoot "..\..\uefi-blueline"),
  [string]$EdkWin = "C:\Users\user\edk2-win2",
  [string]$Target = "DEBUG",
  [ValidateSet("vendor", "simplefb")][string]$Display = "vendor",
  [switch]$NoVendor,
  [string]$UniXNU = (Join-Path $PSScriptRoot "..\..\refs\UniXNU-main"),
  [string[]]$Extra = @(),
  [string[]]$Define = @()
)
$ErrorActionPreference = "Stop"
$Uefi = (Resolve-Path $Uefi).Path
$UniXNU = (Resolve-Path $UniXNU).Path
if ($NoVendor -and $Display -eq "vendor") { throw "-NoVendor needs -Display simplefb (the stock DisplayDxe is a vendor driver)" }
$variant = "$(if ($NoVendor) { 'mu' } else { 'stock' })-$Display"
$config = Get-Content (Join-Path $Uefi "Resources\Configs\blueline.toml") -Raw
$base = [regex]::Match($config, '(?m)^base\s*=\s*(0x[0-9A-Fa-f]+)').Groups[1].Value
$size = [regex]::Match($config, '(?m)^size\s*=\s*(0x[0-9A-Fa-f]+)').Groups[1].Value
$blocks = [Convert]::ToInt64($size, 16) / 0x1000
# Package paths of Platforms/Google/bluelinePkg/DeviceBuild.py, with the checkout root first, plus $Extra
# (UniXNU's package and DarwinBoot when the firmware embeds them).
$packages = @($Uefi) + @("Platforms\Google", "Common\Mu", "Common\Mu_OEM_Sample", "Silicon\Qualcomm", "Silicon\Silicium",
                         "Silicium-ACPI", "Silicium-ACPI\Silicon\Qualcomm" | ForEach-Object { Join-Path $Uefi $_ }) +
            @((Join-Path $UniXNU "package"), (Join-Path $UniXNU "drivers\DarwinBoot")) + $Extra
$defines = @("FD_BASE=$base", "FD_SIZE=$size", "FD_BLOCKS=$blocks", "ENABLE_SECUREBOOT=0",
             "USE_CUSTOM_DISPLAY_DRIVER=$(if ($Display -eq 'vendor') { 1 } else { 0 })", "BLUELINE_VENDOR_HW=$(if ($NoVendor) { 0 } else { 1 })") + $Define
# tools_def's *_CLANGPDB_*_*_DLL is ENV(CLANGPDB_DLL); build.py prepends it to PATH, and an empty entry stops
# cmd.exe's PATH search, so GenFds (which runs GenSec and friends through the shell) finds no tool.
$env:CLANGPDB_DLL = Join-Path $EdkWin "bin"
# The Mu_Basecore patches Mu-Silicium's build_uefi.py applies to every build, and uefi-sheng's Gic-Spurious.patch
# (masks an interrupt that fires without a registered handler): applied to the shared edk2 checkout
# for this build and always reverted (as UniXNU's firmware step does with board.toml [firmware] patches).
$edk2 = Join-Path $EdkWin "mu_basecore"
$git = (Get-Command git).Source   # build.ps1 replaces PATH; keep git and the caller's PATH for the revert
$savedPath = $env:PATH
$patches = "AsmMacroLib", "Auth-Service", "Boot-Manager", "Timer", "Usb-Bus", "Gic-Spurious" | ForEach-Object { Join-Path $Uefi "Resources\MuPatches\$_.patch" }
$applied = @()
$code = 1
try {
  foreach ($p in $patches) {
    & $git -C $edk2 apply --check $p; if ($LASTEXITCODE) { throw "patch does not apply: $p" }
    & $git -C $edk2 apply $p; if ($LASTEXITCODE) { throw "patch failed: $p" }
    $applied += $p
  }
  & (Join-Path $EdkWin "bin\build.ps1") -Dsc bluelinePkg/blueline.dsc -Packages $packages -Workspace $Uefi -Target $Target -Define $defines
  $code = $LASTEXITCODE
} finally {
  $env:PATH = $savedPath
  [array]::Reverse($applied)
  foreach ($p in $applied) { & $git -C $edk2 apply --reverse $p; if ($LASTEXITCODE) { Write-Warning "revert failed: $p" } }
}
$fd = Join-Path $Uefi "Build\bluelinePkg\$($Target)_CLANGPDB\FV\SILICIUM_UEFI.fd"
if ($code -eq 0) {
  $len = (Get-Item $fd).Length
  if ($len -ne [Convert]::ToInt64($size, 16)) { throw "$fd is $len bytes, the device config says $size" }
  $outDir = Join-Path $PSScriptRoot "..\..\build\firmware"
  New-Item -ItemType Directory -Force $outDir | Out-Null
  $copy = Join-Path (Resolve-Path $outDir).Path "blueline-$variant-$($Target.ToLower()).fd"
  Copy-Item $fd $copy
  "firmware: $copy ($len bytes, sha256 $((Get-FileHash $copy -Algorithm SHA256).Hash.ToLower()))"
}
exit $code
