Get-CimInstance Win32_Process -Filter "Name='electron.exe'" | ForEach-Object {
  $cl = $_.CommandLine
  if ($null -eq $cl) { $cl = '(no cmdline visible)' }
  if ($cl.Length -gt 160) { $cl = $cl.Substring(0,160) }
  "{0}`t{1}" -f $_.ProcessId, $cl
}
