# One-command readback for the blueline port (run from the folder with adb, phone in TWRP):
#   powershell -ExecutionPolicy Bypass -File blueline-dump.ps1          the UFS diagnostic log only (32 MiB, a few seconds)
#   powershell -ExecutionPolicy Bypass -File blueline-dump.ps1 -Data    also the /var persistent store (hundreds of MiB)
# The log is the last 32 MiB of the darwin partition (package/BluelineLog.c, sheng-log=1): the kernel message buffer and the
# GPU status, decoded with scripts/blueline-log.py. -Data adds the store: it reads the bitmap, works out the written page runs
# and dumps exactly those pages. Everything is compressed to blueline-readback.7z (or .zip without 7-Zip). Reads only;
# writes nothing to the phone's storage.
param([switch]$Data)
$ErrorActionPreference = 'Stop'
$adb = if (Test-Path .\adb.exe) { '.\adb.exe' } else { 'adb' }
$logStart = 31201024; $logBlocks = 8192          # 0x1dc1700: darwin last_lba 0x1dc36ff + 1 - 8192
$bitmapStart = 9974912; $pagesBase = 9975552
$files = @('blueline-log.bin')

Write-Host 'Reading the log window...'
& $adb shell "dd if=/dev/block/sda of=/tmp/blueline-log.bin bs=4096 skip=$logStart count=$logBlocks 2>/dev/null"
& $adb pull /tmp/blueline-log.bin blueline-log.bin | Out-Null
$magic = [System.Text.Encoding]::ASCII.GetString([System.IO.File]::ReadAllBytes((Join-Path (Get-Location) 'blueline-log.bin')), 0, 8)
if ($magic -eq 'SHLOG001') { Write-Host 'Log header found.' } else { Write-Host 'No log header in the window (the writer did not run).' }

if ($Data) {
    Write-Host 'Reading the bitmap...'
    & $adb shell "dd if=/dev/block/sda of=/tmp/data-bitmap.bin bs=4096 skip=$bitmapStart count=640 2>/dev/null"
    & $adb pull /tmp/data-bitmap.bin data-bitmap.bin | Out-Null
    $bytes = [System.IO.File]::ReadAllBytes((Join-Path (Get-Location) 'data-bitmap.bin'))

    Write-Host 'Finding written pages...'
    $runs = New-Object System.Collections.Generic.List[long[]]
    $start = -1L; $count = 0L; $total = 0L
    for ($b = 0; $b -lt $bytes.Length; $b++) {
        $v = $bytes[$b]
        if ($v -eq 0) { continue }
        for ($k = 0; $k -lt 8; $k++) {
            if (($v -shr $k) -band 1) {
                $i = [long]$b * 8 + $k; $total++
                if ($count -gt 0 -and $i -eq $start + $count) { $count++ }
                else { if ($count -gt 0) { $runs.Add(@($start, $count)) }; $start = $i; $count = 1 }
            }
        }
    }
    if ($count -gt 0) { $runs.Add(@($start, $count)) }
    $expected = $total * 4096
    Write-Host "$total pages in $($runs.Count) runs ($expected bytes)"

    Write-Host 'Dumping the pages on the phone...'
    & $adb shell "rm -f /tmp/data-pages.bin"
    for ($c = 0; $c -lt $runs.Count; $c += 100) {
        $pairs = ($runs[$c..([Math]::Min($c + 99, $runs.Count - 1))] | ForEach-Object { "$($pagesBase + $_[0]) $($_[1])" }) -join ' '
        & $adb shell "set -- $pairs; while [ `$# -gt 0 ]; do dd if=/dev/block/sda bs=4096 skip=`$1 count=`$2 2>/dev/null; shift 2; done >> /tmp/data-pages.bin"
    }
    $size = (& $adb shell "wc -c < /tmp/data-pages.bin").Trim()
    if ([long]$size -ne $expected) { throw "page dump is $size bytes, expected $expected" }
    Write-Host 'Pulling...'
    & $adb pull /tmp/data-pages.bin data-pages.bin | Out-Null
    $files += 'data-bitmap.bin', 'data-pages.bin'
}

$sevenZip = 'C:\Program Files\7-Zip\7z.exe'
if (Test-Path $sevenZip) {
    Remove-Item -ErrorAction SilentlyContinue blueline-readback.7z
    & $sevenZip a -mx=5 blueline-readback.7z @files | Out-Null
    Write-Host 'Done: blueline-readback.7z'
} else {
    Compress-Archive -Force -Path $files -DestinationPath blueline-readback.zip
    Write-Host 'Done: blueline-readback.zip'
}
