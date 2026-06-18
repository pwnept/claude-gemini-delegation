# Delegation wrapper script
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
python3 "$ScriptDir/pre_delegate.py" $args
