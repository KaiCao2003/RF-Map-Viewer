[CmdletBinding()]
param(
    [string]$PythonBin = "python",
    [string]$BuildRoot = "",
    [string]$IsccPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$AppName = "RF Map Viewer"
$ExecutableName = "RF Map Viewer.exe"
$AppVersion = "1.9.6"
$AppBuild = "10908"
$ReleaseEdition = "Full"
$ReleaseFlavor = "full"
$Architecture = "x64"

$PyInstallerVersion = "6.21.0"
$NumpyVersion = "2.4.6"
$PillowVersion = "12.3.0"
$TkinterDnD2Version = "0.6.2"

$ScriptDir = Split-Path -Parent $PSCommandPath
$RootDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$RepoRoot = (Resolve-Path (Join-Path $RootDir "..")).Path
$GuiPath = Join-Path $RootDir "rfmapping_gui.py"
$RequirementsPath = Join-Path $RootDir "requirements.txt"
$ReadmePath = Join-Path $RootDir "README.md"
$SmokeJson = Join-Path $RootDir "tests\fixtures\release_smoke_rf.json"
$IconPath = Join-Path $RootDir "assets\rf-mapping-viewer-icon-1024.png"
$HooksDir = Join-Path $RootDir "packaging\pyinstaller-hooks"
$TkinterDnDHook = Join-Path $HooksDir "hook-tkinterdnd2.py"
$TkinterRuntimeHookBackport = Join-Path $HooksDir "rthooks\pyi_rth__tkinter.py"
$TkinterRuntimeHookPatcher = Join-Path $ScriptDir "patch_pyinstaller_tk9_runtime_hook.py"
$InstallerScript = Join-Path $RootDir "packaging\windows\RFMapViewer.iss"
$MetadataAuditor = Join-Path $ScriptDir "verify_python_stable_release_metadata.py"
$VersionVerifier = Join-Path $RepoRoot "release\verify_versions.py"
$VersionManifest = Join-Path $RepoRoot "release\versions.json"

$PortableName = "RF_Map_Viewer-python-$AppVersion-$ReleaseFlavor-windows-$Architecture-portable.zip"
$SetupBaseName = "RF_Map_Viewer-python-$AppVersion-$ReleaseFlavor-windows-$Architecture-setup"
$SetupName = "$SetupBaseName.exe"
$ChecksumName = "SHA256SUMS-python-$AppVersion-$ReleaseFlavor-windows-$Architecture.txt"
$DistDir = Join-Path $RootDir "dist\windows"
$PortablePath = Join-Path $DistDir $PortableName
$SetupPath = Join-Path $DistDir $SetupName
$ChecksumPath = Join-Path $DistDir $ChecksumName

function Fail([string]$Message) {
    throw "Windows stable build failed: $Message"
}

function Assert-NativeSuccess([string]$Description) {
    if ($LASTEXITCODE -ne 0) {
        Fail "$Description exited with code $LASTEXITCODE"
    }
}

function Assert-File([string]$Path, [string]$Description) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Fail "$Description is missing: $Path"
    }
    if ((Get-Item -LiteralPath $Path).Length -le 0) {
        Fail "$Description is empty: $Path"
    }
}

function Assert-Equal([object]$Actual, [object]$Expected, [string]$Description) {
    if ([string]$Actual -cne [string]$Expected) {
        Fail "$Description is '$Actual'; expected '$Expected'"
    }
}

function Invoke-WindowedSmoke(
    [string]$Executable,
    [string[]]$Arguments,
    [string]$Description,
    [string]$ReportPath
) {
    Remove-Item -LiteralPath $ReportPath -Force -ErrorAction SilentlyContinue
    $PreviousReportExists = Test-Path Env:RF_MAPPING_WINDOWED_SMOKE_REPORT
    $PreviousReport = if ($PreviousReportExists) {
        $env:RF_MAPPING_WINDOWED_SMOKE_REPORT
    } else {
        $null
    }
    $env:RF_MAPPING_WINDOWED_SMOKE_REPORT = $ReportPath
    $Process = $null
    try {
        $Process = Start-Process `
            -FilePath $Executable `
            -ArgumentList $Arguments `
            -PassThru
        if (-not $Process.WaitForExit(120000)) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
            $Process.WaitForExit()
            $Detail = if (Test-Path -LiteralPath $ReportPath -PathType Leaf) {
                Get-Content -LiteralPath $ReportPath -Raw
            } else {
                "no windowed smoke report was created"
            }
            Fail "$Description timed out after 120 seconds; report: $Detail"
        }
        Assert-File $ReportPath "$Description report"
        try {
            $Report = Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json
        } catch {
            Fail "$Description produced an invalid report: $($_.Exception.Message)"
        }
        if ($Process.ExitCode -ne 0) {
            $Detail = Get-Content -LiteralPath $ReportPath -Raw
            Fail "$Description exited with code $($Process.ExitCode); report: $Detail"
        }
        Assert-Equal $Report.status "success" "$Description report status"
        Assert-Equal $Report.exitCode 0 "$Description report exit code"
    } finally {
        if ($null -ne $Process) {
            $Process.Dispose()
        }
        if ($PreviousReportExists) {
            $env:RF_MAPPING_WINDOWED_SMOKE_REPORT = $PreviousReport
        } else {
            Remove-Item Env:RF_MAPPING_WINDOWED_SMOKE_REPORT -ErrorAction SilentlyContinue
        }
    }
}

function Resolve-Iscc([string]$RequestedPath) {
    $Candidates = [System.Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        $Candidates.Add($RequestedPath)
    }
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $Candidates.Add((Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"))
    }
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $Candidates.Add((Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"))
    }
    $Command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($null -ne $Command) {
        $Candidates.Add($Command.Source)
    }
    foreach ($Candidate in $Candidates) {
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }
    Fail "Inno Setup 6 compiler was not found; install it or pass -IsccPath"
}

function Assert-SafeBuildRoot([string]$Path) {
    $FullPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $Leaf = Split-Path -Leaf $FullPath
    $DriveRoot = [System.IO.Path]::GetPathRoot($FullPath).TrimEnd('\', '/')
    if ([string]::IsNullOrWhiteSpace($Leaf) -or -not $Leaf.StartsWith("rfmapping-stable-windows-")) {
        Fail "build root basename must start with 'rfmapping-stable-windows-': $FullPath"
    }
    foreach ($Forbidden in @($DriveRoot, $RootDir, $RepoRoot, $DistDir)) {
        if (-not [string]::IsNullOrWhiteSpace($Forbidden) -and $FullPath -ieq $Forbidden.TrimEnd('\', '/')) {
            Fail "refusing unsafe build root: $FullPath"
        }
    }
    $AllowedParents = @([System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/'))
    if (-not [string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
        $AllowedParents += [System.IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\', '/')
    }
    $UnderAllowedParent = $false
    foreach ($AllowedParent in $AllowedParents) {
        if ($FullPath.StartsWith("$AllowedParent\", [System.StringComparison]::OrdinalIgnoreCase)) {
            $UnderAllowedParent = $true
            break
        }
    }
    if (-not $UnderAllowedParent) {
        Fail "build root must be below the system temp or RUNNER_TEMP directory: $FullPath"
    }
    if (Test-Path -LiteralPath $FullPath -PathType Leaf) {
        Fail "build root is a file: $FullPath"
    }
    if (Test-Path -LiteralPath $FullPath) {
        $Attributes = (Get-Item -LiteralPath $FullPath -Force).Attributes
        if (($Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail "build root may not be a reparse point: $FullPath"
        }
    }
    return $FullPath
}

function Invoke-FrozenSmoke(
    [string]$Executable,
    [string]$Fixture,
    [string]$ExportRoot,
    [string]$Label
) {
    Assert-File $Executable "$Label executable"
    Invoke-WindowedSmoke `
        $Executable `
        @("--self-test", "`"$Fixture`"") `
        "$Label data self-test" `
        "$ExportRoot-data-smoke-report.json"
    Invoke-WindowedSmoke `
        $Executable `
        @("--self-test-dnd") `
        "$Label TkDND self-test" `
        "$ExportRoot-tkdnd-smoke-report.json"
    Invoke-WindowedSmoke `
        $Executable `
        @("--self-test-export", "`"$ExportRoot`"") `
        "$Label figure export self-test" `
        "$ExportRoot-figure-export-smoke-report.json"
    Assert-File (Join-Path $ExportRoot "figure-export-smoke.pdf") "$Label PDF export"
    Assert-File `
        (Join-Path $ExportRoot "figure-export-smoke\manifest.json") `
        "$Label PNG manifest"
    Assert-File (Join-Path $ExportRoot "displayed-data-smoke.csv") "$Label CSV export"
}

function Assert-WindowsVersionResource([string]$Path, [string]$Label) {
    $VersionInfo = (Get-Item -LiteralPath $Path).VersionInfo
    Assert-Equal $VersionInfo.ProductName $AppName "$Label product name"
    Assert-Equal $VersionInfo.ProductVersion $AppVersion "$Label product version"
    Assert-Equal $VersionInfo.FileVersion "$AppVersion.$AppBuild" "$Label file version"
}

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    Fail "this builder must run on Windows"
}

foreach ($Required in @(
    $GuiPath,
    $RequirementsPath,
    $ReadmePath,
    $SmokeJson,
    $IconPath,
    $TkinterDnDHook,
    $TkinterRuntimeHookBackport,
    $TkinterRuntimeHookPatcher,
    $InstallerScript,
    $MetadataAuditor,
    $VersionVerifier,
    $VersionManifest
)) {
    Assert-File $Required "required release input"
}

$PythonTarget = (& $PythonBin -c 'import platform, struct, sys; print(f"{sys.version_info.major}.{sys.version_info.minor}-{struct.calcsize(chr(80)) * 8}-{platform.machine()}")').Trim()
Assert-NativeSuccess "Python target inspection"
Assert-Equal $PythonTarget "3.14-64-AMD64" "build Python target"

& $PythonBin $VersionVerifier --tag "python-v$AppVersion"
Assert-NativeSuccess "canonical release version verification"
& $PythonBin $MetadataAuditor $RootDir $AppVersion $ReleaseEdition
Assert-NativeSuccess "stable Python metadata verification"

$Manifest = Get-Content -LiteralPath $VersionManifest -Raw | ConvertFrom-Json
$StableManifest = $Manifest.components.python_stable
Assert-Equal $StableManifest.release_version $AppVersion "manifest Python stable release"
Assert-Equal $StableManifest.build $AppBuild "manifest Python stable build"
Assert-Equal $StableManifest.edition $ReleaseEdition "manifest Python stable edition"
Assert-Equal $StableManifest.artifact_flavor $ReleaseFlavor "manifest Python stable flavor"

if ([string]::IsNullOrWhiteSpace($BuildRoot)) {
    $TemporaryParent = if (-not [string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
        $env:RUNNER_TEMP
    } else {
        [System.IO.Path]::GetTempPath()
    }
    $BuildRoot = Join-Path $TemporaryParent "rfmapping-stable-windows-$AppVersion-$Architecture"
}
$BuildRoot = Assert-SafeBuildRoot $BuildRoot

if (Test-Path -LiteralPath $BuildRoot) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $BuildRoot | Out-Null
New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
foreach ($Output in @($PortablePath, $SetupPath, $ChecksumPath)) {
    Remove-Item -LiteralPath $Output -Force -ErrorAction SilentlyContinue
}

$VenvDir = Join-Path $BuildRoot "venv"
& $PythonBin -m venv $VenvDir
Assert-NativeSuccess "build virtual environment creation"
$BuildPython = Join-Path $VenvDir "Scripts\python.exe"
Assert-File $BuildPython "build Python"

& $BuildPython -m pip install --disable-pip-version-check `
    "pyinstaller==$PyInstallerVersion" `
    "numpy==$NumpyVersion" `
    "pillow==$PillowVersion" `
    "tkinterdnd2==$TkinterDnD2Version"
Assert-NativeSuccess "pinned Windows packaging dependency installation"

$DependencyProbe = @'
import importlib.metadata as metadata
import platform
import PyInstaller
import numpy
import PIL
import tkinter

expected = {
    "PyInstaller": "6.21.0",
    "numpy": "2.4.6",
    "Pillow": "12.3.0",
    "tkinterdnd2": "0.6.2",
}
actual = {
    "PyInstaller": PyInstaller.__version__,
    "numpy": numpy.__version__,
    "Pillow": PIL.__version__,
    "tkinterdnd2": metadata.version("tkinterdnd2"),
}
if actual != expected or platform.machine() != "AMD64":
    raise SystemExit(f"unexpected Windows build environment: {actual}, {platform.machine()}")
print("Tk runtime:", tkinter.TkVersion)
'@
& $BuildPython -c $DependencyProbe
Assert-NativeSuccess "pinned Windows packaging dependency verification"

# PyInstaller 6.21.0 predates upstream support for Python 3.14's Windows
# Tcl/Tk 9 layout, which keeps its script libraries in DLL-embedded zipfs
# archives. The patcher refuses to touch any other PyInstaller version, Tcl
# library layout, Tk major version, or installed-hook revision.
& $BuildPython $TkinterRuntimeHookPatcher $TkinterRuntimeHookBackport
Assert-NativeSuccess "PyInstaller Tcl/Tk 9 runtime-hook backport"

$VersionFile = Join-Path $BuildRoot "RFMapViewer-version-info.txt"
$VersionResource = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 9, 6, 10908),
    prodvers=(1, 9, 6, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [
          StringStruct(u'CompanyName', u'KaiCao2003'),
          StringStruct(u'FileDescription', u'RF Map Viewer Python stable'),
          StringStruct(u'FileVersion', u'$AppVersion.$AppBuild'),
          StringStruct(u'InternalName', u'RF Map Viewer'),
          StringStruct(u'OriginalFilename', u'$ExecutableName'),
          StringStruct(u'ProductName', u'$AppName'),
          StringStruct(u'ProductVersion', u'$AppVersion')
        ]
      )
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"@
Set-Content -LiteralPath $VersionFile -Value $VersionResource -Encoding Ascii

$InstallerIcon = Join-Path $BuildRoot "RFMappingViewer.ico"
$IconConverter = @'
from pathlib import Path
import sys
from PIL import Image

source, destination = map(Path, sys.argv[1:3])
with Image.open(source) as image:
    image.convert("RGBA").save(
        destination,
        format="ICO",
        sizes=((16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)),
    )
'@
& $BuildPython -c $IconConverter $IconPath $InstallerIcon
Assert-NativeSuccess "Windows icon generation"
Assert-File $InstallerIcon "Windows installer icon"

$PyInstallerDist = Join-Path $BuildRoot "pyinstaller-dist"
$PyInstallerWork = Join-Path $BuildRoot "pyinstaller-work"
$PyInstallerSpec = Join-Path $BuildRoot "pyinstaller-spec"
New-Item -ItemType Directory -Path $PyInstallerDist, $PyInstallerWork, $PyInstallerSpec | Out-Null

$ReadmeDataArgument = "$ReadmePath;."
& $BuildPython -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --noupx `
    --name $AppName `
    --icon $InstallerIcon `
    --version-file $VersionFile `
    --distpath $PyInstallerDist `
    --workpath $PyInstallerWork `
    --specpath $PyInstallerSpec `
    --additional-hooks-dir $HooksDir `
    --add-data $ReadmeDataArgument `
    $GuiPath
Assert-NativeSuccess "PyInstaller onedir build"

$FrozenSource = Join-Path $PyInstallerDist $AppName
$FrozenExecutable = Join-Path $FrozenSource $ExecutableName
Assert-File $FrozenExecutable "PyInstaller executable"
Assert-WindowsVersionResource $FrozenExecutable "portable executable"

$TkDndDlls = @(Get-ChildItem -LiteralPath $FrozenSource -Recurse -File -Filter "*.dll" | Where-Object {
    $_.FullName -match '[\\/]tkinterdnd2[\\/]tkdnd[\\/]win-x64(?:-tcl9)?[\\/]'
})
if ($TkDndDlls.Count -eq 0) {
    Fail "PyInstaller output is missing the Windows x64 TkDND runtime"
}

$StageDir = Join-Path $BuildRoot "portable-stage\$AppName"
$StageApp = Join-Path $StageDir "App"
$StageResources = Join-Path $StageDir "Resources"
New-Item -ItemType Directory -Path $StageApp, $StageResources -Force | Out-Null
Copy-Item -Path (Join-Path $FrozenSource "*") -Destination $StageApp -Recurse -Force
Copy-Item -LiteralPath $ReadmePath -Destination (Join-Path $StageResources "README.md") -Force

$StageExecutable = Join-Path $StageApp $ExecutableName
Invoke-FrozenSmoke `
    $StageExecutable `
    $SmokeJson `
    (Join-Path $BuildRoot "smoke-staged") `
    "staged portable"

Compress-Archive -Path (Join-Path $StageDir "*") -DestinationPath $PortablePath -CompressionLevel Optimal
Assert-File $PortablePath "portable ZIP"

Add-Type -AssemblyName System.IO.Compression.FileSystem
$Zip = [System.IO.Compression.ZipFile]::OpenRead($PortablePath)
try {
    $ZipEntries = @($Zip.Entries | ForEach-Object { $_.FullName.Replace('\', '/') })
    foreach ($RequiredEntry in @("App/$ExecutableName", "Resources/README.md")) {
        if ($ZipEntries -cnotcontains $RequiredEntry) {
            Fail "portable ZIP is missing $RequiredEntry"
        }
    }
    if ($ZipEntries | Where-Object {
        $_ -match '(?i)(^|/)[^/]+\.(rfmap|tc|probe)$|(^|/)release_smoke_rf\.json$'
    }) {
        Fail "portable ZIP must not bundle RF, tuning-curve, probe, or smoke sample data"
    }
    if (-not ($ZipEntries | Where-Object { $_ -match '^App/(?:_internal/)?tkinterdnd2/tkdnd/win-x64(?:-tcl9)?/.+\.dll$' })) {
        Fail "portable ZIP is missing the Windows x64 TkDND runtime"
    }
} finally {
    $Zip.Dispose()
}

$ExtractedDir = Join-Path $BuildRoot "portable-extracted"
Expand-Archive -LiteralPath $PortablePath -DestinationPath $ExtractedDir
$ExtractedExecutable = Join-Path $ExtractedDir "App\$ExecutableName"
Invoke-FrozenSmoke `
    $ExtractedExecutable `
    $SmokeJson `
    (Join-Path $BuildRoot "smoke-extracted") `
    "extracted portable"
Assert-WindowsVersionResource $ExtractedExecutable "extracted portable executable"

$ResolvedIscc = Resolve-Iscc $IsccPath
$IsccArguments = @(
    "/DSourceRoot=$StageDir",
    "/DMyAppVersion=$AppVersion",
    "/DMyAppBuild=$AppBuild",
    "/DOutputDir=$DistDir",
    "/DOutputBaseFilename=$SetupBaseName",
    "/DSetupIconFile=$InstallerIcon",
    $InstallerScript
)
& $ResolvedIscc @IsccArguments
Assert-NativeSuccess "Inno Setup compilation"
Assert-File $SetupPath "Windows setup executable"
Assert-WindowsVersionResource $SetupPath "Windows setup executable"

$InstallDir = Join-Path $BuildRoot "installed\$AppName"
$InstallArguments = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/DIR=`"$InstallDir`""
)
$InstallerProcess = Start-Process -FilePath $SetupPath -ArgumentList $InstallArguments -Wait -PassThru
if ($InstallerProcess.ExitCode -ne 0) {
    Fail "installer smoke test exited with code $($InstallerProcess.ExitCode)"
}

$InstalledExecutable = Join-Path $InstallDir "App\$ExecutableName"
Invoke-FrozenSmoke `
    $InstalledExecutable `
    $SmokeJson `
    (Join-Path $BuildRoot "smoke-installed") `
    "installed application"
Assert-WindowsVersionResource $InstalledExecutable "installed executable"
Assert-File (Join-Path $InstallDir "Resources\README.md") "installed README"

$Uninstaller = Join-Path $InstallDir "unins000.exe"
Assert-File $Uninstaller "Windows uninstaller"
$UninstallProcess = Start-Process -FilePath $Uninstaller -ArgumentList @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART"
) -Wait -PassThru
if ($UninstallProcess.ExitCode -ne 0) {
    Fail "installer cleanup exited with code $($UninstallProcess.ExitCode)"
}

$ChecksumLines = foreach ($Artifact in @($PortablePath, $SetupPath)) {
    $Hash = Get-FileHash -LiteralPath $Artifact -Algorithm SHA256
    "{0}  {1}" -f $Hash.Hash.ToLowerInvariant(), (Split-Path -Leaf $Artifact)
}
Set-Content -LiteralPath $ChecksumPath -Value $ChecksumLines -Encoding Ascii
Assert-File $ChecksumPath "Windows SHA-256 checksum"

foreach ($Line in Get-Content -LiteralPath $ChecksumPath) {
    if ($Line -notmatch '^([0-9a-f]{64})  (.+)$') {
        Fail "invalid checksum line: $Line"
    }
    $ExpectedHash = $Matches[1]
    $ArtifactPath = Join-Path $DistDir $Matches[2]
    Assert-File $ArtifactPath "checksummed artifact"
    $ActualHash = (Get-FileHash -LiteralPath $ArtifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-Equal $ActualHash $ExpectedHash "SHA-256 for $($Matches[2])"
}

Write-Output "Built Python $AppVersion $ReleaseEdition Windows $Architecture application."
Write-Output "Created portable archive: $PortablePath"
Write-Output "Created installer: $SetupPath"
Write-Output "Created checksum: $ChecksumPath"
