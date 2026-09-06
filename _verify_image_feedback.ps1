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
$outFile = "_verify_image_feedback_response.json"
$hdrFile = "_verify_image_feedback_headers.txt"

Write-Output "Firing at https://api.lawttorney.com/pyapi/search ..."
$sw = [System.Diagnostics.Stopwatch]::StartNew()
try {
    $r = Invoke-WebRequest -Uri "https://api.lawttorney.com/pyapi/search" `
        -Headers @{"X-API-Key"=$key; "Content-Type"="application/json"} `
        -Method POST -Body $body -TimeoutSec 600 -UseBasicParsing
    $sw.Stop()
    $r.Content | Out-File $outFile -Encoding utf8
    $r.Headers.GetEnumerator() | ForEach-Object { "$($_.Key): $($_.Value)" } | Out-File $hdrFile -Encoding utf8
    Write-Output "OK. HTTP $($r.StatusCode) in $($sw.Elapsed.TotalSeconds)s"
    Write-Output "Response bytes: $($r.RawContentLength)"
    Write-Output "Content-Type: $($r.Headers['Content-Type'])"
    Write-Output "Saved to: $outFile"
} catch {
    $sw.Stop()
    Write-Output "FAIL after $($sw.Elapsed.TotalSeconds)s"
    Write-Output $_.Exception.Message
    if ($_.Exception.Response) {
        Write-Output "Status: $($_.Exception.Response.StatusCode.value__)"
    }
}
