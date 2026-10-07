# VHS recording for docs/demo.gif.
#
# Requires github.com/charmbracelet/vhs:
#   vhs demo.tape
#
# Demonstrates: --version, a simple diff, a breaking change (exit 3) and --json.
Output docs/demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1000
Set Height 640
Set TypingSpeed 40ms

Type "./app --version"
Enter
Sleep 1s

Type "cd examples && ../app diff migration_old.sql migration_new.sql"
Enter
Sleep 2s

Type "../app diff breaking_old.sql breaking_new.sql; echo exit=$?"
Enter
Sleep 2s

Type "../app diff ../tests/fixtures/ac13_old.sql ../tests/fixtures/ac13_new.sql --json"
Enter
Sleep 3s
