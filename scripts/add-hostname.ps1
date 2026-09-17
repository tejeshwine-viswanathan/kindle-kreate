# Adds a permanent hosts-file entry so "kindle-kreate.localhost" resolves everywhere,
# not only in browsers. Re-launches itself elevated (one UAC prompt). Idempotent.
$name = "kindle-kreate.localhost"
$hosts = "$env:SystemRoot\System32\drivers\etc\hosts"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}
if (Select-String -Path $hosts -Pattern "^\s*127\.0\.0\.1\s+$([regex]::Escape($name))\s*$" -Quiet) {
    Write-Host "$name already in hosts file."
} else {
    Add-Content -Path $hosts -Value "`r`n127.0.0.1 $name" -Encoding ascii
    Write-Host "Added 127.0.0.1 $name to hosts file."
}
ipconfig /flushdns | Out-Null
