$key = $env:LAWTECH_API_KEY
if (-not $key) { throw "LAWTECH_API_KEY env var must be set" }
$prompt = @'
A hacker gains unauthorized access to a fintech company's servers.

He steals:

Aadhaar records PAN data Bank account details Credit card information Source code

The data is sold on the dark web.

Prepare:

Identify every applicable offence under BNS. Discuss provisions of the Information Technology Act. Explain investigation procedure. Digital evidence collection. Preservation of electronic evidence. Role of cyber forensic laboratory. Admissibility of electronic evidence under Bharatiya Sakshya Adhiniyam. Draft FIR. Draft seizure memo. Draft arrest memo. Draft charge sheet. Relevant Supreme Court judgments.
'@
$body = @{ prompt_query = $prompt } | ConvertTo-Json -Compress
Write-Output "Firing at prod POST-REVERT ..."
$sw = [System.Diagnostics.Stopwatch]::StartNew()
try {
    $r = Invoke-RestMethod -Uri "https://api.lawttorney.com/pyapi/search" `
        -Headers @{"X-API-Key"=$key; "Content-Type"="application/json"} `
        -Method POST -Body $body -TimeoutSec 300 -UseBasicParsing
    $sw.Stop()
    $r | ConvertTo-Json -Depth 10 | Out-File "_verify_prod_post_revert_response.json" -Encoding utf8
    Write-Output "OK in $($sw.Elapsed.TotalSeconds)s"
    Write-Output "agents_used   : $($r.agents_used -join ', ')"
    Write-Output "result length : $($r.result.Length) chars"
    Write-Output "source count  : $($r.source.Count)"
    if ($r.agents_used -contains 'Drafting') {
        Write-Output "FAIL: Drafting is in agents_used - revert did NOT take"
    } else {
        Write-Output "PASS: Drafting NOT in agents_used - Phase 2 successfully reverted"
    }
} catch {
    $sw.Stop()
    Write-Output "FAIL after $($sw.Elapsed.TotalSeconds)s: $($_.Exception.Message)"
}
