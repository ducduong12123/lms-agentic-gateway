$ErrorActionPreference = "Stop"
$nameServerLine = (wsl.exe -d Ubuntu -- cat /etc/resolv.conf | Select-String '^nameserver' | Select-Object -First 1).ToString()
$wslHost = ($nameServerLine -split '\s+')[1].Trim()
if (-not $wslHost) {
    throw "Cannot discover the Windows host address from WSL."
}
$compose = "cd /mnt/d/lms-agentic-gateway && DOCKER_LLM_BASE_URL=http://${wslHost}:20128/v1 docker compose up -d --build"
wsl.exe -d Ubuntu -- bash -lc $compose
Write-Host "LMS AI: http://localhost:8080/lms"
Write-Host "Engine: http://127.0.0.1:8001/health"
