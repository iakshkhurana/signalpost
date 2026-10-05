# One command to run the agent on a list of Norwegian organisation numbers (Windows).
#   .\run.ps1 companies.txt            -> profiles\<orgnr>.json + runs\<timestamp>.json
#   .\run.ps1 923609016 878703812      -> same, for numbers given inline
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
Set-Location $PSScriptRoot
python -m pip install -q -r requirements.txt
if ($Args.Count -ge 1 -and (Test-Path $Args[0])) {
    python -m signalpost run --input $Args[0] @($Args | Select-Object -Skip 1)
} else {
    python -m signalpost run @Args
}
exit $LASTEXITCODE
