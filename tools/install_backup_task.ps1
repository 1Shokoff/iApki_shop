# Регистрирует в Планировщике Windows задачу «iApki backup»: каждые 6 часов
# и при входе в систему забирать копии базы с сервера (tools\pull_backups.py).
#
#   powershell -ExecutionPolicy Bypass -File tools\install_backup_task.ps1
#   powershell -ExecutionPolicy Bypass -File tools\install_backup_task.ps1 -Dir "E:\Копии\iApki"
#
# Пропущенный запуск (ПК был выключен) выполнится, как только ПК включится.
# Запустить сейчас: Start-ScheduledTask -TaskName "iApki backup"
# Удалить задачу:   Unregister-ScheduledTask -TaskName "iApki backup"
param(
    [string]$Dir = "D:\Backups\iApki"
)

$ErrorActionPreference = "Stop"
# Имя латиницей и без тире: его набирают руками, а «—» легко спутать с «-».
$name = "iApki backup"
# Прежнее имя задачи — убираем, чтобы копии не забирались дважды.
Get-ScheduledTask -TaskName "iApki — копии базы" -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false
$root = Split-Path -Parent $PSScriptRoot
# pythonw — без окна консоли, которое мигало бы раз в 6 часов.
$python = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $python)) {
    throw "Нет $python — сначала создайте .venv (README, «Запуск без Docker»)"
}
$script = Join-Path $root "tools\pull_backups.py"

$action = New-ScheduledTaskAction -Execute $python -Argument ('"{0}" --dir "{1}"' -f $script, $Dir) -WorkingDirectory $root
$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(47) `
        -RepetitionInterval (New-TimeSpan -Hours 6) -RepetitionDuration (New-TimeSpan -Days 3650))
)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Settings $settings `
    -Description "Забирает зашифрованные копии базы iApki с сервера в $Dir" -Force | Out-Null

Write-Host "Задача «$name» зарегистрирована: каждые 6 часов и при входе в систему."
Write-Host "Копии: $Dir, журнал: $Dir\pull.log"
Write-Host "Запустить сейчас: Start-ScheduledTask -TaskName `"$name`""
