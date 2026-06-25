# Repository Instructions

- Run `python -m unittest discover -s tests -v` after changing Python or hook code.
- Run `$files = @(Get-ChildItem src/gemini_delegation -Filter *.py) + @(Get-ChildItem hooks -Filter *.py); python -m py_compile @($files.FullName)` before handing off installer changes.
