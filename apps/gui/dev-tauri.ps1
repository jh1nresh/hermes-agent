param(
  [string]$Command = "dev",
  [switch]$Fresh,
  [switch]$ResetFresh
)

$ErrorActionPreference = "Stop"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Join-Path $AppRoot "scripts\tauri.mjs"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  throw "Windows Node.js was not found. Install it with: winget install OpenJS.NodeJS.LTS"
}

if (-not (Get-Command rustc -ErrorAction SilentlyContinue)) {
  throw "Windows Rust was not found. Install it with: winget install Rustlang.Rustup"
}

$Tauri = Get-Command tauri -ErrorAction SilentlyContinue
$CargoTauri = Get-Command cargo-tauri -ErrorAction SilentlyContinue

if (-not $Tauri -and -not $CargoTauri) {
  throw "Tauri CLI not found. Install it with: npm install -g @tauri-apps/cli (run from a normal Windows path, not \\wsl$)"
}

$env:CARGO_INCREMENTAL = "0"
$env:CARGO_TARGET_DIR = Join-Path $env:LOCALAPPDATA "Hermes\cargo-target\gui"
New-Item -ItemType Directory -Force -Path $env:CARGO_TARGET_DIR | Out-Null

if ($Fresh) {
  $FreshHome = Join-Path $env:LOCALAPPDATA "Hermes\fresh-install-home"
  if ($ResetFresh -and (Test-Path $FreshHome)) {
    Remove-Item -Recurse -Force $FreshHome
  }
  New-Item -ItemType Directory -Force -Path $FreshHome | Out-Null
  $env:HERMES_HOME = $FreshHome
  $env:HERMES_GUI_PORT = "9140"
  $env:HERMES_GUI_FRESH = "1"
  Write-Host "Fresh GUI mode"
  Write-Host "  HERMES_HOME=$FreshHome"
  Write-Host "  HERMES_GUI_PORT=$env:HERMES_GUI_PORT"
}

Push-Location $AppRoot
try {
  if ($Tauri) {
    & tauri $Command
  }
  else {
    & cargo tauri $Command
  }
}
finally {
  Pop-Location
}
