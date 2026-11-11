@echo off
cd /d "%~dp0"
title Sakanet - reiniciar estudios
echo.
echo  Esto BORRA todos los estudios de estrellas y reinicia el descubrimiento desde el lote 1.
echo  Se conservan: modelos, catalogos, curvas descargadas, ajustes y la lista de seguimiento.
echo  KIC 6881353 se copia antes a results\seguimiento\KIC_6881353.
echo.
set /p OK=Escribe SI para continuar:
if /i not "%OK%"=="SI" (echo Cancelado. & pause & exit /b 1)

echo Cerrando panel y ayudante locales...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -like '*panel_local.py*' -or $_.CommandLine -like '*ayudante_local*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
timeout /t 2 /nobreak >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$ErrorActionPreference='Stop'; $p='results\local_panel';" ^
 "$src=Join-Path $p '226c4d7e4a9eaa6a\workspace\results\KIC_6881353';" ^
 "if (Test-Path $src) { New-Item -ItemType Directory -Force 'results\seguimiento' | Out-Null; Copy-Item $src 'results\seguimiento' -Recurse -Force; Write-Host 'KIC 6881353 guardada en results\seguimiento' }" ^
 "$w=Join-Path $p 'watchlist.json'; $j=@{}; if (Test-Path $w) { (Get-Content $w -Raw | ConvertFrom-Json).psobject.Properties | ForEach-Object { $j[$_.Name]=$_.Value } };" ^
 "if (-not $j.ContainsKey('6881353')) { $j['6881353']=@{added=[double][DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); note='Candidata ~1.2 R_tierra, SNR 9.7 (estudio anterior)'} };" ^
 "[IO.File]::WriteAllText((Resolve-Path $p).Path + '\watchlist.json', ($j | ConvertTo-Json -Depth 4));" ^
 "$n=0; Get-ChildItem $p -Directory | Where-Object { $_.Name -match '^[0-9a-f]{16}$' } | ForEach-Object { Remove-Item $_.FullName -Recurse -Force; $n++ }; Write-Host \"Estudios borrados: $n\";" ^
 "Get-ChildItem $p -File | Where-Object { $_.Name -like 'discovery_automation*' -or $_.Name -in 'serious_evidence.json','evidence_backfill.json' } | Remove-Item -Force;" ^
 "foreach ($m in 'data\discovery_batches\manifest.csv','data\tess_discovery_batches\manifest.csv') { if (Test-Path $m) { $rows=Import-Csv $m; foreach ($r in $rows) { $r.status='pending'; if ($r.psobject.Properties['completed_stars']) { $r.completed_stars=0 } }; $rows | Export-Csv $m -NoTypeInformation -Encoding ASCII; Write-Host \"Lotes reiniciados: $m\" } }"
if errorlevel 1 (echo. & echo ERROR durante el reinicio. & pause & exit /b 1)

echo.
echo Listo. Abre ABRIR_PANEL_RED.bat y pulsa Automatizar.
pause
