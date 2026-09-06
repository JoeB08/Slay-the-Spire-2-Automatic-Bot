<#
.SYNOPSIS
    Builds STS2MCP from source and installs it into a Slay the Spire 2 install.

.DESCRIPTION
    The mod's last tagged GitHub release predates several game-compatibility
    fixes on its main branch, so this builds from source (requires .NET 9 SDK)
    rather than downloading the release zip. Close the game first -- it locks
    the mod DLL while running.

.PARAMETER SourceDir
    Path to an extracted/cloned STS2MCP source tree (contains STS2_MCP.csproj).

.PARAMETER GameDir
    Path to the Slay the Spire 2 installation directory.

.EXAMPLE
    .\install_mod.ps1 -SourceDir "C:\path\to\STS2MCP-main" -GameDir "C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2"
#>
param(
    [Parameter(Mandatory = $true)][string]$SourceDir,
    [Parameter(Mandatory = $true)][string]$GameDir
)

$ErrorActionPreference = "Stop"

& (Join-Path $SourceDir "build.ps1") -GameDir $GameDir
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$modsDir = Join-Path $GameDir "mods"
New-Item -ItemType Directory -Force -Path $modsDir | Out-Null

Copy-Item (Join-Path $SourceDir "out\STS2_MCP\STS2_MCP.dll") (Join-Path $modsDir "STS2_MCP.dll") -Force
Copy-Item (Join-Path $SourceDir "mod_manifest.json") (Join-Path $modsDir "STS2_MCP.json") -Force

Write-Host "Installed STS2_MCP into $modsDir" -ForegroundColor Green
Write-Host "Launch the game, confirm mods are enabled in Settings, and check http://localhost:15526/api/v1/singleplayer" -ForegroundColor Green
