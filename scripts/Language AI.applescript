on backendHealthy()
  try
    do shell script "/usr/bin/curl --fail --silent --max-time 1 http://127.0.0.1:8000/api/health >/dev/null"
    return true
  on error
    return false
  end try
end backendHealthy

on run
  if not backendHealthy() then
    set userID to do shell script "/usr/bin/id -u"
    set serviceDomain to "gui/" & userID
    set serviceTarget to serviceDomain & "/com.language-ai-platform"
    set plistPath to POSIX path of (path to home folder) & "Library/LaunchAgents/com.language-ai-platform.plist"

    try
      do shell script "/bin/launchctl print " & quoted form of serviceTarget & " >/dev/null 2>&1"
    on error
      try
        do shell script "/bin/launchctl bootstrap " & quoted form of serviceDomain & " " & quoted form of plistPath
      end try
    end try
    try
      do shell script "/bin/launchctl kickstart -k " & quoted form of serviceTarget
    end try
  end if

  repeat 30 times
    if backendHealthy() then
      open location "http://127.0.0.1:8000"
      return
    end if
    delay 1
  end repeat

  display dialog "Language AI could not start. Check ~/Library/Logs/LanguageAIPlatform/backend.stderr.log for details." buttons {"OK"} default button "OK" with icon stop
end run
