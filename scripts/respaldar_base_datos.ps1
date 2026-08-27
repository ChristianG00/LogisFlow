param(
    [string]$Destino = [System.IO.Path]::Combine(
        [Environment]::GetFolderPath('MyDocuments'),
        'LogisFlow-Respaldos'
    )
)

$ErrorActionPreference = 'Stop'

# El respaldo se guarda fuera del proyecto para que no se pierda al actualizarlo
$raizProyecto = Split-Path -Parent $PSScriptRoot
$archivoEntorno = Join-Path $raizProyecto '.env'

if (-not (Test-Path -LiteralPath $archivoEntorno)) {
    throw 'No se encontró el archivo .env con la contraseña de la base de datos'
}

$lineaClave = Get-Content -LiteralPath $archivoEntorno | Where-Object {
    $_ -match '^\s*DB_PASSWORD\s*='
} | Select-Object -First 1

if (-not $lineaClave) {
    throw 'No se encontró DB_PASSWORD en el archivo .env'
}

$clave = ($lineaClave -replace '^\s*DB_PASSWORD\s*=\s*', '').Trim()
if (($clave.StartsWith('"') -and $clave.EndsWith('"')) -or
    ($clave.StartsWith("'") -and $clave.EndsWith("'"))) {
    $clave = $clave.Substring(1, $clave.Length - 2)
}

$pgDump = Get-Command 'pg_dump.exe' -ErrorAction SilentlyContinue
if (-not $pgDump) {
    $pgDump = Get-Command 'pg_dump' -ErrorAction SilentlyContinue
}

if (-not $pgDump) {
    $candidatos = Get-ChildItem 'C:\Program Files\PostgreSQL' -Filter 'pg_dump.exe' -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($candidatos) {
        $pgDump = $candidatos.FullName
    }
}

if (-not $pgDump) {
    throw 'No se encontró pg_dump. Instala PostgreSQL Client Tools y vuelve a ejecutar este script'
}

New-Item -ItemType Directory -Path $Destino -Force | Out-Null

$fecha = Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'
$archivoRespaldo = Join-Path $Destino "logisflow_$fecha.backup"
$variableAnterior = $env:PGPASSWORD

try {
    # Solo se expone la clave al proceso pg_dump durante este respaldo
    $env:PGPASSWORD = $clave
    & $pgDump --host 'postgresql-logisflow.alwaysdata.net' --port '5432' --username 'logisflow' --format custom --compress 9 --file $archivoRespaldo 'logisflow_logisflowdb'

    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $archivoRespaldo) -or
        (Get-Item -LiteralPath $archivoRespaldo).Length -eq 0) {
        throw 'pg_dump no pudo crear un respaldo válido'
    }

    $tamanoMB = [math]::Round((Get-Item -LiteralPath $archivoRespaldo).Length / 1MB, 2)
    Write-Host "Respaldo creado correctamente: $archivoRespaldo ($tamanoMB MB)" -ForegroundColor Green
}
finally {
    if ($null -eq $variableAnterior) {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
    else {
        $env:PGPASSWORD = $variableAnterior
    }
}
