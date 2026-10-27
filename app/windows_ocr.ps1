param(
    [Parameter(Mandatory = $true)][string]$Image,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Lang = "zh-Hans-CN"
)

# OCR an image with the Windows built-in engine (Windows.Media.Ocr) and write
# the recognized text as UTF-8 to $Out.
#
# IMPORTANT: keep this file pure ASCII. Windows PowerShell 5.1 decodes a .ps1
# without a BOM using the ANSI code page, so any non-ASCII bytes can corrupt
# parsing and silently break the script. Error codes are emitted as
# "ERROR: QBERR|<code>|<detail>" and translated to Chinese by app/ocr.py.

$ErrorActionPreference = 'Stop'

function Fail($msg) {
    throw [System.Exception]::new("QBFAIL|" + $msg)
}

trap {
    $m = $_.Exception.Message
    if (-not $m.StartsWith("QBFAIL|")) {
        $m = "unexpected|" + $m + " @ " + ($_.InvocationInfo.PositionMessage -replace "`r?`n", " ")
    } else {
        $m = $m.Substring(7)
    }
    [System.IO.File]::WriteAllText($Out, "ERROR: QBERR|" + $m, [System.Text.UTF8Encoding]::new($false))
    exit 0
}

Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

# WinRT async methods need an AsTask wrapper in PowerShell.
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

if ($null -eq $asTaskGeneric) {
    Fail "no-astask|WindowsRuntime extensions unavailable"
}

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

if (-not (Test-Path -LiteralPath $Image)) {
    Fail "no-file|" + $Image
}

try {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Image)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
} catch {
    Fail ("read-failed|" + $_.Exception.Message)
}

$engine = $null
if ($Lang) {
    try {
        $language = [Windows.Globalization.Language]::new($Lang)
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
    } catch {
        $engine = $null
    }
}
if ($null -eq $engine) {
    try {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    } catch {
        $engine = $null
    }
}
if ($null -eq $engine) {
    Fail ("no-language|" + $Lang)
}

try {
    $op = $engine.RecognizeAsync($bitmap)
    $result = Await $op ([Windows.Media.Ocr.OcrResult])
} catch {
    Fail ("recognize-failed|" + $_.Exception.Message)
}

$lines = @()
foreach ($line in $result.Lines) {
    $lines += $line.Text
}

[System.IO.File]::WriteAllText($Out, ($lines -join "`n"), [System.Text.UTF8Encoding]::new($false))
exit 0
